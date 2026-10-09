from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import document_ui as ui
import app
import ui_utils

ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.session = ui.ReaderSession()

    def tearDown(self):
        self.session.cancel()

    def test_worker_to_ui_and_page_edit(self):
        events = list(ui.process_document(str(ROOT / 'tests/fixtures/multipage.pdf'), 'English', False, self.session))
        result = events[-1][0]
        self.assertEqual(result['pageCount'], 2)
        updated, combined, payload, download, status = ui.save_page(result, '2', 'Corrected second page')
        self.assertIn('PAGE ONE', combined)
        self.assertIn('Corrected second page', combined)
        self.assertEqual(ui.load_page(updated, '2'), 'Corrected second page')
        self.assertFalse(payload['modelInferencePerformed'])
        self.assertIsNone(download)

    def test_cancellation_discards_old_job(self):
        stream = ui.process_document(str(ROOT / 'tests/fixtures/scanned.pdf'), 'English', True, self.session)
        next(stream)
        job = self.session.job
        self.session.cancel()
        self.assertEqual(list(stream), [])
        self.assertIsNotNone(job.process.poll())

    def test_replacement_clears_stale_result(self):
        stream = ui.process_document(str(ROOT / 'tests/fixtures/text.pdf'), 'English', False, self.session)
        next(stream)
        selected = ui.select_document(str(ROOT / 'samples/fee_notice.png'), self.session)
        self.assertEqual(selected[0], {})
        self.assertEqual(selected[4], '')
        self.assertEqual(list(stream), [])

    def test_clear_and_error_states(self):
        self.assertEqual(ui.select_document(None, self.session)[0], {})
        result = list(ui.process_document(None, 'English', False, self.session))[-1]
        self.assertEqual(result[0], {})
        self.assertIn('Could not start', result[-1])

    def test_reader_actions_wait_for_extraction(self):
        empty = ui.reader_availability({})
        self.assertTrue(all(note.visible for note in empty[:2]))
        self.assertTrue(all(not button.interactive for button in empty[2:5]))
        self.assertFalse(empty[-1].visible)
        events = list(ui.process_document(str(ROOT / 'tests/fixtures/text.pdf'), 'English', False, self.session))
        available = ui.reader_availability(events[-1][0])
        self.assertTrue(all(not note.visible for note in available[:2]))
        self.assertTrue(all(button.interactive for button in available[2:5]))
        self.assertIn('review it before use', available[-1].value)
        # Clearing/replacing a document must restore the empty state.
        cleared = ui.reader_availability(ui.select_document(None, self.session)[0])
        self.assertTrue(cleared[0].visible)
        self.assertFalse(cleared[4].interactive)

    def test_results_do_not_claim_unreadable_pages_are_complete(self):
        events = list(ui.process_document(str(ROOT / 'tests/fixtures/scanned.pdf'), 'English', False, self.session))
        result = events[-1][0]
        summary = ui.reader_availability(result)[-1].value
        self.assertIn('No selectable text', summary)
        self.assertIn('Warnings:', summary)
        self.assertNotIn('Text extracted', summary)
        # Manual correction and export remain available even when OCR is required.
        self.assertTrue(ui.reader_availability(result)[2].interactive)

    def test_source_navigation_preserves_separation(self):
        real = app.switch_source('Your document')
        sample = app.switch_source(app.SAMPLE_SOURCE)
        self.assertTrue(all(group.visible for group in real[:4]))
        self.assertTrue(all(not group.visible for group in real[4:8]))
        self.assertTrue(all(not group.visible for group in sample[:4]))
        self.assertTrue(all(group.visible for group in sample[4:8]))
        self.assertEqual(real[8].selected, 'upload')
        self.assertEqual(sample[8].selected, 'upload')

    def test_original_mock_workflow_unchanged(self):
        extracted = app.read_notice(app.sample_path('Clean notice'), 'Clean notice')
        self.assertEqual(len(extracted), len(app.extraction_outputs))
        self.assertEqual(extracted[0]['amount'], 'INR 2500')
        values = extracted[1:1 + len(app.EDIT_KEYS)]
        with tempfile.TemporaryDirectory() as folder, patch.object(ui_utils, 'BASE', Path(folder)):
            generated = app.create_plan(extracted[0], 'English', '', True, True, *values)
            self.assertEqual(len(generated[2]), 2)
        self.assertEqual(app.invalidate()[:2], (False, False))
        self.assertEqual(app.read_notice(None, 'Clean notice')[0], {})


if __name__ == '__main__':
    unittest.main()
