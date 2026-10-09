from pathlib import Path
import gradio as gr
from ui_adapter import MODE, SCALARS, LISTS, extract_notice, build_guidance
from ui_utils import export_plan, parse_deadline, official_resources
from document_ui import build_document_reader

BASE = Path(__file__).resolve().parent
EDIT_KEYS = SCALARS + ['eligibility', 'required_actions', 'documents_needed', 'unclear_fields']
LABELS = ['Notice title', 'Category (e.g. college_fee)', 'Issuer', 'Deadline (YYYY-MM-DD; blank if unknown)',
          'Original deadline wording', 'Amount (include currency)', 'Who it applies to — one per line',
          'Required actions — one per line', 'Documents needed — one per line', 'Unresolved items — one per line']

def sample_path(name):
    filename = 'fee_notice_unclear.png' if name == 'Unclear deadline' else 'fee_notice.png'
    return str(BASE / 'samples' / filename)

def empty_plan():
    return '', gr.CheckboxGroup(choices=[], value=[]), None, []

def invalidate():
    return False, False, *empty_plan(), 'Details changed. Review and confirm before generating a new plan.'

def reset_notice():
    return {}, *([''] * len(EDIT_KEYS)), '', False, False, *empty_plan(), 'Ready. Click Read notice.'

def read_notice(path, sample):
    try:
        data = extract_notice(path, sample)
        values = ['\n'.join(data.get(k, [])) if k in LISTS else (data.get(k) or '') for k in EDIT_KEYS]
        evidence = '\n\n'.join(data['source_evidence']) or 'No supporting excerpt returned. Compare fields with the image.'
        message = 'Needs review: check the unresolved items.' if data['unclear_fields'] else 'Extracted. Compare the details with the notice before confirming.'
        return data, *values, evidence, False, False, *empty_plan(), message
    except Exception as exc:
        # Clear stale results even if the next model request fails.
        result = list(reset_notice())
        result[-1] = f'Could not read notice: {exc}'
        return tuple(result)

def create_plan(original, language, region, confirmed, date_checked, *values):
    try:
        if not original:
            raise ValueError('Read a notice first.')
        if not confirmed:
            raise ValueError('Check the confirmation box after reviewing the details.')
        fields = dict(original)
        for key, value in zip(EDIT_KEYS, values):
            fields[key] = [line.strip() for line in value.splitlines() if line.strip()] if key in LISTS else (value.strip() or None)
        parse_deadline(fields['deadline'])
        guidance = build_guidance(fields, language)
        checklist = list(dict.fromkeys(guidance['checklist']))
        guidance['checklist'] = checklist
        files = export_plan(fields, guidance, date_checked, MODE)
        summary = guidance['explanation']
        summary += f"\n\nConfirmed amount: {fields.get('amount') or 'Not supplied'}\nConfirmed deadline: {fields.get('deadline') or 'Unknown'}"
        if fields['unclear_fields']:
            summary += '\n\nStill unresolved:\n' + '\n'.join(fields['unclear_fields'])
        message = 'Plan ready. Tick tasks as you complete them.'
        if len(files) == 1:
            message += ' Calendar omitted: verify the date and resolve all unclear items to enable it.'
        return summary, gr.CheckboxGroup(choices=checklist, value=[]), files, official_resources(fields['doc_type'], region), message
    except Exception as exc:
        return *empty_plan(), f'Could not build plan: {exc}'

CSS = '''
.gradio-container {max-width: 1180px !important; margin: auto;}
#hero {background: #122c38; border-radius: 18px; padding: 28px; margin-bottom: 16px;}
#hero h1, #hero p {color: #f3faf8 !important;}
#hero h1 {font-size: 38px; letter-spacing: -1px; margin-bottom: 8px;}
'''

with gr.Blocks(title='NoticeBridge', analytics_enabled=False) as demo:
    original = gr.State({})
    gr.HTML('<div id="hero"><p>COMMUNITY NOTICES → CLEAR NEXT STEPS</p><h1>NoticeBridge</h1><p>Read it. Verify it. Act on it.</p></div>')
    document_result = build_document_reader()
    gr.Markdown('---\n## Notice-to-action workflow · saved demo / future model integration')
    gr.Markdown('**DEMO MODE — saved synthetic results; no AI image reading. English only.**' if MODE == 'demo' else '**LIVE MODE — connected to your model.py backend. Review extracted facts before acting.**')
    with gr.Row():
        with gr.Column(scale=4):
            gr.Markdown('## 1 · Read your notice')
            sample = gr.Dropdown(['Clean notice', 'Unclear deadline'], value='Clean notice', label='Synthetic demo sample', visible=MODE == 'demo')
            notice = gr.Image(value=sample_path('Clean notice') if MODE == 'demo' else None,
                              type='filepath', label='Original notice', interactive=MODE == 'live')
            language = gr.Dropdown(['English'] if MODE == 'demo' else ['English', 'Hindi', 'Kannada'], value='English', label='Explanation language')
            read_button = gr.Button('Read notice', variant='primary')
            with gr.Accordion('Source excerpts — compare with the image', open=True):
                evidence = gr.Textbox(label='Model-extracted evidence; not independent verification', lines=6, interactive=False)
        with gr.Column(scale=6):
            gr.Markdown('## 2 · Check what we found')
            gr.Markdown('Correct anything misread. Keep missing facts blank. Remove an unresolved item only after checking it.')
            editors = []
            for key, label in zip(EDIT_KEYS, LABELS):
                editors.append(gr.Textbox(label=label, lines=2 if key in LISTS else 1, interactive=True))
            region = gr.Textbox(label='Region for official resources (optional)', placeholder='Exact region used in your verified CSV')
            confirmed = gr.Checkbox(label='I reviewed the details against the original notice.')
            date_checked = gr.Checkbox(label='I verified the full deadline, including the year. Enable calendar download.')
            confirm_button = gr.Button('Confirm details & build my plan', variant='primary')
    status = gr.Textbox(label='Workflow status', value='Ready. Click Read notice.', interactive=False)
    gr.Markdown('## 3 · Your action plan')
    explanation = gr.Textbox(label='Simple explanation', lines=6, interactive=False)
    tasks = gr.CheckboxGroup(choices=[], label='My next steps', interactive=True)
    downloads = gr.File(label='Download your plan / calendar', file_count='multiple', interactive=False)
    resources = gr.Dataframe(headers=['Organization', 'Official URL', 'Phone', 'Verified on'], datatype=['str'] * 4, value=[], interactive=False, label='Related official resources — empty until verified matches are added')
    plan_outputs = [explanation, tasks, downloads, resources]
    extraction_outputs = [original, *editors, evidence, confirmed, date_checked, *plan_outputs, status]
    event_options = {'concurrency_id': 'workflow', 'concurrency_limit': 1}
    read_button.click(read_notice, [notice, sample], extraction_outputs, **event_options)
    confirm_button.click(create_plan, [original, language, region, confirmed, date_checked, *editors], [*plan_outputs, status], **event_options)
    notice.change(reset_notice, outputs=extraction_outputs, **event_options)
    sample.change(sample_path, sample, notice, **event_options)
    for component in [*editors, language, region]:
        component.input(invalidate, outputs=[confirmed, date_checked, *plan_outputs, status], **event_options)
    for checkbox in [confirmed, date_checked]:
        checkbox.input(empty_plan, outputs=plan_outputs, **event_options)

if __name__ == '__main__':
    demo.queue().launch(server_name='127.0.0.1', inbrowser=True, css=CSS)
