"""Launch real Gradio + FastAPI and exercise UI APIs. Model inference is TEST-ONLY mocked.
Run from the project root: python scripts/integration_smoke.py
"""
import importlib.util
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
# Avoid unrelated corporate/container proxy settings for loopback-only testing.
for key in ('ALL_PROXY', 'HTTPS_PROXY', 'HTTP_PROXY', 'all_proxy', 'https_proxy', 'http_proxy'):
    os.environ.pop(key, None)
os.environ['GRADIO_ANALYTICS_ENABLED'] = 'False'
from test_backend_integration import IntegrationTests
from gradio_client import Client, handle_file

IntegrationTests.setUpClass()
ui = None
try:
    with patch('model_ui.BackendClient', return_value=IntegrationTests.client):
        spec = importlib.util.spec_from_file_location('deadlense_smoke_ui', ROOT / 'app.py')
        ui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ui)
    ui.demo.queue().launch(server_name='127.0.0.1', server_port=7867, inbrowser=False,
                           prevent_thread_lock=True, css=ui.CSS, theme=ui.THEME, quiet=True)
    client = Client('http://127.0.0.1:7867', verbose=False)
    assert 'Backend connected' in client.predict(api_name='/health')
    print('PASS: Gradio startup and HTTP backend connection')
    for filename in ['tests/fixtures/multipage.pdf', 'fixtures/notice.png']:
        result = client.predict(handle_file(str(ROOT / filename)), 'English', 'Your document', api_name='/run_file')
        assert result[0] == 'Scholarship notice', str(result)[:500]
        print('PASS: real UI upload -> backend ingestion -> controlled inference -> editable facts:', filename)
    result = client.predict('Scholarship deadline is 15 November 2026.', 'English', 'Your document', api_name='/run_text')
    fields = list(result[:17])
    fields[3] = 'User-corrected explanation through the real UI.'
    primary = list(result[30:40])
    primary[3] = '2026-11-16'
    plan = client.predict(True, 'English', *fields, *primary, api_name='/generate_plan')
    assert any('User-corrected explanation' in str(item) for item in plan), str(plan)
    assert any('checklist.txt' in str(item) for item in plan), 'Checklist should be ready immediately after generation'
    assert any('Required' in str(item) or 'Submit application' in str(item) for item in plan)
    print('PASS: UI review corrections -> PATCH -> checklist generation')
    preview = client.predict(api_name='/handler_1')
    assert any('2026-11-16' in str(item) for item in preview)
    calendar = client.predict(True, api_name='/calendar_handler')
    assert any('notice.ics' in str(item) for item in calendar)
    checklist = client.predict(api_name='/handler')
    assert any('checklist.txt' in str(item) for item in checklist)
    directory = client.predict(api_name='/handler_2')
    assert any('checked_at' in str(item) for item in directory)
    print('PASS: UI calendar preview/confirmation/download, checklist download, directory provenance')
    from app import ollama_client
    from unittest.mock import AsyncMock
    with patch.object(ollama_client, 'structured', AsyncMock(side_effect=ollama_client.OllamaUnavailable('TEST model offline'))):
        failed = client.predict('New notice', 'English', 'Your document', api_name='/run_text')
    assert failed[0] == ''
    assert any('503' in str(item) for item in failed)
    print('PASS: model failure clears old facts and reports error; no demo fallback')
    print('All UI smoke checks passed. Gemma inference was mocked, not live.')
finally:
    if ui is not None:
        ui.demo.close()
    IntegrationTests.tearDownClass()
