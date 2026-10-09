"""Independent Gradio document-reader panel; never invokes model.py."""
from copy import deepcopy
import json
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import signal
import subprocess
import sys
from threading import Lock, Thread
import time
from uuid import uuid4

import gradio as gr
from PIL import Image
from document_reader import (DocumentError, EXTENSIONS, LANGUAGES, MAX_BYTES,
                             MAX_PIXELS, READ_TIMEOUT, apply_page_edits, file_info)

BASE = Path(__file__).resolve().parent


class ReaderJob:
    def __init__(self, path, language, scans):
        self.folder = BASE / 'outputs' / 'document-reader' / uuid4().hex
        self.folder.mkdir(parents=True)
        self.events = Queue()
        self.stopped = False
        self.started = time.monotonic()
        command = [sys.executable, str(BASE / 'document_reader.py'), '--worker', str(path),
                   '--output', str(self.folder), '--language', LANGUAGES[language]]
        if scans:
            command.append('--ocr-scans')
        options = {'start_new_session': True} if os.name != 'nt' else {
            'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
        with (self.folder / 'worker-errors.log').open('wb') as errors:
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors,
                                            text=True, encoding='utf-8', **options)
        Thread(target=self._collect, daemon=True).start()

    def _collect(self):
        try:
            for line in self.process.stdout:
                try:
                    self.events.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
        finally:
            self.process.stdout.close()
            self.process.wait()
            self.events.put({'type': 'exit'})

    def stop(self):
        self.stopped = True
        if self.process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.process.wait(timeout=10)

    def cleanup(self):
        self.stop()
        shutil.rmtree(self.folder, ignore_errors=True)


class ReaderSession:
    """One job per browser session. Replaced results are never published."""
    def __init__(self):
        self.lock = Lock()
        self.job = None
        self.generation = 0
        self.completed_jobs = []

    def __deepcopy__(self, memo):
        return ReaderSession()

    def cancel(self, clear_results=True):
        with self.lock:
            self.generation += 1
            job, self.job = self.job, None
            completed = self.completed_jobs if clear_results else []
            if clear_results:
                self.completed_jobs = []
        if job:
            job.cleanup()
        for old in completed:
            old.cleanup()

    def begin(self, path, language, scans):
        self.cancel(clear_results=False)
        with self.lock:
            self.job = ReaderJob(path, language, scans)
            return self.job, self.generation

    def complete(self, job, generation):
        with self.lock:
            if self.job is not job or self.generation != generation:
                return False
            old, self.completed_jobs = self.completed_jobs, [job]
            self.job = None
        for previous in old:
            previous.cleanup()
        return True

    def current(self, job, generation):
        with self.lock:
            return self.job is job and self.generation == generation and not job.stopped


def _clear(status='Select a PDF, image, TXT, DOCX, PPTX or PPT to start.'):
    return {}, '', [], gr.Dropdown(choices=[], value=None), '', '', None, None, status


def reset_reader(session):
    session.cancel()
    return None, *_clear('Document workflow reset. Choose a new document.')


def select_document(path, session, previous=None):
    session.cancel(clear_results=False)
    if not path:
        return (gr.skip(),) * 8 + (('No file selected. Saved results remain available; use New document / reset to clear them.' if previous else 'Select a document to begin.'),)
    try:
        info = file_info(path)
        metadata = f"{info['fileName']} | {info['fileType']} | {info['fileSize'] / 1024:.1f} KB"
        preview = []
        if info['fileType'].startswith('image/'):
            with Image.open(path) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise DocumentError('Image dimensions exceed the configured limit. Resize it first.')
                image.verify()
            preview = [(str(path), 'Selected image')]
        saved = bool(previous)
        return gr.skip(), metadata, gr.skip() if saved else preview, gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), 'File selected. Click Read document for AI analysis or Extract text for local reading. Previous successful text and edits remain saved until extraction succeeds.'
    except Exception as exc:
        message = str(exc) if isinstance(exc, DocumentError) else 'Cannot preview this image. It may be damaged; upload a fresh PNG/JPG.'
        return (gr.skip(),) * 8 + ('Upload error: ' + message + '. Previous successful results are unchanged.',)


def process_document(path, language, scans, session, previous=None):
    # Gradio consumes this generator without blocking stop/replacement events.
    try:
        info = file_info(path)
        job, generation = session.begin(path, language, scans)
    except Exception as exc:
        yield (gr.skip(),) * 8 + (f'Could not start: {exc}. Previous successful text and edits are unchanged.',)
        return
    metadata = f"{info['fileName']} | {info['fileType']} | {info['fileSize'] / 1024:.1f} KB"
    yield (gr.skip(),) * 8 + ('Reading locally… Previous successful text and edits remain available.',)
    finished = False
    try:
        while session.current(job, generation):
            if time.monotonic() - job.started > READ_TIMEOUT:
                job.stop()
                yield (gr.skip(),) * 8 + (f'Reading timed out after {READ_TIMEOUT}s. Previous results are unchanged. Split or resize the file and retry.',)
                return
            try:
                event = job.events.get(timeout=0.2)
            except Empty:
                continue
            if not session.current(job, generation):
                return
            if event['type'] == 'progress':
                fraction = event.get('progress')
                prefix = f'{round(fraction * 100)}% · ' if fraction is not None else ''
                yield (gr.skip(),) * 8 + (prefix + event['message'],)
            elif event['type'] == 'result':
                result = event['result']
                if previous and not result.get('hasReadableText'):
                    yield (gr.skip(),) * 8 + ('No readable text obtained from the new file. Previous successful text and edits are unchanged. Enable OCR or try another file.',)
                    return
                payload = apply_page_edits(result, [p['text'] for p in result['pages']])
                previews = [(p['previewPath'], p.get('sourceRef', f"Page {p['pageNumber']}")) for p in result['pages'] if p.get('previewPath')]
                choices = [str(p['pageNumber']) for p in result['pages']]
                status = f"{result['extractionStatus'].upper()} · {result['extractionMethod']} · {result['pageCount']} page(s). Review text before reuse. No model inference performed."
                if result['warnings']:
                    status += '\n' + '\n'.join(result['warnings'])
                if not session.complete(job, generation):
                    return
                finished = True
                yield result, metadata, previews, gr.Dropdown(choices=choices, value='1'), result['pages'][0]['text'], payload['extractedText'], payload, None, status
                return
            elif event['type'] in {'error', 'exit'}:
                message = event.get('message', 'Reader stopped unexpectedly. Try a smaller file or reinstall the reader dependencies.')
                yield (gr.skip(),) * 8 + ('Reading error: ' + message + '. Previous successful text and edits are unchanged.',)
                return
    finally:
        if not finished:
            job.stop()


def load_page(result, page):
    if not result or not page:
        return ''
    return result['pages'][int(page) - 1]['text']


def save_page(result, page, text):
    if not result or not page:
        raise gr.Error('Read a document first.')
    updated = deepcopy(result)
    index = int(page) - 1
    texts = [p['text'] for p in result['pages']]
    texts[index] = text
    try:
        payload = apply_page_edits(result, texts)
    except DocumentError as exc:
        raise gr.Error(str(exc)) from exc
    for target, edited in zip(updated['pages'], payload['pages']):
        target.update(edited)
    updated.update({k: v for k, v in payload.items() if k != 'pages'})
    return updated, payload['extractedText'], payload, None, 'Page corrections saved. Original extraction text/status retained; no model inference performed.'


def download_result(result):
    if not result:
        raise gr.Error('Read a document first.')
    payload = apply_page_edits(result, [p['text'] for p in result['pages']])
    folder = BASE / 'outputs' / 'document-reader-exports' / uuid4().hex
    folder.mkdir(parents=True)
    target = folder / 'document-text.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(target)


def reader_availability(result):
    """Reflect actual extraction state without implying that review is complete."""
    available = bool(result)
    summary = ''
    if available:
        statuses = {'success': 'Text extracted — review it before use',
                    'partial': 'Some pages need attention — check the warnings',
                    'ocr_required': 'No selectable text — enable scanned-page OCR on Upload or enter text on Review',
                    'no_text': 'No readable text found — check the preview or enter text on Review'}
        summary = (f"{result['fileName']}\n{result['pageCount']} page(s) · "
                   f"{statuses.get(result['extractionStatus'], result['extractionStatus'])}")
        if result.get('warnings'):
            summary += '\n\nWarnings:\n' + '\n'.join(result['warnings'])
    return (gr.Markdown(visible=not available), gr.Markdown(visible=not available),
            *[gr.Button(interactive=available) for _ in range(3)],
            gr.Textbox(value=summary, visible=available))


def build_document_reader(upload_tab, review_tab, results_tab, navigation, source, workspace):
    """Render the real-document source into the shared three-stage workspace."""
    from ui_style import stage_intro
    session = gr.State(value=ReaderSession, time_to_live=3600,
                       delete_callback=lambda value: value.cancel())
    result = gr.State({})
    with upload_tab:
        with gr.Group() as upload_panel:
            stage_intro('Upload and preview your document', 'Choose a document, then click Read document. Images preview immediately; Reading options provides PDF previews and editable Office text in document order.')
            with gr.Row(equal_height=True):
                with gr.Column(scale=4, min_width=280, elem_classes=['surface']) as upload_controls:
                    upload = gr.File(label=f'Choose or drop a document · {MAX_BYTES // (1024 * 1024)} MB max',
                                     file_types=EXTENSIONS, type='filepath', height=180)
                    gr.Markdown('PDF · TXT · Word DOCX · PowerPoint PPTX/PPT · PNG, JPG/JPEG, WEBP, BMP, TIFF, GIF. Legacy PPT requires local LibreOffice.', elem_classes=['quiet-note'])
                    metadata = gr.Textbox(label='Selected file', placeholder='No file selected yet.', interactive=False)
                    with gr.Accordion('Reading options', open=False):
                        language = gr.Dropdown(list(LANGUAGES), value='English', label='OCR language')
                        scans = gr.Checkbox(label='OCR PDF pages without selectable text', value=False)
                        gr.Markdown('Image OCR needs local Tesseract and the selected language packs. Text PDFs work without OCR.', elem_classes=['quiet-note'])
                        run = gr.Button('Extract text →')
                        with gr.Row():
                            stop = gr.Button('Cancel reading', size='sm')
                            remove = gr.Button('New document / reset', size='sm')
                with gr.Column(scale=6, min_width=280, elem_classes=['surface']):
                    gr.Markdown('### Document preview')
                    previews = gr.Gallery(label='Pages in document order', columns=2, height=400, interactive=False)
                    gr.Markdown('Select an image for an immediate preview or extract a PDF to preview its pages. For DOCX, PPTX, PPT and TXT, use the editable text preview on Review; slide and paragraph references stay in order.', elem_classes=['quiet-note'])
    with review_tab:
        with gr.Group() as review_panel:
            with gr.Accordion('Page text and OCR corrections', open=False):
                stage_intro('Review the extracted text', 'Compare each page with the preview on Upload. Edits stay in this session when you change pages or views. Save or continue when ready.')
                review_empty = gr.Markdown('**No extracted text yet.** Go to Upload, select a document, and click **Extract text**. Then review each page here.', elem_classes=['empty-state'])
                with gr.Row():
                    with gr.Column(scale=6, min_width=280, elem_classes=['surface']):
                        page = gr.Dropdown(choices=[], label='Page to review', interactive=True)
                        editor = gr.Textbox(label='Editable page text', lines=14, interactive=True,
                                            placeholder='Extract a document on Upload to see its page text here.', elem_classes=['document-text'])
                        save = gr.Button('Save page corrections', interactive=False)
                        gr.Markdown('Save before switching pages. Continue also saves the current page.', elem_classes=['quiet-note'])
                    with gr.Column(scale=4, min_width=260, elem_classes=['surface']):
                        gr.Markdown('### All pages at a glance')
                        combined = gr.Textbox(label='Saved document text', lines=14, interactive=False,
                                              placeholder='Text from all pages appears after extraction. Saved corrections appear here too.', elem_classes=['document-text'])
                        gr.Markdown('Page boundaries stay intact. Empty pages are flagged rather than filled with guessed text.', elem_classes=['quiet-note'])
                with gr.Row():
                    back = gr.Button('← Back to upload')
                    ready = gr.Button('Save page & view results →', variant='primary', interactive=False)
    with results_tab:
        with gr.Group() as results_panel:
            with gr.Accordion('Extracted text and JSON download', open=False):
                stage_intro('Document text and downloads', 'Results come from your uploaded file. Check the text before using it; the confirmed Gemma action plan appears below after fact review.')
                results_empty = gr.Markdown('**No document results yet.** Extract a document on Upload, then check its text on Review. Your saved text and JSON download will appear here.', elem_classes=['empty-state'])
                result_summary = gr.Textbox(label='Extraction summary · uploaded document', interactive=False,
                                            lines=3, visible=False)
                with gr.Row():
                    with gr.Column(scale=6, min_width=280, elem_classes=['surface']):
                        gr.Markdown('### Document text')
                        result_text = gr.Textbox(label='Combined text · includes saved corrections', lines=12, interactive=False,
                                                placeholder='No extracted text yet. Start on Upload, then review and save any corrections.', elem_classes=['document-text'])
                        with gr.Accordion('Technical details · JSON and original text', open=False):
                            payload = gr.JSON(label='Document reading result')
                    with gr.Column(scale=4, min_width=260, elem_classes=['surface']):
                        gr.Markdown('### Take your text with you')
                        gr.Markdown('Create a JSON file containing the saved text, original extraction, page order and reading status. Save any page corrections on Review first.', elem_classes=['quiet-note'])
                        export = gr.Button('Prepare JSON download', variant='primary', interactive=False)
                        download = gr.File(label='Document JSON', interactive=False)
                        edit_again = gr.Button('← Return to review')
    with gr.Group() as status_panel:
        status = gr.Textbox(label='Document reading status', value='Step 1: choose a document on Upload, then click Read document or Extract text.', lines=2, interactive=False, elem_classes=['workflow-status'])
    outputs = [result, metadata, previews, page, editor, combined, payload, download, status]
    read_event = run.click(process_document, [upload, language, scans, session, result], outputs,
                           concurrency_limit=2, trigger_mode='once')
    def reader_stage(data, current, message, memory):
        if not data or not message.startswith(('SUCCESS', 'PARTIAL', 'OCR_REQUIRED', 'NO_TEXT')):
            return gr.skip()
        memory.remember_stage('Your document', 'review')
        return gr.Tabs(selected='review') if memory.showing('Your document') else gr.skip()
    read_event.then(reader_stage, [result, source, status, workspace], navigation)
    upload.change(select_document, [upload, session, result], outputs, queue=False, cancels=[read_event])

    def cancel(session):
        session.cancel(clear_results=False)
        return (gr.skip(),) * 8 + ('Reading cancelled. Previous successful text and edits remain saved.',)

    stop.click(cancel, session, outputs, queue=False, cancels=[read_event])
    remove.click(reset_reader, session, [upload, *outputs], queue=False, cancels=[read_event])
    page.input(load_page, [result, page], editor, concurrency_id='reader-edits', concurrency_limit=1, trigger_mode='multiple')
    save.click(save_page, [result, page, editor], [result, combined, payload, download, status], concurrency_id='reader-edits', concurrency_limit=1)
    editor.input(save_page, [result, page, editor], [result, combined, payload, download, status], concurrency_id='reader-edits', concurrency_limit=1, trigger_mode='multiple')
    export.click(download_result, result, download)
    combined.change(lambda text: text, combined, result_text, queue=False)
    ready.click(save_page, [result, page, editor], [result, combined, payload, download, status], concurrency_id='reader-edits', concurrency_limit=1).success(
        lambda memory: gr.Tabs(selected=memory.remember_stage('Your document', 'results')) if memory.showing('Your document') else gr.skip(), workspace, navigation)
    back.click(lambda memory: gr.Tabs(selected=memory.remember_stage('Your document', 'upload')), workspace, navigation, queue=False)
    edit_again.click(lambda memory: gr.Tabs(selected=memory.remember_stage('Your document', 'review')), workspace, navigation, queue=False)
    result.change(reader_availability, result,
                  [review_empty, results_empty, save, ready, export, result_summary], queue=False)
    return {'result': result, 'panels': [upload_panel, review_panel, results_panel, status_panel],
            'session': session, 'read_event': read_event, 'upload': upload, 'combined': combined, 'editor': editor, 'upload_controls': upload_controls, 'reset': remove}
