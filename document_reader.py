"""Local document reading. No model calls, fixtures, network requests or Gradio imports.

read_document() returns a serializable result; apply_page_edits() is the future
model handoff. The UI runs this module in a disposable subprocess so cancelling
also interrupts native PDF parsing and rendering safely.
"""
import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from PIL import Image, ImageOps, UnidentifiedImageError
import pypdfium2 as pdfium
from office_reader import OFFICE_EXTENSIONS, OFFICE_MIME, OfficeError, read_office


def _limit(name, default):
    try:
        value = int(os.environ.get(name.replace('NOTICEBRIDGE_', 'DEADLENSE_'), os.environ.get(name, default)))
        return value if value > 0 else default
    except ValueError:
        return default


MAX_BYTES = _limit('NOTICEBRIDGE_MAX_FILE_MB', 20) * 1024 * 1024
MAX_PAGES = _limit('NOTICEBRIDGE_MAX_PAGES', 30)
MAX_PIXELS = _limit('NOTICEBRIDGE_MAX_IMAGE_PIXELS', 25_000_000)
MAX_TEXT = _limit('NOTICEBRIDGE_MAX_TEXT_CHARS', 500_000)
OCR_TIMEOUT = _limit('NOTICEBRIDGE_OCR_TIMEOUT', 60)
READ_TIMEOUT = _limit('NOTICEBRIDGE_READ_TIMEOUT', 180)
EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff', '.gif', '.txt', '.docx', '.pptx', '.ppt']
LANGUAGES = {'English': 'eng', 'Hindi + English': 'hin+eng', 'Kannada + English': 'kan+eng'}
MIME = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.bmp': 'image/bmp',
        '.tif': 'image/tiff', '.tiff': 'image/tiff', '.gif': 'image/gif'}
MIME.update(OFFICE_MIME)
MIME['.txt'] = 'text/plain'


class DocumentError(ValueError):
    pass


def file_info(path):
    if not path:
        raise DocumentError('Select a PDF, image, TXT, DOCX, PPTX or PPT first.')
    path = Path(path)
    if not path.is_file():
        raise DocumentError('The selected file is no longer available. Upload it again.')
    suffix = path.suffix.lower()
    if suffix not in EXTENSIONS:
        raise DocumentError('Unsupported format. Use PDF, PNG, JPG, WEBP, BMP, TIFF, GIF, TXT, DOCX, PPTX or PPT.')
    size = path.stat().st_size
    if size == 0:
        raise DocumentError('The selected file is empty (0 bytes). Upload a complete file.')
    if size > MAX_BYTES:
        raise DocumentError(f'File exceeds the {MAX_BYTES // (1024 * 1024)} MB limit. Compress or split it.')
    return {'fileName': path.name, 'fileType': MIME[suffix], 'fileSize': size}


def tesseract_path():
    candidates = [os.environ.get('TESSERACT_CMD'), shutil.which('tesseract')]
    if os.name == 'nt':
        candidates += [str(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tesseract-OCR/tesseract.exe'),
                       str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Tesseract-OCR/tesseract.exe')]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise DocumentError('Tesseract OCR is not installed or could not be found. Install it using the README instructions, or set TESSERACT_CMD to tesseract.exe. Text-based PDFs work without OCR.')


def _readable(text):
    # Presence of letters/digits is not a confidence score; always invite review.
    return any(char.isalnum() for char in text)


def _clean(text):
    return ''.join(c for c in text.replace('\r\n', '\n').replace('\r', '\n')
                   if c in '\n\t' or c.isprintable()).strip()


def _ocr(image, workdir, number, language, progress):
    executable = tesseract_path()
    image_path = workdir / f'ocr-input-{number}.png'
    output_base = workdir / f'ocr-text-{number}'
    image.save(image_path)
    progress(f'Running local OCR on page {number} (may take up to {OCR_TIMEOUT}s).', None)
    try:
        run = subprocess.run([executable, str(image_path), str(output_base), '-l', language,
                              '--psm', '3'], capture_output=True, timeout=OCR_TIMEOUT,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired as exc:
        raise DocumentError(f'OCR timed out after {OCR_TIMEOUT}s. Try a smaller, sharper image.') from exc
    if run.returncode:
        details = run.stderr.decode('utf-8', errors='replace')
        if 'traineddata' in details or 'Failed loading language' in details:
            raise DocumentError(f'OCR language data is missing for {language}. Install the matching Tesseract language packs or select English.')
        raise DocumentError('Tesseract could not read this image. Try a clear, upright scan.')
    text_path = output_base.with_suffix('.txt')
    if not text_path.exists():
        raise DocumentError('OCR returned no text file. Try a different image.')
    if text_path.stat().st_size > MAX_TEXT * 4:
        raise DocumentError('OCR output is too large. Split this document into smaller files.')
    return _clean(text_path.read_text(encoding='utf-8-sig'))


def _preview(image, workdir, number):
    preview = image.copy()
    preview.thumbnail((1000, 1000))
    target = workdir / f'preview-{number}.png'
    preview.save(target)
    preview.close()
    return str(target)


def _page(number, text, method, status, preview):
    return {'pageNumber': number, 'originalText': text, 'text': text,
            'extractionMethod': method, 'extractionStatus': status,
            'edited': False, 'previewPath': preview}


def assemble(result):
    pages = result['pages']
    readable = sum(_readable(p['text']) for p in pages)
    result['extractedText'] = '\n\n'.join(f"--- {p.get('sourceRef', 'Page ' + str(p['pageNumber']))} ---\n{p['text']}" for p in pages)
    result['extractionStatus'] = ('success' if readable == len(pages) and readable else
                                  'partial' if readable else
                                  'ocr_required' if any(p['extractionStatus'] == 'ocr_required' for p in pages) else 'no_text')
    methods = set(p['extractionMethod'] for p in pages)
    result['extractionMethod'] = next(iter(methods)) if len(methods) == 1 else 'mixed'
    result['hasReadableText'] = readable > 0
    result['pageCount'] = len(pages)
    return result


def read_document(path, workdir, language='eng', ocr_scans=False, progress=None):
    """Read on the local host. Use a separate process when calling concurrently.

    PDFium is not thread-safe. The Gradio UI provides process isolation, timeout
    and cancellation. The service itself has no dependency on the model module.
    """
    info = file_info(path)
    if language not in LANGUAGES.values():
        raise DocumentError('Choose a supported OCR language.')
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    report = progress or (lambda message, percent: None)
    result = {**info, 'schemaVersion': 1, 'pages': [], 'warnings': [], 'edited': False,
              'modelInferencePerformed': False}
    path = Path(path)
    if path.suffix.lower() in OFFICE_EXTENSIONS:
        report('Reading Office document text locally.', 0.1)
        try:
            content = read_office(path.name, path.read_bytes(), max_bytes=MAX_BYTES,
                                  max_chars=MAX_TEXT, max_slides=MAX_PAGES, temporary_root=workdir)
        except OfficeError as exc:
            raise DocumentError(str(exc)) from exc
        result['warnings'].extend(content.warnings)
        if content.kind == 'docx':
            result['pages'].append(_page(1, content.text, 'docx_text', 'success', None))
            result['pages'][0]['sourceRef'] = 'Word document · paragraph/table references'
        else:
            for part in content.parts:
                status = 'success' if _readable(part.text) else 'no_text'
                page = _page(part.number, part.text, 'ppt_conversion_text' if content.kind == 'ppt' else 'pptx_text', status, None)
                page['sourceRef'] = part.reference
                result['pages'].append(page)
    elif path.suffix.lower() == '.txt':
        data = path.read_bytes()
        for encoding in ('utf-8-sig', 'utf-16', 'latin-1'):
            try:
                text = _clean(data.decode(encoding))
                break
            except UnicodeDecodeError:
                continue
        if not _readable(text):
            raise DocumentError('The text file contains no readable text.')
        if len(text) > MAX_TEXT:
            raise DocumentError('Text exceeds the character limit. Split the file.')
        result['pages'].append(_page(1, text, 'plain_text', 'success', None))
    elif path.suffix.lower() == '.pdf':
        with path.open('rb') as stream:
            if b'%PDF-' not in stream.read(1024):
                raise DocumentError('This file is not a valid PDF. Re-export it from the original source.')
        try:
            document = pdfium.PdfDocument(str(path))
        except pdfium.PdfiumError as exc:
            if getattr(exc, 'err_code', None) == 4 or 'password' in str(exc).lower():
                raise DocumentError('This PDF is password-protected. Upload an unlocked copy you are authorized to read.') from exc
            raise DocumentError('The PDF is corrupted or unsupported. Try exporting a fresh copy.') from exc
        try:
            count = len(document)
            if not count:
                raise DocumentError('The PDF contains no pages.')
            if count > MAX_PAGES:
                raise DocumentError(f'PDF has {count} pages; the limit is {MAX_PAGES}. Split it into smaller documents.')
            total = 0
            for index in range(count):
                report(f'Reading PDF page {index + 1} of {count}.', index / count)
                page = document[index]
                try:
                    text_page = page.get_textpage()
                    try:
                        if text_page.count_chars() + total > MAX_TEXT:
                            raise DocumentError('Document text exceeds the configured limit. Split the PDF.')
                        text = _clean(text_page.get_text_bounded())
                    finally:
                        text_page.close()
                    width, height = page.get_size()
                    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
                        raise DocumentError(f'PDF page {index + 1} has invalid dimensions.')
                    scale = min(2.5 if ocr_scans and not _readable(text) else 1.5,
                                2500 / max(width, height), math.sqrt(MAX_PIXELS / (width * height)))
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil().convert('RGB')
                    finally:
                        bitmap.close()
                    try:
                        preview = _preview(image, workdir, index + 1)
                        method, status = 'pdf_text', 'success'
                        if not _readable(text):
                            text = ''
                            status = 'ocr_required'
                            if ocr_scans:
                                method = 'image_ocr'
                                try:
                                    text = _ocr(image, workdir, index + 1, language, report)
                                    status = 'success' if _readable(text) else 'no_text'
                                except DocumentError as exc:
                                    result['warnings'].append(f'Page {index + 1}: {exc}')
                                    status = 'ocr_required'
                            if status != 'success':
                                result['warnings'].append(f'Page {index + 1}: no readable text obtained. This page may be blank or scanned; review the preview and try OCR if text is visible.')
                        result['pages'].append(_page(index + 1, text, method, status, preview))
                        total += len(text)
                        if total > MAX_TEXT:
                            raise DocumentError('Document text exceeds the configured limit. Split the PDF.')
                    finally:
                        image.close()
                finally:
                    page.close()
        finally:
            document.close()
    else:
        report('Validating image and preparing preview.', 0.1)
        try:
            with Image.open(path) as source:
                if source.width * source.height > MAX_PIXELS:
                    raise DocumentError(f'Image exceeds the {MAX_PIXELS:,}-pixel limit. Resize it before uploading.')
                if getattr(source, 'n_frames', 1) > 1:
                    result['warnings'].append('Only the first frame of this GIF/TIFF is processed. Upload separate images or a PDF for additional pages.')
                image = ImageOps.exif_transpose(source).convert('RGB')
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise DocumentError('This image is corrupted, unsupported, or too large. Try exporting a PNG/JPG.') from exc
        try:
            preview = _preview(image, workdir, 1)
            report('Image ready. Starting local OCR.', 0.25)
            text = _ocr(image, workdir, 1, language, report)
        finally:
            image.close()
        status = 'success' if _readable(text) else 'no_text'
        if not _readable(text):
            text = ''
            result['warnings'].append('OCR found no readable text. Try a sharper, upright image with larger printed text.')
        result['pages'].append(_page(1, text, 'image_ocr', status, preview))
    report('Finished reading. Review the extracted text.', 1.0)
    return assemble(result)


def apply_page_edits(result, texts):
    """Produce the model-ready payload without claiming a model has run.

    Original extraction status/text are retained. Manual corrections never
    rewrite OCR failure into successful machine extraction.
    """
    if not result or len(texts) != len(result.get('pages', [])):
        raise DocumentError('Read a document before saving page edits.')
    if sum(len(str(text)) for text in texts) > MAX_TEXT:
        raise DocumentError('Edited text exceeds the configured limit.')
    output = deepcopy(result)
    for page, text in zip(output['pages'], texts):
        page['text'] = str(text)
        page['edited'] = page['text'] != page['originalText']
        page.pop('previewPath', None)
    output['edited'] = any(p['edited'] for p in output['pages'])
    output['extractedText'] = '\n\n'.join(f"--- {p.get('sourceRef', 'Page ' + str(p['pageNumber']))} ---\n{p['text']}" for p in output['pages'])
    output['hasReadableText'] = any(_readable(p['text']) for p in output['pages'])
    output['reviewStatus'] = 'user_edited' if output['edited'] else 'not_edited'
    output['modelInferencePerformed'] = False
    return output


def _worker():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--language', default='eng')
    parser.add_argument('--ocr-scans', action='store_true')
    args = parser.parse_args()

    def emit(event):
        print(json.dumps(event, ensure_ascii=True), flush=True)

    try:
        result = read_document(args.worker, args.output, args.language, args.ocr_scans,
                               lambda message, percent: emit({'type': 'progress', 'message': message, 'progress': percent}))
        emit({'type': 'result', 'result': result})
    except Exception as exc:
        message = str(exc) if isinstance(exc, DocumentError) else 'Document reading failed. The file may be damaged; try a fresh PDF or PNG/JPG.'
        emit({'type': 'error', 'message': message})
        sys.exit(1)


if __name__ == '__main__':
    _worker()
