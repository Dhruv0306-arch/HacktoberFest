"""Session isolation, preservation on failure, page edits and source navigation."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from ui_session import WorkspaceSession
import document_ui as reader
import test_document_ui as demo_fixture

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceTests(unittest.TestCase):
    def test_separate_raw_drafts_and_stage_history(self):
        memory = WorkspaceSession()
        draft = {'primary': [' user edit ', None], 'rows': [['uncertain', '']]}
        memory.save_draft('Your document', draft)
        memory.remember_stage('Your document', 'review')
        memory.select_source('Demo sample')
        memory.save_draft('Demo sample', {'primary': ['Sample edit']})
        memory.remember_stage('Demo sample', 'results')
        for _ in range(5):
            self.assertEqual(memory.select_source('Your document'), 'review')
            self.assertEqual(memory.draft('Your document'), draft)
            self.assertEqual(memory.select_source('Demo sample'), 'results')
            self.assertEqual(memory.draft('Demo sample')['primary'], ['Sample edit'])
        returned = memory.draft('Your document'); returned['primary'][0] = 'external mutation'
        self.assertEqual(memory.draft('Your document'), draft)
        memory.reset('Your document')
        self.assertEqual(memory.draft('Your document'), {})
        self.assertTrue(memory.draft('Demo sample'))

    def test_new_browser_session_is_isolated(self):
        memory = WorkspaceSession(); memory.save_draft('Your document', {'text': 'private'})
        other = deepcopy(memory)
        self.assertEqual(other.draft('Your document'), {})
        self.assertEqual(memory.draft('Your document'), {'text': 'private'})

    def test_switch_source_updates_only_visibility_and_navigation(self):
        memory = WorkspaceSession()
        memory.remember_stage('Your document', 'results')
        memory.remember_stage('Demo sample', 'review')
        demo_fixture.app.switch_source('Demo sample', memory)
        self.assertTrue(memory.showing('Demo sample'))
        updates = demo_fixture.app.switch_source('Your document', memory)
        self.assertEqual(updates[8].selected, 'results')
        # It emits no fact/form/plan components that could erase fields.
        self.assertEqual(len(updates), 10)

    def test_demo_failure_is_non_destructive(self):
        returned = demo_fixture.app.read_notice(None, 'Clean notice')
        self.assertTrue(all(v == {'__type__': 'update'} for v in returned[:-1]))
        plan = demo_fixture.app.create_plan({}, 'English', '', False, False)
        self.assertTrue(all(v == {'__type__': 'update'} for v in plan[:-1]))


class ReaderPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.session = reader.ReaderSession()

    def tearDown(self):
        self.session.cancel()

    def read(self, path, previous=None):
        return list(reader.process_document(str(path), 'English', False, self.session, previous))[-1]

    def test_page_edits_and_preview_survive_candidate_and_failed_read(self):
        result = self.read(ROOT / 'tests/fixtures/multipage.pdf')[0]
        edited, text, payload, _, _ = reader.save_page(result, '1', 'Edited first page')
        previews = [Path(page['previewPath']) for page in edited['pages']]
        self.assertEqual(reader.load_page(edited, '1'), 'Edited first page')
        selected = reader.select_document(str(ROOT / 'fixtures/notice.png'), self.session, edited)
        self.assertEqual(selected[0], {'__type__': 'update'})
        self.assertEqual(selected[2], {'__type__': 'update'})
        self.assertTrue(all(p.exists() for p in previews))
        with tempfile.TemporaryDirectory() as folder:
            corrupt = Path(folder) / 'broken.docx'; corrupt.write_bytes(b'broken')
            failed = self.read(corrupt, edited)
        self.assertTrue(all(v == {'__type__': 'update'} for v in failed[:8]))
        self.assertEqual(reader.load_page(edited, '1'), 'Edited first page')
        self.assertTrue(all(p.exists() for p in previews))
        self.assertIn('Previous successful text', failed[-1])
        newer = self.read(ROOT / 'tests/fixtures/text.pdf', edited)[0]
        self.assertNotIn('Edited first page', newer['extractedText'])
        self.assertTrue(all(not p.exists() for p in previews))
        cleared = reader.reset_reader(self.session)
        self.assertIsNone(cleared[0]); self.assertEqual(cleared[1], {})

    def test_no_readable_text_keeps_prior_success(self):
        result = self.read(ROOT / 'tests/fixtures/text.pdf')[0]
        failed = self.read(ROOT / 'tests/fixtures/scanned.pdf', result)
        self.assertTrue(all(v == {'__type__': 'update'} for v in failed[:8]))
        self.assertIn('No readable text', failed[-1])
        self.assertTrue(Path(result['pages'][0]['previewPath']).exists())
