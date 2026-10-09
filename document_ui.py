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

    def __deepcopy__(self, memo):
        return ReaderSession()

    def cancel(self):
        with self.lock:
            self.generation += 1
            job, self.job = self.job, None
        if job:
            job.cleanup()

    def begin(self, path, language, scans):
        self.cancel()
        with self.lock:
            self.job = ReaderJob(path, language, scans)
            return self.job, self.generation

    def current(self, job, generation):
        with self.lock:
            return self.job is job and self.generation == generation and not job.stopped


def _clear(status='Select a PDF or notice image to start.'):
    return {}, '', [], gr.Dropdown(choices=[], value=None), '', '', None, None, status


def select_document(path, session):
    session.cancel()
    if not path:
        return _clear()
    try:
        info = file_info(path)
        metadata = f"{info['fileName']} | {info['fileType']} | {info['fileSize'] / 1024:.1f} KB"
        preview = []
        if info['fileType'] != 'application/pdf':
            with Image.open(path) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise DocumentError('Image dimensions exceed the configured limit. Resize it first.')
                image.verify()
            preview = [(str(path), 'Selected image')]
        return {}, metadata, preview, gr.Dropdown(choices=[], value=None), '', '', None, None, 'Ready. Click Extract readable text. PDF previews appear after processing.'
    except Exception as exc:
        message = str(exc) if isinstance(exc, DocumentError) else 'Cannot preview this image. It may be damaged; upload a fresh PNG/JPG.'
        return _clear('Upload error: ' + message)


def process_document(path, language, scans, session):
    # Gradio consumes this generator without blocking stop/replacement events.
    try:
        info = file_info(path)
        job, generation = session.begin(path, language, scans)
    except Exception as exc:
        yield _clear(f'Could not start: {exc}')
        return
    metadata = f"{info['fileName']} | {info['fileType']} | {info['fileSize'] / 1024:.1f} KB"
    yield {}, metadata, [], gr.Dropdown(choices=[], value=None), '', '', None, None, 'Reading locally…'
    finished = False
    try:
        while session.current(job, generation):
            if time.monotonic() - job.started > READ_TIMEOUT:
                job.stop()
                yield {}, metadata, [], gr.Dropdown(choices=[], value=None), '', '', None, None, f'Reading timed out after {READ_TIMEOUT}s. Split or resize the file and retry.'
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
                payload = apply_page_edits(result, [p['text'] for p in result['pages']])
                previews = [(p['previewPath'], f"Page {p['pageNumber']}") for p in result['pages']]
                choices = [str(p['pageNumber']) for p in result['pages']]
                status = f"{result['extractionStatus'].upper()} · {result['extractionMethod']} · {result['pageCount']} page(s). Review text before reuse. No model inference performed."
                if result['warnings']:
                    status += '\n' + '\n'.join(result['warnings'])
                yield result, metadata, previews, gr.Dropdown(choices=choices, value='1'), result['pages'][0]['text'], payload['extractedText'], payload, None, status
                finished = True
                return
            elif event['type'] in {'error', 'exit'}:
                message = event.get('message', 'Reader stopped unexpectedly. Try a smaller file or reinstall the reader dependencies.')
                yield {}, metadata, [], gr.Dropdown(choices=[], value=None), '', '', None, None, 'Reading error: ' + message
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


def build_document_reader(upload_tab, review_tab, results_tab, navigation, source):
    """Render the real-document source into the shared three-stage workspace."""
    from ui_style import stage_intro
    session = gr.State(value=ReaderSession, time_to_live=3600,
                       delete_callback=lambda value: value.cancel())
    result = gr.State({})
    with upload_tab:
        with gr.Group() as upload_panel:
            stage_intro('Start with your notice', 'Upload a PDF or image. Read its text locally, then check the details at your own pace.')
            with gr.Row(equal_height=True):
                with gr.Column(scale=4, min_width=280, elem_classes=['surface']):
                    upload = gr.File(label=f'Choose or drop a document · {MAX_BYTES // (1024 * 1024)} MB max',
                                     file_types=EXTENSIONS, type='filepath', height=180)
                    metadata = gr.Textbox(label='Selected file', interactive=False)
                    with gr.Accordion('Reading options', open=False):
                        language = gr.Dropdown(list(LANGUAGES), value='English', label='OCR language')
                        scans = gr.Checkbox(label='OCR PDF pages without selectable text', value=False)
                        gr.Markdown('Image OCR needs local Tesseract and the selected language packs. Text PDFs work without OCR.', elem_classes=['quiet-note'])
                    run = gr.Button('Extract text →', variant='primary')
                    with gr.Row():
                        stop = gr.Button('Cancel reading', size='sm')
                        remove = gr.Button('Remove file', size='sm')
                with gr.Column(scale=6, min_width=280, elem_classes=['surface']):
                    gr.Markdown('### Document preview')
                    previews = gr.Gallery(label='Pages in document order', columns=2, height=400, interactive=False)
                    gr.Markdown('Images appear on selection. PDF pages appear after extraction.', elem_classes=['quiet-note'])
    with review_tab:
        with gr.Group() as review_panel:
            stage_intro('Make sure it reads right', 'Review each page and correct anything misread. Your original extracted text is preserved.')
            with gr.Row():
                with gr.Column(scale=6, min_width=280, elem_classes=['surface']):
                    page = gr.Dropdown(choices=[], label='Page', interactive=True)
                    editor = gr.Textbox(label='Editable page text', lines=14, interactive=True,
                                        placeholder='Your extracted text will appear here. Upload a document to get started.')
                    save = gr.Button('Save page corrections', variant='primary')
                    gr.Markdown('Save before switching pages. Continue also saves the current page.', elem_classes=['quiet-note'])
                with gr.Column(scale=4, min_width=260, elem_classes=['surface']):
                    gr.Markdown('### All pages at a glance')
                    combined = gr.Textbox(label='Saved document text', lines=14, interactive=False)
                    gr.Markdown('Page boundaries stay intact. Empty pages are flagged rather than filled with guessed text.', elem_classes=['quiet-note'])
            with gr.Row():
                back = gr.Button('← Back to upload')
                ready = gr.Button('Save & continue →', variant='primary')
    with results_tab:
        with gr.Group() as results_panel:
            stage_intro('Your document, ready for the next step', 'Export the reviewed text for future model processing. No predictions or action plan have been generated from this upload.')
            with gr.Row():
                with gr.Column(scale=6, min_width=280, elem_classes=['surface']):
                    gr.Markdown('### Reviewed document')
                    result_text = gr.Textbox(label='Saved text', lines=12, interactive=False)
                    with gr.Accordion('Structured result & original text', open=False):
                        payload = gr.JSON(label='Document reading result')
                with gr.Column(scale=4, min_width=260, elem_classes=['surface']):
                    gr.Markdown('### Take your text with you')
                    gr.Markdown('The JSON includes your corrections, original text, page order, and extraction status. Model integration is pending.', elem_classes=['quiet-note'])
                    export = gr.Button('Prepare JSON download', variant='primary')
                    download = gr.File(label='Document JSON', interactive=False)
                    edit_again = gr.Button('← Return to review')
    with gr.Group() as status_panel:
        status = gr.Textbox(label='Document reading status', value='Ready when you are. Choose a PDF or image to begin.', lines=2, interactive=False)
    outputs = [result, metadata, previews, page, editor, combined, payload, download, status]
    read_event = run.click(process_document, [upload, language, scans, session], outputs,
                           concurrency_limit=2, trigger_mode='once')
    read_event.then(lambda data, current: gr.Tabs(selected='review') if data and current == 'Your document' else gr.skip(), [result, source], navigation)
    upload.change(select_document, [upload, session], outputs, queue=False, cancels=[read_event])

    def cancel(session):
        session.cancel()
        return _clear('Reading cancelled. Click Extract text to retry.')

    stop.click(cancel, session, outputs, queue=False, cancels=[read_event])
    remove.click(lambda session: (session.cancel(), None)[1], session, upload, queue=False, cancels=[read_event])
    page.input(load_page, [result, page], editor)
    save.click(save_page, [result, page, editor], [result, combined, payload, download, status])
    editor.input(lambda: (None, 'Unsaved page edits. Save before switching pages or exporting.'), outputs=[download, status], queue=False)
    export.click(download_result, result, download)
    combined.change(lambda text: text, combined, result_text, queue=False)
    ready.click(save_page, [result, page, editor], [result, combined, payload, download, status]).success(
        lambda: gr.Tabs(selected='results'), outputs=navigation)
    back.click(lambda: gr.Tabs(selected='upload'), outputs=navigation, queue=False)
    edit_again.click(lambda: gr.Tabs(selected='review'), outputs=navigation, queue=False)
    return {'result': result, 'panels': [upload_panel, review_panel, results_panel, status_panel],
            'session': session, 'read_event': read_event}
