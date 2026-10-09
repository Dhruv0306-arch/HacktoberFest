# DeadLense Office document support

Apply this update to the existing integrated DeadLense checkout on `dhruv-ui`.
It preserves the existing UI, independent Demo sample, PDF/OCR reader, HTTP API,
confirmation, checklist downloads and calendar flow. No commit/push/branch switch
was performed.

## Install and start

Stop both services, extract the update ZIP into your existing project folder,
and install dependencies in the same virtual environment used to run the app:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-ui.txt -r requirements.txt
```

New dependencies: `python-docx>=1.2,<2`, `python-pptx>=1.0.2,<2`, `olefile==0.47`.
DOCX and PPTX do not need LibreOffice. Legacy PPT additionally needs a local
LibreOffice installation. Windows Program Files paths are automatically detected.
If necessary, configure the full executable path in both terminals before starting:

```powershell
$env:LIBREOFFICE_CMD = "C:\Program Files\LibreOffice\program\soffice.exe"
```

Backend terminal:

```powershell
$env:OLLAMA_HOST = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "gemma4:e4b"
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend terminal:

```powershell
$env:DEADLENSE_API_URL = "http://127.0.0.1:8000"
.\venv\Scripts\python.exe app.py
```

The prior NOTICEBRIDGE_API_URL setting remains compatible. Refresh the browser.

## How it works

The shared `office_reader.py` parses Word paragraphs/tables in document order,
including nested tables. Paragraph and table references are retained instead of
inventing Word pagination. PowerPoint slides remain in slide order, with slide
numbers, text boxes, tables and grouped text; shapes use approximate visual order.
Blank slides remain in sequence and are warned about. Documents with no readable
text produce an error and are not submitted to the model.

The file picker accepts DOCX/PPTX/PPT and TXT alongside all prior PDF/image types.
Selected filename, MIME and size display as before. Reading options -> Extract
text provides editable Office text in the existing Review area. Native rendered
Office page/slide images are not generated. Read document sends the original file
to `/api/analyze`, which locally extracts the text and passes its references to
Gemma. Evidence verification locates real source quotes within paragraphs/tables
or slides. The same editable facts, confirmation, checklist and calendar controls
are used for all formats. Source warnings are displayed alongside provenance.

Legacy PPT uses real OLE validation and local LibreOffice PPTX conversion, not
python-pptx directly. Missing LibreOffice produces instructions to install it or
save as PPTX. No cloud converter or separate backend was added. Conversion has a
60-second timeout and uses an isolated temporary profile with macros disabled;
VBA streams and modern Office macro parts are rejected. Temporary conversion files
are removed after processing; local reader conversion files live under the job's
cleanup directory. Backend parsing runs off the async event loop.

## Limits and limitations

- UI limit: 20 MB. Backend retains its configured 25 MB default.
- Presentations: 30 slides; model input: 90,000 characters.
- Office archives: 100 MB expanded, 5,000 parts, per-part expansion checks,
  duplicate/invalid parts and XML entity declarations rejected.
- Extension, Office MIME and container content are validated; corrupted,
  encrypted, empty and no-text documents return useful errors.
- Text inside images, charts, headers/footnotes, speaker notes and embedded objects
  is not extracted. Embedded objects/scripts are not executed. Export image-only
  content to PDF and use the existing OCR workflow.
- Complex visual reading order is approximate. Users still need to review facts.

## Files

Created: office_reader.py, tests/test_office_documents.py, three synthetic
Office fixtures in tests/fixtures, and this report.

Modified: document_reader.py, document_ui.py, backend_client.py, model_ui.py,
app/ingest.py, app/services/analyze.py, app/prompts.py, app/main.py,
requirements.txt, requirements-ui.txt, README.md, API.md, .env.example,
tests/test_document_reader.py, scripts/integration_smoke.py.

## Verification

Passed:

```text
python -m unittest discover -s tests -q   # 63 tests
python scripts/unit_test.py              # 20 checks
python scripts/integration_smoke.py      # actual Gradio/FastAPI HTTP services
python -m compileall -q app.py app office_reader.py document_reader.py document_ui.py model_ui.py tests scripts
python -m pip check                      # no broken requirements
```

Test coverage includes paragraphs + tables + nested tables, multiple slides and
blank slides, real legacy PPT conversion, missing converter errors, corrupt/
empty/no-text files, limits, unsafe archives, MIME/content mismatch, editable
local worker results and source references. HTTP tests verify the extracted text
reaches the model boundary, facts reach the UI, confirmation/checklist/calendar
work, and invalid documents do not invoke inference. PDF and image regression
checks and the explicit saved demo remain working.

Gemma inference was replaced ONLY inside tests by controlled model responses.
Real parsing, legacy conversion and Gradio/FastAPI connections were verified.
Live Gemma accuracy/performance and a visual browser inspection were not verified
in this environment. Run a real Office upload on your laptop before presenting.
