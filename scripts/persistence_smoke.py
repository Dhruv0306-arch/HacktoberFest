"""Real Gradio/FastAPI session persistence checks; model inference is controlled."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
for key in ('ALL_PROXY', 'HTTPS_PROXY', 'HTTP_PROXY', 'all_proxy', 'https_proxy', 'http_proxy'):
    os.environ.pop(key, None)
os.environ['GRADIO_ANALYTICS_ENABLED'] = 'False'
from test_backend_integration import IntegrationTests, FIXTURE
from gradio_client import Client, handle_file
from app import ollama_client

IntegrationTests.setUpClass()
ui = None
try:
    with patch('model_ui.BackendClient', return_value=IntegrationTests.client):
        spec = importlib.util.spec_from_file_location('deadlense_persistence_ui', ROOT / 'app.py')
        ui = importlib.util.module_from_spec(spec); spec.loader.exec_module(ui)
    ui.demo.queue().launch(server_name='127.0.0.1', server_port=7868, inbrowser=False,
                           prevent_thread_lock=True, css=ui.CSS, theme=ui.THEME, quiet=True)
    client = Client('http://127.0.0.1:7868', verbose=False)
    result = client.predict(handle_file(str(ROOT/'tests/fixtures/office_notice.docx')), 'English', 'Your document', api_name='/run_file')
    states = ui.demo.state_holder[client.session_hash]
    memory = states[ui.workspace._id]
    record = states[ui.backend_workflow['record']._id]
    record_id = record['id']
    fields, primary = list(result[:17]), list(result[30:40])
    fields[2] = 'Issuer edited by the user'
    fields[3] = 'Edited explanation retained across navigation'
    primary[3] = '2026-11-16'
    client.predict(*fields, *primary, api_name='/invalidate_facts')
    expected = memory.draft('Your document')
    normalized_fields = [value.get('data', value) if isinstance(value, dict) else value for value in fields]
    assert expected['editors'] == normalized_fields, (expected['editors'], normalized_fields)
    assert expected['primary'] == primary
    review_event = next(fn.api_name for fn in ui.demo.fns.values()
                        if fn.fn and fn.fn.__name__ == 'remember_tab' and fn.fn.__defaults__ == ('review',))
    client.predict('Your document', api_name='/' + review_event)
    client.predict('Demo sample', api_name='/switch_source')
    demo_result = client.predict(handle_file(str(ROOT/'samples/fee_notice.png')), 'Clean notice', api_name='/read_notice')
    demo_fields = list(demo_result[:10]); demo_fields[2] = 'Demo issuer edit'
    client.predict(*demo_fields, api_name='/sample_edited')
    assert memory.draft('Demo sample')['editors'] == demo_fields
    for _ in range(3):
        client.predict('Your document', api_name='/switch_source')
        assert memory.stages['Your document'] == 'review'
        assert memory.draft('Your document') == expected
        assert states[ui.backend_workflow['record']._id]['id'] == record_id
        client.predict('Demo sample', api_name='/switch_source')
        assert memory.draft('Demo sample')['editors'] == demo_fields
    client.predict('Your document', api_name='/switch_source')
    plan = client.predict(True, 'English', record_id, *fields, *primary, api_name='/generate_plan')
    assert any('Edited explanation retained' in str(item) for item in plan)
    confirmed = states[ui.backend_workflow['record']._id]
    assert confirmed['notice']['dates'][0]['iso_date'] == '2026-11-16'
    # Both editors stay aligned with the confirmed state; generating again must not revert the date.
    snapshot = memory.draft('Your document')
    from model_ui import TABLES
    api_fields = list(snapshot['editors'])
    for index, columns in enumerate(TABLES.values(), 10):
        api_fields[index] = {'headers': columns, 'data': api_fields[index]}
    client.predict(True, 'English', record_id, *api_fields, *snapshot['primary'], api_name='/generate_plan')
    assert states[ui.backend_workflow['record']._id]['notice']['dates'][0]['iso_date'] == '2026-11-16'
    saved_plan = states[ui.backend_workflow['plan']._id]
    for view in ['Demo sample', 'Your document']:
        client.predict(view, api_name='/switch_source')
    assert states[ui.backend_workflow['plan']._id] == saved_plan
    assert memory.stages['Your document'] == 'results'
    before_failure = memory.draft('Your document')
    with patch.object(ollama_client, 'structured', AsyncMock(side_effect=ollama_client.OllamaUnavailable('TEST offline'))):
        failed = client.predict('Replacement notice', 'English', 'Your document', api_name='/run_text')
    assert failed[0] == {'__type__': 'update'}
    assert any('503' in str(item) for item in failed)
    assert memory.draft('Your document') == before_failure
    assert states[ui.backend_workflow['plan']._id] == saved_plan
    with tempfile.TemporaryDirectory() as folder:
        bad = Path(folder)/'broken.docx'; bad.write_bytes(b'not a document')
        failed = client.predict(handle_file(str(bad)), 'English', 'Your document', api_name='/run_file')
        assert any('400' in str(item) for item in failed)
    assert memory.draft('Your document') == before_failure
    assert states[ui.backend_workflow['record']._id]['id'] == record_id
    replacement = dict(FIXTURE, title='Replacement document', issuing_body='New issuer', required_documents=[])
    with patch.object(ollama_client, 'structured', AsyncMock(return_value=replacement)):
        newer = client.predict('Replacement deadline: 15 November 2026', 'English', 'Your document', api_name='/run_text')
    assert newer[0] == 'Replacement document'
    assert memory.draft('Your document')['editors'][2] == 'New issuer'
    assert states[ui.backend_workflow['record']._id]['id'] != record_id
    assert states[ui.backend_workflow['plan']._id] == {}
    assert memory.draft('Demo sample')['editors'] == demo_fields
    stale = client.predict(True, 'English', record_id, *fields, *primary, api_name='/generate_plan')
    assert any('active document changed' in str(item) for item in stale)
    assert states[ui.backend_workflow['record']._id]['notice']['title'] == 'Replacement document'
    client.predict(api_name='/reset_document')
    client.predict(api_name='/reset_reader')
    assert states[ui.backend_workflow['record']._id] == {}
    assert memory.draft('Your document') == {}
    assert memory.draft('Demo sample')['editors'] == demo_fields
    local = client.predict(handle_file(str(ROOT/'tests/fixtures/multipage.pdf')), 'English', False, api_name='/process_document')
    client.predict('1', 'Auto-retained first-page edit', api_name='/save_page_1')
    client.predict('2', api_name='/load_page')
    client.predict('1', api_name='/load_page')
    assert states[ui.document_result._id]['pages'][0]['text'] == 'Auto-retained first-page edit'
    client.predict('Demo sample', api_name='/switch_source')
    client.predict('Your document', api_name='/switch_source')
    assert states[ui.document_result._id]['pages'][0]['text'] == 'Auto-retained first-page edit'
    client.predict(api_name='/reset_reader')
    assert states[ui.document_result._id] == {}
    print('PASS: local PDF page edit autosave, page changes, source switches and scoped reset')
    print('PASS: upload, raw edits, navigation, demo isolation, repeated source switches, confirmed-state alignment')
    print('PASS: failed model/corrupted upload retain results; new success replaces cleanly; explicit reset stays scoped')
    print('Persistence smoke checks passed. Actual UI/backend HTTP; controlled model inference, not live Gemma.')
finally:
    if ui is not None:
        ui.demo.close()
    IntegrationTests.tearDownClass()
