"""Real notice workflow, embedded into the existing document-reader panels."""
from copy import deepcopy
import json
from pathlib import Path
from threading import Lock
from uuid import uuid4

import gradio as gr
from app.schemas import Notice
from app.services.checklist import render_markdown
from backend_client import BackendClient, BackendError
from presentation_utils import plain_markdown

BASE = Path(__file__).resolve().parent
SCALARS = ['title', 'notice_type', 'issuing_body', 'summary', 'audience', 'venue', 'eligibility', 'answer']
LABELS = ['Title', 'Notice type', 'Issuer', 'Plain-language summary', 'Who it affects', 'Venue', 'Eligibility', 'Answer / explanation']
TABLES = {
    'dates': ['label', 'value', 'iso_date', 'iso_end_date', 'source_ref'],
    'fees': ['label', 'amount', 'currency', 'iso_date', 'source_ref'],
    'action_items': ['step', 'detail', 'required', 'source_ref'],
    'contacts': ['name', 'role', 'phone', 'email', 'source_ref'],
    'links': ['label', 'url'],
    'missing': ['field', 'issue', 'note'],
}


class RequestSession:
    def __init__(self):
        self.lock = Lock()
        self.revision = 0
        self.busy = False

    def __deepcopy__(self, memo):
        return RequestSession()

    def invalidate(self):
        with self.lock:
            self.revision += 1

    def begin(self):
        with self.lock:
            if self.busy:
                raise BackendError('A model request is already running. Wait for it to finish before retrying.')
            self.busy = True
            return self.revision

    def current(self, revision):
        with self.lock:
            return self.revision == revision

    def finish(self):
        with self.lock:
            self.busy = False


def blank_row(columns):
    # An editable empty row replaces Gradio's CSV drop zone; it is never a fact.
    return [False if key == 'required' else '' for key in columns]


def table_rows(notice, field, columns):
    return [[row.get(key, '') for key in columns] for row in notice[field]] or [blank_row(columns)]


def add_table_row(rows, columns):
    return list(rows or []) + [blank_row(columns)]


def notice_values(record):
    notice = Notice.model_validate(record['notice']).model_dump()
    return ([notice[k] for k in SCALARS] +
            ['\n'.join(notice[k]) for k in ['required_documents', 'optional_documents']] +
            [table_rows(notice, field, columns) for field, columns in TABLES.items()] +
            [notice['needs_clearer_image']])


def reviewed_notice(record, values):
    notice = deepcopy(record['notice'])
    for key, value in zip(SCALARS, values[:len(SCALARS)]):
        notice[key] = value.strip()
    offset = len(SCALARS)
    for key, value in zip(['required_documents', 'optional_documents'], values[offset:offset+2]):
        notice[key] = [line.strip() for line in value.splitlines() if line.strip()]
    offset += 2
    for (field, columns), rows in zip(TABLES.items(), values[offset:offset+len(TABLES)]):
        notice[field] = []
        for row in rows or []:
            if not any(v is not None and str(v).strip() for v, key in zip(row, columns) if key != 'required'):
                continue
            obj = {}
            for key, value in zip(columns, row):
                if key == 'required':
                    if value not in (True, False):
                        raise BackendError('Required must be checked or unchecked.')
                    obj[key] = bool(value)
                else:
                    obj[key] = '' if value is None else str(value).strip()
            notice[field].append(obj)
    notice['needs_clearer_image'] = bool(values[-1])
    return Notice.model_validate(notice, strict=True).model_dump()


def checklist_choices(record):
    required, optional = [], []
    for index, step in enumerate(record['checklist']['steps'], 1):
        if not (step.get('step', '').strip() or step.get('detail', '').strip()):
            continue
        kind = ('Required' if step['required'] else 'Optional') if step.get('_requirement_known', True) else 'Not classified'
        text = f"{index}. {kind} · {plain_markdown(step['step'])}"
        if step['detail']:
            text += ' — ' + plain_markdown(step['detail'])
        if step['source_ref']:
            text += ' [' + step['source_ref'] + ']'
        (required if step['required'] else optional).append(text)
    return required, optional


def export_bytes(name, data):
    folder = BASE / 'outputs' / 'backend-exports' / uuid4().hex
    folder.mkdir(parents=True)
    path = folder / name
    path.write_bytes(data)
    return str(path)



PRIMARY_LABELS = ['Notice title', 'Category', 'Issuer', 'Main date (YYYY-MM-DD; blank if unknown)',
                  'Original date wording', 'Amount (include currency)', 'Who it applies to',
                  'Actions — one per line', 'Documents needed — one per line', 'Unresolved items']


def main_date_index(notice):
    dates = notice.get('dates', [])
    return next((i for i, d in enumerate(dates) if any(word in d.get('label', '').lower() for word in ('deadline', 'due', 'last date'))), 0) if dates else None


def primary_values(record):
    notice = record.get('notice', {})
    index = main_date_index(notice)
    date = notice['dates'][index] if index is not None else {}
    fee = (notice.get('fees') or [{}])[0]
    amount = ' '.join(str(fee.get(k, '')).strip() for k in ('currency', 'amount') if fee.get(k))
    return [notice.get('title', ''), notice.get('notice_type', ''), notice.get('issuing_body', ''),
            date.get('iso_date', ''), date.get('value', ''), amount, notice.get('audience', ''),
            '\n'.join(item.get('step', '') + (' — ' + item['detail'] if item.get('detail') else '') for item in notice.get('action_items', [])),
            '\n'.join(notice.get('required_documents', [])),
            '\n'.join(item.get('field', '') + ': ' + item.get('issue', '') + (' — ' + item['note'] if item.get('note') else '') for item in notice.get('missing', []))]


def apply_primary_edits(record, notice, values):
    """Only changed summary fields override advanced edits; all other nested facts survive."""
    original = primary_values(record)
    for index, key in [(0, 'title'), (1, 'notice_type'), (2, 'issuing_body'), (6, 'audience')]:
        if values[index].strip() != original[index]:
            notice[key] = values[index].strip()
    if any(values[i].strip() != original[i] for i in (3, 4)):
        from datetime import date as date_type
        iso = values[3].strip()
        if iso:
            try:
                if date_type.fromisoformat(iso).isoformat() != iso:
                    raise ValueError('Non-ISO date')
            except ValueError as exc:
                raise BackendError('Enter the main date as YYYY-MM-DD, or leave it blank if unknown.') from exc
        index = main_date_index(record['notice'])
        if index is None:
            if iso or values[4].strip():
                notice['dates'].append({'label': 'User-reviewed date', 'value': values[4].strip(), 'iso_date': iso, 'iso_end_date': '', 'source_ref': ''})
        else:
            if index >= len(notice['dates']):
                raise BackendError('The main date was also removed in detailed fields. Resolve the detailed date list before confirming.')
            notice['dates'][index].update(iso_date=iso, value=values[4].strip())
    if values[5].strip() != original[5]:
        value = values[5].strip()
        if notice['fees']:
            fee = notice['fees'][0]
            currency = fee.get('currency', '')
            if currency and value.startswith(currency + ' '):
                fee['amount'] = value[len(currency):].strip()
            else:
                fee.update(amount=value, currency='')
        elif value:
            notice['fees'].append({'label': 'User-reviewed amount', 'amount': value, 'currency': '', 'iso_date': '', 'source_ref': ''})
    if values[7].strip() != original[7]:
        old = record['notice'].get('action_items', [])
        actions = []
        for index, line in enumerate(values[7].splitlines()):
            if not line.strip():
                continue
            step, separator, detail = line.strip().partition(' — ')
            action = dict(old[index]) if index < len(old) else {'required': True, 'source_ref': ''}
            action.update(step=step, detail=detail if separator else '')
            actions.append(action)
        notice['action_items'] = actions
    if values[8].strip() != original[8]:
        notice['required_documents'] = [line.strip() for line in values[8].splitlines() if line.strip()]
    return Notice.model_validate(notice, strict=True).model_dump()


def source_excerpt_text(record):
    lines = []
    for item in record.get('evidence', []):
        flag = {True: 'quote found in source text', False: 'quote not found in source text', None: 'not automatically verified'}.get(item.get('verified'), 'not automatically verified')
        lines.append(f"{item.get('field', '')}: {item.get('quote', '')}\n[{item.get('source_ref', '')} · {flag}]")
    return '\n\n'.join(lines) or 'No source excerpts returned. Compare all facts with the original document.'


def prepare_plan_download(record):
    """Export the actual confirmed backend record without an additional API call."""
    return export_bytes('checklist.txt', plain_markdown(render_markdown(record)).encode('utf-8'))


def build_model_workflow(reader, navigation, source):
    client = BackendClient()
    request_session = gr.State(RequestSession)
    record = gr.State({})
    plan = gr.State({})
    calendar_state = gr.State({})
    from ui_style import stage_intro
    with reader['upload_controls']:
        language = gr.Dropdown(['English', 'Hindi'], value='English', label='Explanation language')
        analyze_file = gr.Button('Read document →', variant='primary')
        gr.Markdown('Read the notice, check the facts, then prepare your action plan.', elem_classes=['quiet-note'])
        with gr.Accordion('Paste text or check connection', open=False):
            pasted = gr.Textbox(label='Notice text', lines=6, max_length=90000)
            analyze_text = gr.Button('Read pasted text →')
            check = gr.Button('Check backend connection', size='sm')
            gr.Markdown('Hindi output should be checked against the original notice.', elem_classes=['quiet-note'])
    with reader['panels'][1]:
        stage_intro('Check the important details', 'Review dates, amounts and instructions against the notice. Correct anything that needs attention before creating your plan.')
        review_empty = gr.Markdown('**No notice details yet.** Go to Upload and click **Read document**, or read your saved OCR text below.', elem_classes=['empty-state'])
        primary = []
        with gr.Row():
            with gr.Column(scale=6, min_width=280, elem_classes=['surface']):
                for start in [0, 2, 4]:
                    with gr.Row():
                        for label in PRIMARY_LABELS[start:start+2]:
                            primary.append(gr.Textbox(label=label, interactive=True, placeholder='Read the notice first; then review this field.'))
                for label in PRIMARY_LABELS[6:]:
                    primary.append(gr.Textbox(label=label, lines=2, interactive=label != 'Unresolved items'))
                gr.Markdown('The main date and amount summarize the backend’s structured fields. Additional dates, fees and optional actions remain under Detailed fields.', elem_classes=['quiet-note'])
            with gr.Column(scale=4, min_width=260, elem_classes=['surface']):
                gr.Markdown('### Check against the source')
                excerpts = gr.Textbox(label='Source excerpts', lines=9, interactive=False)
                gr.Markdown('Compare excerpts with the original notice on Upload. An excerpt is supporting context, not independent verification.', elem_classes=['quiet-note'])
                provenance = gr.Textbox(label='Analysis source', interactive=False)
                with gr.Accordion('Original evidence and verification', open=False):
                    evidence = gr.JSON(label='Original extraction evidence · not re-verified after your edits')
                confirmed = gr.Checkbox(label='I reviewed these details against the notice and checked unresolved items.')
                generate = gr.Button('Build my action plan →', variant='primary')
        back_notice = gr.Button('← Back to notice')
        editors = []
        table_buttons = []
        with gr.Accordion('Detailed fields · all dates, fees and source references', open=False):
            gr.Markdown('These fields preserve the complete backend response. Edit either the main fields or these details. Main fields you change take precedence for the same detail.')
            for key, label in zip(SCALARS, LABELS):
                editors.append(gr.Textbox(label=label, lines=2, placeholder='Leave blank if not stated in the notice.'))
            for label in ['Required documents · one per line', 'Optional documents · one per line']:
                editors.append(gr.Textbox(label=label, lines=3))
            for field, columns in TABLES.items():
                with gr.Accordion(field.replace('_', ' ').title(), open=False):
                    editors.append(gr.Dataframe(headers=columns, datatype=['bool' if k == 'required' else 'str' for k in columns],
                                                value=[blank_row(columns)], type='array', interactive=True, label=field.replace('_', ' ').title(),
                                                column_count=(len(columns), 'fixed'), wrap=True))
                    gr.Markdown('Blank rows are ignored. Add only details verified against the notice.', elem_classes=['quiet-note'])
                    table_buttons.append((gr.Button('Add row', size='sm'), editors[-1], columns))
            editors.append(gr.Checkbox(label='Source needs clarification · check only if text is unreadable'))
        analyze_saved = gr.Button('Read saved OCR text with Gemma →')
        gr.Markdown('Use Page text and OCR corrections above to save each page first.', elem_classes=['quiet-note'])
    with reader['panels'][2]:
        stage_intro('Your next steps, in one place', 'A clear explanation, an actionable checklist, and files you can keep.')
        results_empty = gr.Markdown('**No action plan yet.** Read your document, review and confirm its details, then click **Build my action plan**.', elem_classes=['empty-state'])
        with gr.Row():
            with gr.Column(scale=6, min_width=280, elem_classes=['surface']):
                summary = gr.Textbox(label='What this notice means', lines=7, interactive=False,
                                     placeholder='Review and confirm the details to create your action plan.', elem_classes=['document-text'])
                required = gr.CheckboxGroup(choices=[], label='Your checklist', interactive=True)
                checklist_empty = gr.Markdown('Review and confirm the notice to generate your checklist.', elem_classes=['quiet-note'])
                optional = gr.CheckboxGroup(choices=[], label='Optional actions', interactive=True)
                with gr.Accordion('Unresolved details', open=False):
                    unresolved = gr.JSON(label='Unresolved details · check with the issuer')
            with gr.Column(scale=4, min_width=260, elem_classes=['surface']):
                gr.Markdown('### Keep your plan')
                download_plan = gr.Button('Download checklist', variant='primary')
                plan_download = gr.File(label='Your checklist · click to download', interactive=False)
                gr.Markdown('Your checklist file appears automatically after you build a plan. For a calendar file, preview and confirm the dates below.', elem_classes=['quiet-note'])
                with gr.Accordion('Calendar · verify dates before export', open=False):
                    preview_dates = gr.Button('Preview calendar dates')
                    calendar_preview = gr.Dataframe(headers=['Event', 'Start date', 'End date', 'Source'], value=[['', '', '', '']], datatype=['str'] * 4, type='array', interactive=False, label='All-day events to review', wrap=True)
                    calendar_message = gr.Textbox(label='Calendar status', value='Preview dates after confirming your notice facts.', interactive=False, lines=3)
                    with gr.Accordion('Calendar response details', open=False):
                        calendar_details = gr.JSON(label='Backend preview response')
                    calendar_confirm = gr.Checkbox(label='I verified these dates, including the year.', interactive=False)
                    export_calendar = gr.Button('Download confirmed calendar', interactive=False)
                    calendar_download = gr.File(label='Calendar ICS', interactive=False)
                edit_details = gr.Button('← Edit the details')
        with gr.Accordion('Related resources · office directory', open=False):
            gr.Markdown('**The supplied directory is fictional demo campus data.** Do not use these contacts as verified information.')
            directory_button = gr.Button('Find directory matches')
            directory_result = gr.JSON(label='Matches, source and last-checked timestamp')
    with reader['panels'][3]:
        status = gr.Textbox(label='Backend workflow status', value='Ready. Analyze a notice to start the real workflow.', interactive=False, lines=3)

    plan_outputs = [plan, summary, required, optional, unresolved, plan_download, calendar_state, calendar_preview, calendar_confirm, calendar_download, directory_result]
    output_components = [record, *editors, provenance, evidence, confirmed, *plan_outputs, status, navigation, *primary, excerpts, review_empty, results_empty, calendar_message, calendar_details, export_calendar, checklist_empty]

    def clear_plan():
        return {**dict(zip(plan_outputs, [{}, '', gr.update(choices=[], value=[]), gr.update(choices=[], value=[]), None, None, {}, [['', '', '', '']], gr.update(value=False, interactive=False), None, None])),
                checklist_empty: gr.update(value='Review and confirm the notice to generate your checklist.', visible=True), calendar_message: 'Preview dates after confirming your notice facts.', calendar_details: None, export_calendar: gr.update(interactive=False), results_empty: gr.update(visible=True)}

    def clear_all():
        empty = {'notice': Notice().model_dump()}
        return {record: {}, **dict(zip(editors, notice_values(empty))), provenance: '', evidence: None,
                confirmed: False, **dict(zip(primary, primary_values({}))), excerpts: '', review_empty: gr.update(visible=True), results_empty: gr.update(visible=True), **clear_plan()}

    def invalidate_input(session):
        session.invalidate()
        return {**clear_all(), status: 'Input changed. Analyze the current input; previous results have been cleared.'}

    def invalidate_facts(session):
        session.invalidate()
        return {confirmed: False, results_empty: gr.update(visible=True), **clear_plan(), status: 'Facts changed. Review and confirm again before using an action plan.'}

    def run_analysis(path, text, lang, session, current_source):
        try:
            revision = session.begin()
        except BackendError as exc:
            yield {status: str(exc)}
            return
        yield {**clear_all(), status: 'Gemma is reading the notice… This may take several minutes. Replacing the input discards this result.'}
        try:
            data = client.analyze(path=path, text=text, language=lang)
            if not session.current(revision):
                return
            src = data.get('source', {})
            unit = 'slide(s)' if src.get('kind') in {'ppt', 'pptx'} else 'content block(s)' if src.get('kind') == 'docx' else 'page(s)'
            description = f"Analysis {data['id']} · {src.get('filename', '')} · {src.get('kind', '')} · {src.get('pages', '?')} {unit}"
            if src.get('warnings'):
                description += '\n' + '\n'.join(str(w) for w in src['warnings'])
            yield {record: data, **dict(zip(editors, notice_values(data))), provenance: description,
                   evidence: data.get('evidence', []), **dict(zip(primary, primary_values(data))), excerpts: source_excerpt_text(data), review_empty: gr.update(visible=False), status: ('Real backend response received. Review facts and uncertainties before confirming.' +
                            (' Some structured details were not returned: ' + ', '.join(field for field in ('dates', 'fees', 'action_items') if not data['notice'].get(field)) + '. Blank rows are not extracted facts.' if any(not data['notice'].get(field) for field in ('dates', 'fees', 'action_items')) else '')),
                   navigation: gr.Tabs(selected='review')}
        except Exception as exc:
            if session.current(revision):
                yield {**clear_all(), status: f'Analysis failed: {exc}'}
        finally:
            session.finish()

    def run_file(path, lang, session, current):
        if not path:
            yield {**clear_all(), status: 'Choose a file first.'}
            return
        yield from run_analysis(path, '', lang, session, current)

    def run_text(text, lang, session, current):
        yield from run_analysis(None, text, lang, session, current)

    def generate_plan(data, checked, lang, session, *values):
        if not data or not checked:
            yield {**clear_plan(), status: 'Analyze a notice, review the extracted facts, and check the confirmation box first.'}
            return
        try:
            revision = session.begin()
        except BackendError as exc:
            yield {status: str(exc)}
            return
        yield {**clear_plan(), status: 'Saving corrections and generating a checklist from the confirmed facts…'}
        try:
            notice = reviewed_notice(data, values[:17])
            if len(values) > 17:
                notice = apply_primary_edits(data, notice, values[17:])
            updated = client.confirm(data, notice, lang)
            if not session.current(revision):
                return
            req, opt = checklist_choices(updated)
            main = primary_values(updated)
            explanation = plain_markdown(updated['checklist']['summary']) + '\n\nConfirmed amount: ' + (main[5] or 'Not supplied') + '\nConfirmed main date: ' + (main[3] or 'Unknown')
            download_message = ''
            try:
                ready_file = prepare_plan_download(updated)
            except Exception as exc:
                ready_file = None
                download_message = f' Checklist file could not be saved: {exc}. Use Download checklist to retry.'
            if not session.current(revision):
                return
            yield {record: updated, plan: updated, summary: explanation, plan_download: ready_file,
                   checklist_empty: gr.update(value='No checklist actions were returned. Check the notice and extracted action fields; no tasks have been invented.', visible=not bool(req or opt)), required: gr.update(choices=req, value=[]), optional: gr.update(choices=opt, value=[]),
                   unresolved: {'missing': updated['notice']['missing'], 'needs_clearer_image': updated['notice']['needs_clearer_image']},
                   **dict(zip(primary, primary_values(updated))), results_empty: gr.update(visible=False), status: 'Plan ready. Your checklist download is available under Keep your plan.' + download_message if ready_file else 'Plan ready.' + download_message, navigation: gr.Tabs(selected='results')}
        except Exception as exc:
            if session.current(revision):
                yield {**clear_plan(), status: f'Plan failed: {exc}. Review and retry; no sample plan was substituted.'}
        finally:
            session.finish()

    def extra(data, session, operation, checked=False, preview=None):
        if not data:
            yield {status: 'Confirm facts and generate a plan first.'}
            return
        try:
            revision = session.begin()
        except BackendError as exc:
            yield {status: str(exc)}
            return
        targets = {'preview': {calendar_state: {}, calendar_preview: [['', '', '', '']], calendar_confirm: gr.update(value=False, interactive=False), calendar_download: None, calendar_message: 'Checking notice dates…', calendar_details: None, export_calendar: gr.update(interactive=False)},
                   'calendar': {calendar_download: None}, 'checklist': {plan_download: None}, 'directory': {directory_result: None}}
        yield {**targets[operation], status: 'Working…'}
        try:
            if operation == 'preview':
                result = client.calendar_preview(data)
                events = result['events']
                rows = [[plain_markdown(e.get('title') or e['label']), e['start'], e.get('end') or e['start'], e.get('source_ref', '')] for e in events]
                message = (f'{len(events)} all-day event(s) ready for review.' if events else 'No usable confirmed dates. Correct dates and resolve date uncertainties on Review before exporting.')
                if result.get('warnings'):
                    message += '\n' + '\n'.join(result['warnings'])
                updates = {calendar_state: result, calendar_preview: rows or [['', '', '', '']], calendar_details: result, calendar_message: message, calendar_confirm: gr.update(value=False, interactive=bool(events)), export_calendar: gr.update(interactive=False)}
            elif operation == 'calendar':
                if not checked or not preview or not preview.get('events'):
                    raise BackendError('Preview dates and confirm them first. Missing dates cannot be exported.')
                updates = {calendar_download: export_bytes('notice.ics', client.calendar_file(data, preview)), calendar_message: 'Calendar file ready. Import it into your calendar application.'}
            elif operation == 'checklist':
                updates = {plan_download: prepare_plan_download(data)}
            else:
                updates = {directory_result: client.enrich(data)}
            if session.current(revision):
                yield {**updates, status: 'Request completed.'}
        except Exception as exc:
            if session.current(revision):
                yield {**targets[operation], calendar_message: f'Calendar request failed: {exc}' if operation in {'preview', 'calendar'} else gr.skip(), status: f'Request failed: {exc}'}
        finally:
            session.finish()

    # All long model calls serialize. Input edits run immediately and invalidate late responses.
    opts = {'concurrency_id': 'backend-workflow', 'concurrency_limit': 1, 'trigger_mode': 'once'}
    for button, fn, inputs in [
        (analyze_file, run_file, [reader['upload'], language, request_session, source]),
        (analyze_text, run_text, [pasted, language, request_session, source]),
        (analyze_saved, run_text, [reader['combined'], language, request_session, source])]:
        button.click(fn, inputs, output_components, **opts)
    generate.click(generate_plan, [record, confirmed, language, request_session, *editors, *primary], output_components, **opts)
    reader['upload'].change(invalidate_input, request_session, output_components, queue=False)
    for component in [pasted, language, source]:
        component.input(invalidate_input, request_session, output_components, queue=False)
    # A programmatic file removal also clears model state.
    reader['upload'].clear(invalidate_input, request_session, output_components, queue=False)
    reader['editor'].input(invalidate_input, request_session, output_components, queue=False)
    reader['combined'].change(invalidate_input, request_session, output_components, queue=False)
    for button, table, columns in table_buttons:
        def append_row(rows, cols=columns):
            return add_table_row(rows, cols)
        button.click(append_row, table, table, queue=False).then(
            invalidate_facts, request_session, output_components, queue=False)
    for component in [*editors, *primary[:9]]:
        component.input(invalidate_facts, request_session, output_components, queue=False)
    def changed_confirmation(checked, session):
        session.invalidate()
        return {results_empty: gr.update(visible=True), **clear_plan(), status: 'Ready to generate a plan.' if checked else 'Confirmation cleared.'}
    confirmed.input(changed_confirmation, [confirmed, request_session], output_components, queue=False)
    for button, operation in [(download_plan, 'checklist'), (preview_dates, 'preview'), (directory_button, 'directory')]:
        def handler(data, session, op=operation):
            yield from extra(data, session, op)
        button.click(handler, [plan, request_session], output_components, **opts)
    def calendar_handler(data, session, checked, preview):
        yield from extra(data, session, 'calendar', checked, preview)
    export_calendar.click(calendar_handler, [plan, request_session, calendar_confirm, calendar_state], output_components, **opts)
    def invalidate_calendar(session, checked, preview):
        session.invalidate()
        return None, gr.update(interactive=bool(checked and preview and preview.get('events')))
    calendar_confirm.input(invalidate_calendar, [request_session, calendar_confirm, calendar_state], [calendar_download, export_calendar], queue=False)
    def health():
        try:
            result = client.health()
            return ('Backend connected; model ready. ' if result.get('ok') else 'Backend connected; model NOT ready. ') + str(result.get('detail', ''))
        except BackendError as exc:
            return str(exc)
    check.click(health, outputs=status)
    back_notice.click(lambda: gr.Tabs(selected='upload'), outputs=navigation, queue=False)
    edit_details.click(lambda: gr.Tabs(selected='review'), outputs=navigation, queue=False)
    return {'record': record, 'plan': plan, 'status': status}
