"""Regression checks for empty editable tables and JSON fallback contracts."""
import unittest
from unittest.mock import AsyncMock, patch

from app import ollama_client
from app.schemas import Notice, to_ollama_schema
from app.services.analyze import audit_structured_fields
from model_ui import TABLES, blank_row, add_table_row, notice_values, reviewed_notice, primary_values, apply_primary_edits, source_excerpt_text


class StructuredReviewTests(unittest.TestCase):
    def test_empty_rows_are_editable_but_never_become_facts(self):
        record = {'notice': Notice().model_dump()}
        values = notice_values(record)
        for rows, columns in zip(values[10:16], TABLES.values()):
            self.assertEqual(rows, [blank_row(columns)])
        edited = reviewed_notice(record, values)
        for field in TABLES:
            self.assertEqual(edited[field], [])
        values[10] = [[None] * 5]
        self.assertEqual(reviewed_notice(record, values)['dates'], [])

    def test_manual_date_and_required_action_round_trip(self):
        record = {'notice': Notice().model_dump()}
        values = notice_values(record)
        values[10] = [['Deadline', '20 October 2026', '2026-10-20', '', 'pasted text']]
        values[12] = [['Pay fee', 'Bring student ID', True, 'pasted text']]
        notice = reviewed_notice(record, values)
        self.assertEqual(notice['dates'][0]['iso_date'], '2026-10-20')
        self.assertTrue(notice['action_items'][0]['required'])
        self.assertEqual(len(add_table_row(values[10], TABLES['dates'])), 2)

    def test_prose_only_fields_flagged_without_inventing_rows(self):
        raw = Notice(summary='Pay INR 2500 by 20 October 2026.', required_documents=['Student ID']).model_dump()
        notice = Notice.model_validate(raw)
        audit_structured_fields(notice, raw)
        self.assertEqual({item.field for item in notice.missing}, {'dates', 'fees', 'action_items'})
        self.assertEqual(notice.dates, [])
        self.assertEqual(notice.fees, [])
        self.assertEqual(notice.action_items, [])

    def test_genuinely_absent_fields_do_not_gain_facts(self):
        raw = Notice(title='Holiday', summary='Office closed.').model_dump()
        notice = Notice.model_validate(raw)
        audit_structured_fields(notice, raw)
        self.assertEqual(notice.missing, [])
        self.assertEqual(notice.dates, [])


class DemoPresentationTests(unittest.TestCase):
    def record(self):
        return {'notice': Notice(title='Real notice', issuing_body='Real issuer',
            dates=[{'label': 'Event', 'value': '10 Oct 2026', 'iso_date': '2026-10-10'},
                   {'label': 'Payment deadline', 'value': '20 Oct 2026', 'iso_date': '2026-10-20'}],
            fees=[{'amount': '2500', 'currency': 'INR'}, {'amount': '100', 'currency': 'INR'}],
            action_items=[{'step': 'Pay', 'required': True}, {'step': 'Optional visit', 'required': False}],
            required_documents=['Student ID'], optional_documents=['Certificate']).model_dump()}

    def test_projection_uses_real_primary_deadline_and_preserves_other_facts(self):
        record = self.record()
        values = primary_values(record)
        self.assertEqual(values[:3], ['Real notice', 'other', 'Real issuer'])
        self.assertEqual(values[3], '2026-10-20')
        self.assertEqual(values[5], 'INR 2500')
        updated = apply_primary_edits(record, reviewed_notice(record, notice_values(record)), values)
        self.assertEqual(updated, record['notice'])

    def test_summary_edits_change_only_selected_nested_details(self):
        record = self.record()
        values = primary_values(record)
        values[3] = '2026-10-21'
        values[5] = 'INR 2600'
        updated = apply_primary_edits(record, reviewed_notice(record, notice_values(record)), values)
        self.assertEqual(updated['dates'][0], record['notice']['dates'][0])
        self.assertEqual(updated['dates'][1]['iso_date'], '2026-10-21')
        self.assertEqual(updated['fees'][0]['amount'], '2600')
        self.assertEqual(updated['fees'][1], record['notice']['fees'][1])
        self.assertFalse(updated['action_items'][1]['required'])
        self.assertEqual(updated['optional_documents'], ['Certificate'])

    def test_missing_fields_stay_empty_and_manual_dates_require_valid_format(self):
        record = {'notice': Notice(title='No deadline').model_dump()}
        values = primary_values(record)
        self.assertEqual(values[3:6], ['', '', ''])
        unchanged = apply_primary_edits(record, reviewed_notice(record, notice_values(record)), values)
        self.assertEqual(unchanged['dates'], [])
        self.assertEqual(unchanged['fees'], [])
        values[3] = '2026-02-30'
        with self.assertRaises(ValueError):
            apply_primary_edits(record, unchanged, values)
        self.assertIn('No source excerpts', source_excerpt_text(record))



class JsonFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_contains_schema_and_preserves_original_messages(self):
        messages = [{'role': 'system', 'content': 'Read the notice.'}, {'role': 'user', 'content': 'Input'}]
        schema = to_ollama_schema(Notice)
        chat = AsyncMock(side_effect=[ollama_client.OllamaError('schema unsupported'), '{"title":"Test"}'])
        with patch.object(ollama_client, 'chat', chat):
            result = await ollama_client.structured(messages, schema)
        self.assertEqual(result['title'], 'Test')
        fallback = chat.call_args_list[1].args[0]
        self.assertIn('"dates"', fallback[0]['content'])
        self.assertIn('"fees"', fallback[0]['content'])
        self.assertIn('"action_items"', fallback[0]['content'])
        self.assertEqual(messages[0]['content'], 'Read the notice.')


if __name__ == '__main__':
    unittest.main()
