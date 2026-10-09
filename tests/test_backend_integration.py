"""Actual HTTP/backend integration, with ONLY model inference replaced by controlled fixtures.
These tests do not establish live Gemma accuracy or Hindi generation quality.
"""
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
from threading import Thread
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import uvicorn
from fastapi.testclient import TestClient
from app.main import app
from app import ollama_client, store
from app.schemas import Notice
from app.services.calendar import preview_events, to_ics
from backend_client import BackendClient, BackendError, validate_record
from model_ui import notice_values, reviewed_notice, checklist_choices, RequestSession

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Notice(
    title='Scholarship notice', summary='Submit an application.', issuing_body='Test issuer',
    dates=[{'label': 'Deadline', 'value': '15 November 2026', 'iso_date': '2026-11-15', 'source_ref': 'pasted text'}],
    required_documents=['Student ID'], optional_documents=['Sports certificate'],
    action_items=[{'step': 'Submit application', 'required': True},
                  {'step': 'Bring sports certificate', 'required': False}],
    evidence=[{'field': 'deadline', 'quote': '15 November 2026', 'source_ref': 'pasted text'}],
).model_dump()


async def controlled_model(messages, schema, **kwargs):
    """Explicit test-only inference. Production modules never import this file."""
    if 'steps' in schema['properties']:
        notice = json.loads(messages[-1]['content'].split('EXTRACTED NOTICE JSON:\n', 1)[1].split('\n\nFOCUS:', 1)[0])
        return {'summary': notice['summary'], 'steps': [dict(item, order=index+1) for index, item in enumerate(notice['action_items'])]}
    if 'matches' in schema['properties']:
        return {'matches': [{'id': 'scholarship-office', 'reason': 'Controlled test match'}]}
    return deepcopy(FIXTURE)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.patches = [patch.object(store, 'ANALYSES_DIR', Path(cls.folder.name)),
                       patch.object(ollama_client, 'ping', AsyncMock(return_value={'ok': True, 'detail': 'TEST inference only'})),
                       patch.object(ollama_client, 'structured', side_effect=controlled_model)]
        for item in cls.patches:
            item.start()
        cls.socket = socket.socket()
        cls.socket.bind(('127.0.0.1', 0))
        cls.url = f'http://127.0.0.1:{cls.socket.getsockname()[1]}'
        cls.server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        cls.thread = Thread(target=cls.server.run, kwargs={'sockets': [cls.socket]}, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 10
        while not cls.server.started and time.monotonic() < deadline:
            time.sleep(.02)
        if not cls.server.started:
            raise RuntimeError('Test backend did not start')
        cls.client = BackendClient(cls.url)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(10)
        cls.socket.close()
        for item in reversed(cls.patches):
            item.stop()
        cls.folder.cleanup()

    def request(self, method, path, **kwargs):
        return httpx.request(method, self.url + path, trust_env=False, **kwargs)

    def test_complete_text_review_checklist_directory_calendar(self):
        self.assertTrue(self.client.health()['ok'])
        data = self.client.analyze(text='Scholarship deadline is 15 November 2026.')
        self.assertTrue(data['evidence'][0]['verified'])
        values = notice_values(data)
        values[3] = 'Corrected explanation from the user.'
        values[10][0][2] = '2026-11-16'
        notice = reviewed_notice(data, values)
        updated = self.client.confirm(data, notice, 'English')
        self.assertEqual(updated['notice']['dates'][0]['iso_date'], '2026-11-16')
        self.assertEqual(updated['checklist']['summary'], values[3])
        self.assertTrue(updated['corrections'])
        required, optional = checklist_choices(updated)
        self.assertIn('Submit application', required[0])
        self.assertIn('sports certificate', optional[0])
        preview = self.client.calendar_preview(updated)
        self.assertEqual(preview['events'][0]['start'], '2026-11-16')
        calendar = self.client.calendar_file(updated)
        self.assertIn(b'DTSTART;VALUE=DATE:20261116', calendar)
        self.assertIn(b'DTEND;VALUE=DATE:20261117', calendar)
        self.assertIn(b'Corrected explanation', self.client.checklist_file(updated))
        directory = self.client.enrich(updated)
        self.assertIn('demo', directory['source']['name'])
        self.assertTrue(directory['matches'][0]['checked_at'])

    def test_real_pdf_ingestion_over_http(self):
        for name, count in [('text.pdf', 1), ('multipage.pdf', 2)]:
            data = self.client.analyze(path=ROOT / 'tests/fixtures' / name)
            self.assertEqual(data['source']['kind'], 'pdf')
            self.assertEqual(data['source']['pages'], count)

    def test_real_image_upload_over_http(self):
        result = self.client.analyze(path=ROOT / 'fixtures/notice.png')
        self.assertEqual(result['source']['images'], 1)
        self.assertIsNone(result['evidence'][0]['verified'])

    def test_bad_files_and_scanned_pdf_are_errors(self):
        for name in ['scanned.pdf', 'protected.pdf', 'blank.pdf']:
            with self.assertRaises(BackendError):
                self.client.analyze(path=ROOT / 'tests/fixtures' / name)
        for name, data, kind in [('a.pdf', b'', 'application/pdf'), ('b.pdf', b'not pdf', 'application/pdf'),
                                 ('c.png', b'fake image', 'image/png'), ('d.exe', b'print(1)', 'application/octet-stream')]:
            result = self.request('POST', '/api/analyze', files={'file': (name, data, kind)})
            self.assertEqual(result.status_code, 400, result.text)

    def test_oversized_upload_rejected(self):
        with patch('app.main.MAX_UPLOAD_BYTES', 10):
            result = self.request('POST', '/api/analyze', files={'file': ('x.txt', b'x'*11, 'text/plain')})
        self.assertEqual(result.status_code, 400)

    def test_patch_rejects_types_and_unknown_keys_without_mutation(self):
        data = self.client.analyze(text='Test notice')
        for patch_data in [{'dates': 'bad'}, {'title': []}, {'unexpected': True}]:
            result = self.request('PATCH', f"/api/analyses/{data['id']}", json=patch_data)
            self.assertEqual(result.status_code, 400)
        actual = self.request('GET', f"/api/analyses/{data['id']}").json()
        self.assertEqual(actual['notice'], data['notice'])

    def test_unknown_dates_are_not_calendared(self):
        data = self.client.analyze(text='Test notice')
        notice = deepcopy(data['notice'])
        notice['dates'] = [{'label': 'Deadline', 'value': 'unclear', 'iso_date': '', 'iso_end_date': '', 'source_ref': 'image'}]
        result = self.client.confirm(data, notice, 'English')
        self.assertEqual(self.client.calendar_preview(result)['events'], [])
        with self.assertRaises(BackendError):
            self.client.calendar_file(result)

    def test_calendar_requires_boolean_confirmation(self):
        result = self.request('POST', '/api/calendar/ics', json={'confirmed': 'false', 'dates': FIXTURE['dates']})
        self.assertEqual(result.status_code, 400)
        events, warnings = preview_events([{'iso_date': '2026-02-30'}, {'iso_date': '2026-02-01', 'iso_end_date': '2026-02-30'}])
        self.assertEqual(events, [])
        self.assertEqual(len(warnings), 2)

    def test_hindi_title_download_does_not_break_headers(self):
        data = self.client.analyze(text='परीक्षा सूचना', language='Hindi')
        notice = deepcopy(data['notice'])
        notice['title'] = 'परीक्षा सूचना'
        updated = self.client.confirm(data, notice, 'Hindi')
        self.assertIn('परीक्षा सूचना', self.client.checklist_file(updated).decode())
        self.assertTrue(self.client.calendar_file(updated).startswith(b'BEGIN:VCALENDAR'))

    def test_model_failure_has_no_mock_fallback(self):
        with patch.object(ollama_client, 'structured', AsyncMock(side_effect=ollama_client.OllamaUnavailable('Test model offline'))):
            with self.assertRaisesRegex(BackendError, '503'):
                self.client.analyze(text='Real user notice')
        with patch.object(ollama_client, 'structured', AsyncMock(return_value={'unexpected': 'bad'})):
            with self.assertRaisesRegex(BackendError, '502'):
                self.client.analyze(text='Real user notice')

    def test_http_and_response_errors(self):
        for content in [b'not JSON', b'[]', b'{}', b'{"id":"abc","notice":{"unknown":"value"}}', b'{"id":"abc","notice":{"dates":"bad"}}']:
            client = BackendClient('http://test', transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content)))
            with self.assertRaises(BackendError):
                client.analyze(text='Some notice')
        def timed_out(request):
            raise httpx.ReadTimeout('test')
        client = BackendClient('http://test', transport=httpx.MockTransport(timed_out))
        with self.assertRaisesRegex(BackendError, 'timed out'):
            client.analyze(text='Notice')

    def test_replacement_and_duplicate_requests(self):
        session = RequestSession()
        token = session.begin()
        with self.assertRaises(BackendError):
            session.begin()
        session.invalidate()
        self.assertFalse(session.current(token))
        session.finish()
        self.assertTrue(session.current(session.begin()))
        session.finish()


if __name__ == '__main__':
    unittest.main()
