from pathlib import Path
import gradio as gr
from ui_adapter import MODE, SCALARS, LISTS, extract_notice, build_guidance
from ui_utils import export_plan, parse_deadline, official_resources
from document_ui import build_document_reader
from model_ui import build_model_workflow
from presentation_utils import plain_markdown

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
        summary = plain_markdown(guidance['explanation'])
        summary += f"\n\nConfirmed amount: {fields.get('amount') or 'Not supplied'}\nConfirmed deadline: {fields.get('deadline') or 'Unknown'}"
        if fields['unclear_fields']:
            summary += '\n\nStill unresolved:\n' + '\n'.join(fields['unclear_fields'])
        message = 'Plan ready. Tick tasks as you complete them.'
        if len(files) == 1:
            message += ' Calendar omitted: verify the date and resolve all unclear items to enable it.'
        return summary, gr.CheckboxGroup(choices=[plain_markdown(item) for item in checklist], value=[]), files, official_resources(fields['doc_type'], region), message
    except Exception as exc:
        return *empty_plan(), f'Could not build plan: {exc}'

from ui_style import CSS, THEME, stage_intro

SAMPLE_SOURCE = 'Demo sample' if MODE == 'demo' else 'Model workflow'


def switch_source(source):
    real = source == 'Your document'
    note = ('**Your document** · Analyze a PDF, image or pasted text with your backend, review the facts, then generate your action plan.' if real else
            '**Demo sample — saved example data** · Explore an example action plan. These results are not extracted from your uploads.' if MODE == 'demo' else
            '**Model workflow** · Uses the configured model adapter only when you click Read notice.')
    return (*[gr.Group(visible=real) for _ in range(4)],
            *[gr.Group(visible=not real) for _ in range(4)],
            gr.Tabs(selected='upload'), note)


with gr.Blocks(title='DeadLense', analytics_enabled=False) as demo:
    original = gr.State({})
    gr.HTML('<div id="brand"><div class="mark" aria-hidden="true">D</div><div><div class="brand-name">DeadLense</div><div class="brand-tag">Community information, made clear</div></div></div>')
    gr.HTML('<div id="hero"><p class="eyebrow">READ · VERIFY · ACT</p><h1>Read your notice. Know what comes next.</h1><p class="subtitle">Upload a document, preview it, and review its text. Or explore a saved sample to see an example action plan.</p></div>')
    source = gr.Radio(['Your document', SAMPLE_SOURCE], value='Your document', label='Choose your starting point', elem_id='source-switch')
    source_note = gr.Markdown('**Your document** · Analyze a PDF, image or pasted text with your backend, review the facts, then generate your action plan.', elem_id='source-note')
    with gr.Tabs(selected='upload', elem_id='workflow') as navigation:
        with gr.Tab('01  Upload', id='upload') as upload_tab:
            pass
        with gr.Tab('02  Review', id='review') as review_tab:
            pass
        with gr.Tab('03  Results', id='results') as results_tab:
            pass
    reader = build_document_reader(upload_tab, review_tab, results_tab, navigation, source)
    document_result = reader['result']
    backend_workflow = build_model_workflow(reader, navigation, source)
    with upload_tab:
        with gr.Group(visible=False) as sample_upload:
            stage_intro('Explore a sample notice' if MODE == 'demo' else 'Choose a notice for the model',
                        'Walk through a complete notice-to-action example. Sample results are clearly labelled and never represent an uploaded document.' if MODE == 'demo' else 'Upload an image for the configured model. Your local document-reader text stays independent.')
            with gr.Row(equal_height=True):
                with gr.Column(scale=4, min_width=280, elem_classes=['surface']):
                    sample = gr.Dropdown(['Clean notice', 'Unclear deadline'], value='Clean notice', label='Sample notice', visible=MODE == 'demo')
                    language = gr.Dropdown(['English'] if MODE == 'demo' else ['English', 'Hindi', 'Kannada'], value='English', label='Explanation language')
                    gr.Markdown('This sample demonstrates reviewing facts, correcting mistakes, and preparing a checklist and calendar event.' if MODE == 'demo' else 'Model results must be checked against the original notice.', elem_classes=['quiet-note'])
                    read_button = gr.Button('Read sample →' if MODE == 'demo' else 'Read notice →', variant='primary')
                with gr.Column(scale=6, min_width=280, elem_classes=['surface']):
                    notice = gr.Image(value=sample_path('Clean notice') if MODE == 'demo' else None,
                                      type='filepath', label='Original notice', interactive=MODE == 'live', height=400)
    with review_tab:
        with gr.Group(visible=False) as sample_review:
            stage_intro('Check the important details', 'Review dates, amounts and instructions against the notice. Correct anything that needs attention before creating your plan.')
            sample_review_empty = gr.Markdown('**No notice details yet.** Go to Upload and click **Read sample** to load the saved example.' if MODE == 'demo' else '**No notice details yet.** Go to Upload and click **Read notice** first.', elem_classes=['empty-state'])
            with gr.Row():
                with gr.Column(scale=6, min_width=280, elem_classes=['surface']):
                    editors = []
                    for start in [0, 2, 4]:
                        with gr.Row():
                            for key, label in zip(EDIT_KEYS[start:start+2], LABELS[start:start+2]):
                                editors.append(gr.Textbox(label=label, interactive=True, placeholder='Read the notice first; then review this field.'))
                    for key, label in zip(EDIT_KEYS[6:], LABELS[6:]):
                        editors.append(gr.Textbox(label=label, lines=2, interactive=True))
                with gr.Column(scale=4, min_width=260, elem_classes=['surface']):
                    gr.Markdown('### Check against the source')
                    evidence = gr.Textbox(label='Source excerpts', lines=9, interactive=False)
                    gr.Markdown('Compare excerpts with the original notice on the Upload tab. An excerpt is supporting context, not independent verification.', elem_classes=['quiet-note'])
                    with gr.Accordion('Related resources', open=False):
                        region = gr.Textbox(label='Region (optional)', placeholder='Match a region in your verified resource list')
                    confirmed = gr.Checkbox(label='I reviewed these details against the notice.')
                    date_checked = gr.Checkbox(label='I verified the full deadline, including the year. Include a calendar file.')
                    confirm_button = gr.Button('Build my action plan →', variant='primary')
            sample_back = gr.Button('← Back to notice')
    with results_tab:
        with gr.Group(visible=False) as sample_results:
            stage_intro('Your next steps, in one place', 'A clear explanation, an actionable checklist, and files you can keep.' + (' These results belong to the synthetic sample.' if MODE == 'demo' else ''))
            sample_results_empty = gr.Markdown('**No action plan yet.** Read the notice on Upload, review and confirm its details, then click **Build my action plan**.', elem_classes=['empty-state'])
            with gr.Row():
                with gr.Column(scale=6, min_width=280, elem_classes=['surface']):
                    explanation = gr.Textbox(label='What this notice means', lines=7, interactive=False,
                                             placeholder='Review and confirm the details to create your action plan.', elem_classes=['document-text'])
                    tasks = gr.CheckboxGroup(choices=[], label='Your checklist', interactive=True)
                with gr.Column(scale=4, min_width=260, elem_classes=['surface']):
                    gr.Markdown('### Keep your plan')
                    downloads = gr.File(label='Checklist & calendar', file_count='multiple', interactive=False)
                    gr.Markdown('Calendar export requires a verified date and no unresolved fields.', elem_classes=['quiet-note'])
                    sample_edit = gr.Button('← Edit the details')
            with gr.Accordion('Related official resources', open=False):
                resources = gr.Dataframe(headers=['Organization', 'Official URL', 'Phone', 'Verified on'], datatype=['str'] * 4, value=[], interactive=False, label='Verified matches')
                gr.Markdown('Only entries matching your category and region appear. No match means no recommendation.', elem_classes=['quiet-note'])
    with gr.Group(visible=False) as sample_status:
        status = gr.Textbox(label='Sample workflow status' if MODE == 'demo' else 'Model workflow status', value='Choose a sample and click Read sample.' if MODE == 'demo' else 'Choose a notice to begin.', lines=2, interactive=False, elem_classes=['workflow-status'])
    gr.Markdown('DeadLense · Review important dates and amounts before acting. Your document and sample walkthrough remain separate.', elem_id='footer-note')
    source.input(switch_source, source, [*reader['panels'], sample_upload, sample_review, sample_results, sample_status, navigation, source_note], queue=False)
    plan_outputs = [explanation, tasks, downloads, resources]
    extraction_outputs = [original, *editors, evidence, confirmed, date_checked, *plan_outputs, status]
    event_options = {'concurrency_id': 'workflow', 'concurrency_limit': 1}
    read_button.click(read_notice, [notice, sample], extraction_outputs, **event_options).then(
        lambda data, current: gr.Tabs(selected='review') if data and current == SAMPLE_SOURCE else gr.skip(), [original, source], navigation)
    confirm_button.click(create_plan, [original, language, region, confirmed, date_checked, *editors], [*plan_outputs, status], **event_options).then(
        lambda text, current: gr.Tabs(selected='results') if text and current == SAMPLE_SOURCE else gr.skip(), [explanation, source], navigation)
    notice.change(reset_notice, outputs=extraction_outputs, **event_options)
    sample.change(sample_path, sample, notice, **event_options)
    for component in [*editors, language, region]:
        component.input(invalidate, outputs=[confirmed, date_checked, *plan_outputs, status], **event_options)
    for checkbox in [confirmed, date_checked]:
        checkbox.input(empty_plan, outputs=plan_outputs, **event_options)
    sample_back.click(lambda: gr.Tabs(selected='upload'), outputs=navigation, queue=False)
    sample_edit.click(lambda: gr.Tabs(selected='review'), outputs=navigation, queue=False)
    original.change(lambda data: gr.Markdown(visible=not bool(data)), original, sample_review_empty, queue=False)
    explanation.change(lambda text: gr.Markdown(visible=not bool(text)), explanation, sample_results_empty, queue=False)

if __name__ == '__main__':
    demo.queue().launch(server_name='127.0.0.1', inbrowser=True, css=CSS, theme=THEME)
