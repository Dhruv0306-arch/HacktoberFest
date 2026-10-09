from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import document_reader as reader

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures'
try:
    reader.tesseract_path()
    HAS_OCR = True
except reader.DocumentError:
    HAS_OCR = False


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def read(self, filename, **kwargs):
        return reader.read_document(FIXTURES / filename, self.folder / 'out', **kwargs)

    def test_text_pdf(self):
        result = self.read('text.pdf')
        self.assertEqual(result['extractionStatus'], 'success')
        self.assertEqual(result['extractionMethod'], 'pdf_text')
        self.assertIn('INR 2500', result['extractedText'])
        self.assertFalse(result['modelInferencePerformed'])
        self.assertTrue(Path(result['pages'][0]['previewPath']).is_file())

    def test_multipage_order(self):
        result = self.read('multipage.pdf')
        self.assertEqual(result['pageCount'], 2)
        self.assertLess(result['extractedText'].index('PAGE ONE'), result['extractedText'].index('PAGE TWO'))
        self.assertIn('--- Page 2 ---', result['extractedText'])

    def test_scan_requires_ocr(self):
        result = self.read('scanned.pdf')
        self.assertEqual(result['extractionStatus'], 'ocr_required')
        self.assertFalse(result['hasReadableText'])

    def test_blank_pdf_not_success(self):
        result = self.read('blank.pdf')
        self.assertEqual(result['extractionStatus'], 'ocr_required')
        self.assertFalse(result['hasReadableText'])

    def test_password_pdf(self):
        with self.assertRaisesRegex(reader.DocumentError, 'password-protected'):
            self.read('protected.pdf')

    def test_corrupt_and_empty(self):
        for name, data in [('corrupt.pdf', b'%PDF-1.7\ninvalid'), ('empty.pdf', b''),
                           ('fake.pdf', b'hello'), ('bad.png', b'not an image')]:
            target = self.folder / name
            target.write_bytes(data)
            with self.subTest(name=name), self.assertRaises(reader.DocumentError):
                reader.read_document(target, self.folder / 'out')

    def test_unsupported_and_oversized(self):
        target = self.folder / 'notice.txt'
        target.write_text('hello')
        with self.assertRaisesRegex(reader.DocumentError, 'Unsupported'):
            reader.file_info(target)
        with patch.object(reader, 'MAX_BYTES', 10), self.assertRaisesRegex(reader.DocumentError, 'limit'):
            reader.file_info(FIXTURES / 'text.pdf')

    def test_page_limit(self):
        with patch.object(reader, 'MAX_PAGES', 1), self.assertRaisesRegex(reader.DocumentError, 'limit'):
            self.read('multipage.pdf')

    def test_image_pixel_limit(self):
        with patch.object(reader, 'MAX_PIXELS', 100), self.assertRaisesRegex(reader.DocumentError, 'pixel'):
            reader.read_document(ROOT / 'samples/fee_notice.png', self.folder / 'out')

    def test_missing_ocr_installation(self):
        with patch.object(reader, 'tesseract_path', side_effect=reader.DocumentError('Tesseract missing')):
            with self.assertRaisesRegex(reader.DocumentError, 'missing'):
                reader.read_document(ROOT / 'samples/fee_notice.png', self.folder / 'out')
            # Scanned PDF retains a useful partial result rather than claiming success.
            result = self.read('scanned.pdf', ocr_scans=True)
            self.assertEqual(result['extractionStatus'], 'ocr_required')
            self.assertIn('missing', ' '.join(result['warnings']))

    def test_corrected_payload_preserves_original(self):
        result = self.read('text.pdf')
        original = deepcopy(result)
        corrected = reader.apply_page_edits(result, ['Corrected fee: INR 2700'])
        self.assertIn('2700', corrected['extractedText'])
        self.assertIn('2500', corrected['pages'][0]['originalText'])
        self.assertTrue(corrected['edited'])
        self.assertNotIn('previewPath', corrected['pages'][0])
        self.assertEqual(result, original)

    def test_manual_text_does_not_claim_successful_ocr(self):
        result = self.read('scanned.pdf')
        corrected = reader.apply_page_edits(result, ['Manually transcribed notice'])
        self.assertTrue(corrected['hasReadableText'])
        self.assertEqual(corrected['extractionStatus'], 'ocr_required')
        self.assertFalse(corrected['modelInferencePerformed'])

    @unittest.skipUnless(HAS_OCR, 'Install Tesseract with English data to run real OCR tests')
    def test_real_image_ocr(self):
        result = reader.read_document(ROOT / 'samples/fee_notice.png', self.folder / 'out')
        self.assertEqual(result['extractionStatus'], 'success')
        self.assertIn('2500', result['extractedText'])
        self.assertIn('student ID', result['extractedText'])

    @unittest.skipUnless(HAS_OCR, 'Install Tesseract with English data to run real OCR tests')
    def test_real_scanned_pdf_ocr(self):
        result = self.read('scanned.pdf', ocr_scans=True)
        self.assertEqual(result['extractionStatus'], 'success')
        self.assertEqual(result['extractionMethod'], 'image_ocr')
        self.assertIn('2500', result['extractedText'])

    @unittest.skipUnless(HAS_OCR, 'Install Tesseract with English data to run real OCR tests')
    def test_blank_image_no_fake_success(self):
        path = self.folder / 'blank.png'
        Image.new('RGB', (640, 480), 'white').save(path)
        result = reader.read_document(path, self.folder / 'out')
        self.assertEqual(result['extractionStatus'], 'no_text')
        self.assertFalse(result['hasReadableText'])


if __name__ == '__main__':
    unittest.main()
