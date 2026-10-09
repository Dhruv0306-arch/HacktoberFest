"""Presentation and calendar regressions. Calendar API tests make actual HTTP calls."""
from copy import deepcopy
import unittest

from app.schemas import Notice
from app.services.calendar import preview_events, to_ics, dates_from_notice, CalendarError
from backend_client import validate_record
from model_ui import checklist_choices
from presentation_utils import plain_markdown, markdown_checklist
import test_backend_integration as fixture_server


class ChecklistPresentationTests(unittest.TestCase):
    def test_formatting_removed_and_links_content_preserved(self):
        text = '# Steps\n\n1. **Pay** INR 2500\n2. Bring `Student ID`\n\n[Portal](https://example.org/apply)'
        result = plain_markdown(text)
        self.assertNotIn('**', result)
        self.assertNotIn('`', result)
        self.assertNotIn('# Steps', result)
        self.assertIn('Pay INR 2500', result)
        self.assertIn('Student ID', result)
        self.assertIn('Portal (https://example.org/apply)', result)

    def test_structured_checkboxes_preserve_order_and_optional_flags(self):
        record = {'checklist': {'steps': [{'step': '**Pay fee**', 'detail': 'Bring `Student ID`', 'required': True, 'source_ref': 'page 1'},
                                        {'step': '*Sports certificate*', 'detail': '', 'required': False, 'source_ref': ''}]}}
        required, optional = checklist_choices(record)
        self.assertIn('1. Required · Pay fee', required[0])
        self.assertIn('Bring Student ID', required[0])
        self.assertIn('2. Optional · Sports certificate', optional[0])
        self.assertEqual(record['checklist']['steps'][0]['step'], '**Pay fee**')

    def test_markdown_response_adapted_without_losing_notes(self):
        raw = '# Action plan\n\n## Required\n- [ ] **Pay** INR 2500\n\n## Optional\n- [ ] Bring sports certificate\n\n## Missing\n- Deadline is unreadable'
        record = {'id': 'test123', 'notice': Notice(title='Notice').model_dump(), 'checklist': raw}
        result = validate_record(record, checklist=True)
        self.assertEqual(len(result['checklist']['steps']), 2)
        self.assertTrue(result['checklist']['steps'][0]['required'])
        self.assertFalse(result['checklist']['steps'][1]['required'])
        self.assertIn('Deadline is unreadable', result['checklist']['summary'])
        self.assertEqual(checklist_choices({'checklist': {'steps': []}}), ([], []))
        unclassified = markdown_checklist('- Read the notice')['steps'][0]
        self.assertFalse(unclassified['_requirement_known'])


class CalendarRegressionTests(unittest.TestCase):
    def test_missing_invalid_reversed_and_duplicate_dates(self):
        dates = [
            {'label': 'Unknown', 'value': '20 October', 'iso_date': ''},
            {'label': 'Invalid', 'iso_date': '2026-02-30'},
            {'label': 'Reversed', 'iso_date': '2026-10-20', 'iso_end_date': '2026-10-19'},
            {'label': 'Deadline', 'iso_date': '2026-10-20'},
            {'label': 'Deadline', 'iso_date': '2026-10-20'},
            {'label': 'Out of range', 'iso_date': '9999-12-31'},
        ]
        events, warnings = preview_events(dates)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(warnings), 5)
        with self.assertRaises(CalendarError):
            preview_events([{'iso_date': 20261020}])

    def test_ics_stable_uids_titles_descriptions_and_exclusive_end(self):
        events, _ = preview_events([{'label': 'Payment deadline', 'value': '20 October 2026, 4 PM', 'iso_date': '2026-10-20', 'source_ref': 'page 2'}])
        one = to_ics(events, title='Semester Fee Notice', notes='Pay INR 2500.', event_namespace='analysis-1')
        two = to_ics(events, title='Semester Fee Notice', notes='Pay INR 2500.', event_namespace='analysis-1')
        uid = lambda text: [line for line in text.split('\r\n') if line.startswith('UID:')]
        self.assertEqual(uid(one), uid(two))
        self.assertIn('SUMMARY:Semester Fee Notice — Payment deadline', one)
        self.assertIn('Pay INR 2500.', one)
        self.assertIn('20 October 2026\\, 4 PM', one)
        self.assertIn('Source: page 2', one.replace('\r\n ', ''))
        self.assertIn('DTEND;VALUE=DATE:20261021', one)
        self.assertTrue(one.endswith('END:VCALENDAR\r\n'))
        self.assertTrue(all(len(line.encode()) <= 75 for line in one.split('\r\n')))

    def test_uncertain_dates_blocked_without_mutating_notice(self):
        notice = {'dates': [{'label': 'Payment deadline', 'value': '20 Oct', 'iso_date': '2026-10-20'}],
                  'missing': [{'field': 'deadline', 'issue': 'unclear', 'note': 'Year unreadable'}]}
        original = deepcopy(notice)
        dates = dates_from_notice(notice)
        self.assertEqual(dates[0]['iso_date'], '')
        self.assertEqual(notice, original)
        self.assertEqual(preview_events(dates)[0], [])


# Reuse the real HTTP test server, but avoid inheriting/re-running its tests.
class CalendarEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_server.IntegrationTests.setUpClass()
    @classmethod
    def tearDownClass(cls):
        fixture_server.IntegrationTests.tearDownClass()

    def test_markdown_fields_from_actual_backend_response_render_as_checkboxes(self):
        data = fixture_server.IntegrationTests.client.analyze(text='Scholarship notice')
        notice = deepcopy(data['notice'])
        notice['summary'] = '## Important\nPay **INR 2500**.'
        notice['action_items'][0].update(step='**Pay fee**', detail='Bring `Student ID`')
        result = fixture_server.IntegrationTests.client.confirm(data, notice, 'English')
        required, optional = checklist_choices(result)
        self.assertIn('Pay fee', required[0])
        self.assertIn('Bring Student ID', required[0])
        self.assertNotIn('**', required[0])
        self.assertNotIn('`', required[0])
        self.assertIn('Pay INR 2500.', plain_markdown(result['checklist']['summary']))

    def test_actual_calendar_endpoints_with_confirmed_dates_and_notes(self):
        response = fixture_server.IntegrationTests.client.request('POST', '/api/calendar/preview', json={
            'dates': [{'label': 'Deadline', 'value': '20 Oct 2026', 'iso_date': '2026-10-20'}], 'title': 'Notice title'})
        self.assertEqual(response['events'][0]['title'], 'Notice title — Deadline')
        result = fixture_server.IntegrationTests.client.request('POST', '/api/calendar/ics', binary=True, json={
            'dates': [{'label': 'Deadline', 'value': '20 Oct 2026', 'iso_date': '2026-10-20'}],
            'confirmed': True, 'title': 'Notice title', 'notes': 'Confirmed instruction', 'event_namespace': 'test'})
        self.assertIn(b'SUMMARY:Notice title', result)
        self.assertIn(b'Confirmed instruction', result)
        self.assertIn(b'DTEND;VALUE=DATE:20261021', result)


if __name__ == '__main__':
    unittest.main()
