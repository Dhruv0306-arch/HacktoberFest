# NoticeBridge

Read it. Verify it. Act on it.

Buildathon PS 1 prototype: community notice image -> reviewed facts -> explanation, checklist and optional calendar file.

## Run on Windows (PowerShell)

Python 3.10+ and an existing `venv` are required. If needed, create it with `py -m venv venv`.

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-ui.txt
$env:NOTICEBRIDGE_MODE = "demo"
.\venv\Scripts\python.exe app.py
```

Open the local URL printed in the terminal. Ctrl+C stops the app.

## Demo versus live

Demo mode deliberately uses labelled, saved synthetic fixtures. It does not read or translate images. Select either clean or unclear-deadline sample. Demo output is English only. All demo calendar exports have a DEMO prefix.

For live mode, place Utkarsh's `model.py` and its supporting files beside `app.py`. Install his backend dependencies. His functions must be synchronous and return Python dictionaries:

```python
extract_notice(image_path: str) -> dict
build_guidance(confirmed_fields: dict, language: str) -> dict
```

Extraction fields: see `samples/demo_notice.json`. Scalar fields accept text or null. List fields accept lists of strings. `build_guidance` returns `{"explanation": "...", "checklist": ["..."]}`. Use only corrected facts, preserve dates/currency/names, and retain unresolved items. The adapter is the only file to change if his function names differ.

```powershell
$env:NOTICEBRIDGE_MODE = "live"
.\venv\Scripts\python.exe app.py
```

Live failures are displayed; they never silently fall back to fixtures. Live languages are options to test, not verified quality claims. Offline capability depends on the full backend and dependencies; test with network disconnected before claiming it.

## Ownership

- Dhruv: app.py, ui_adapter.py, ui_utils.py, requirements-ui.txt, samples, helplines.csv, README.
- Utkarsh: model.py, schemas, inference configuration and backend dependencies.

## Verified resources

`helplines.csv` is intentionally header-only. Add only manually verified official resources, including exact category, region, HTTPS URL and verification date (YYYY-MM-DD). Leave unavailable phone numbers blank. Lookup requires exact category and explicit region; it never guesses a helpline. Presence of a date is not automatic verification.

## Demo walkthrough

1. Read the clean synthetic notice and compare source excerpts with the image.
2. Confirm the details and date, generate a plan and download its TXT and all-day ICS.
3. Change the amount; the old plan clears and confirmation resets. Generate again and check the updated amount.
4. Select the unclear-deadline sample. It must show missing date and unresolved information; calendar export remains unavailable.
5. Repeat with actual image inference in live mode before submission. Do not present fixture playback as AI extraction.

## Limits

The original model/demo workflow accepts images only; the independent document reader below additionally accepts PDFs. No automatic form submission, payment or message sending. Source excerpts are model outputs and should be compared with the original. Human confirmation records review, not a guarantee of accuracy. Checklist progress is session-only. Calendar files require a valid verified date and no remaining unclear items. Dates are all-day events, with no inferred time zone or time. Exports are stored under ignored `outputs/`; delete after the demo when no longer needed. No real helplines are bundled.

## Real document reader (works even in demo mode)

The new panel at the top of the app reads your actual files. The existing saved demo/model panel remains separate and unchanged. The reader never calls `ui_adapter.py`, `model.py`, an OCR website or a prediction API. File bytes travel from the browser to this Python application, which is localhost by default; this is local-host processing, not browser-only processing. Do not enable public hosting for sensitive documents without designing appropriate access controls.

### Install / launch on Windows

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-ui.txt
winget install --exact --id UB-Mannheim.TesseractOCR
$env:NOTICEBRIDGE_MODE = "demo"
.\venv\Scripts\python.exe app.py
```

Tesseract is only needed for image OCR and optional scanned-PDF OCR. Text-based PDFs and their previews work without it. If winget is unavailable, follow the Windows installer link in the [official Tesseract instructions](https://tesseract-ocr.github.io/tessdoc/Installation.html). The app checks PATH and common Windows installation directories. For a custom location:

```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
& $env:TESSERACT_CMD --list-langs
.\venv\Scripts\python.exe app.py
```

English requires `eng` data. Hindi + English requires `hin` and `eng`; Kannada + English requires `kan` and `eng`. Install language packs separately in Tesseract's tessdata directory; the app never downloads language packs or uploads documents automatically. OCR supports printed text best; multilingual quality needs local testing.

### Use

1. Drag/drop or select a PDF, PNG, JPG/JPEG, WEBP, BMP, TIFF or GIF in **Read a real document**. Filename, MIME type and size appear immediately. Images preview immediately; PDF page previews appear after reading.
2. Select an installed OCR language. Enable **Also OCR PDF pages without selectable text** only when needed. Text PDFs use the text layer without OCR by default.
3. Click **Extract readable text / Retry**. Progress reports completed PDF pages and OCR stages; it does not fabricate Tesseract recognition percentages.
4. Select a page, correct its text, and click **Save this page's corrections** before changing pages or exporting.
5. Review the combined text and JSON. Download the saved JSON for future integration. No model predictions are generated from this text yet.
6. Cancel stops the reader process and its OCR child process. Removing/replacing a file cancels pending work and clears prior reader output. The mock output panel is unaffected.

### Integration boundary

`document_reader.py` is independent of Gradio and model inference:

```python
from document_reader import read_document, apply_page_edits

result = read_document(image_or_pdf_path, temporary_work_directory,
                       language="eng", ocr_scans=False)
payload = apply_page_edits(result, corrected_text_for_each_page)
# Future integration may consume payload; no API is assumed or called today.
```

PDFium is not thread-safe: call `read_document` in a separate process when processing concurrent requests. `document_ui.py` already does this and handles cancellation/timeouts.

Payload fields: `fileName`, `fileType`, `fileSize`, `schemaVersion`, `pageCount`, `pages`, `extractedText`, `extractionMethod`, `extractionStatus`, `hasReadableText`, `warnings`, `edited`, `reviewStatus`, `modelInferencePerformed`.

Each page retains `pageNumber`, `originalText`, editable `text`, `extractionMethod`, `extractionStatus`, and `edited`. Downloaded JSON excludes internal preview paths. Original extraction status remains unchanged after manual edits, so manual transcription never masquerades as successful OCR. `hasReadableText` reflects current edited content. Model inference is always explicitly false.

### Limits and storage

Defaults are configurable through environment variables before starting the app:

| Variable | Default |
| --- | --- |
| `NOTICEBRIDGE_MAX_FILE_MB` | 20 MB |
| `NOTICEBRIDGE_MAX_PAGES` | 30 pages |
| `NOTICEBRIDGE_MAX_IMAGE_PIXELS` | 25,000,000 pixels |
| `NOTICEBRIDGE_MAX_TEXT_CHARS` | 500,000 characters |
| `NOTICEBRIDGE_OCR_TIMEOUT` | 60 seconds per image/page |
| `NOTICEBRIDGE_READ_TIMEOUT` | 180 seconds per document |

Oversized files, zero-byte files, corrupt documents, protected PDFs and unsupported formats report errors. Upload an authorized unlocked copy of protected PDFs. A page with no readable text is marked `ocr_required` or `no_text`, never success. Mixed PDFs may return `partial` with page-level warnings. Blank pages may also be flagged as needing OCR because text visibility cannot be inferred reliably from an empty text layer. Only the first frame of animated GIF/multipage TIFF is processed, with a warning. Complex columns/tables can have imperfect reading order within a page, while page order is preserved. No handwriting or OCR accuracy guarantee. PDF render dimensions are bounded to control memory.

Temporary previews/OCR files live under `outputs/document-reader/`; replacing/removing/cancelling the document cleans its work directory. Reader session state expires after one hour. Explicit JSON downloads live under `outputs/document-reader-exports/` until you delete them. Gradio also maintains its normal upload/download cache. Existing `outputs/` files are not modified or deleted by this update.

### Checks

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
.\venv\Scripts\python.exe -m compileall -q app.py ui_adapter.py ui_utils.py document_reader.py document_ui.py tests
```

Tests include real PDF text extraction, multi-page order, scanned/blank/protected PDFs, actual image and scanned-PDF OCR, invalid/empty/oversized files, edit provenance, cancellation/replacement and the original mock checklist/calendar workflow. OCR tests are skipped if Tesseract is unavailable. The small fixtures are synthetic; `protected.pdf` uses password `test-password`. The existing project has no lint, static type-check or frontend build configuration.

Implementation references: [PDFium Python API and process-safety guidance](https://pypdfium2.readthedocs.io/en/stable/python_api.html), [Tesseract CLI](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html).
