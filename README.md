# DeadLense

Read it. Verify it. Act on it.

A local Gradio UI connected to Utkarsh's FastAPI backend and Ollama (`gemma4:e4b`). Upload a notice, review its extracted facts and source evidence, confirm corrections, and generate an action checklist. The saved sample walkthrough remains explicitly separate.

## Start on Windows / PowerShell

Use Python 3.10+; integration was tested with Python 3.12. From the repository root, check the branch without changing it:

```powershell
git branch --show-current
```

It should print `dhruv-ui`. Keep your current branch and uncommitted work.

Install **both** sets of requirements into the same environment. If you already use `venv`, keep it:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-ui.txt -r requirements.txt
```

If you do not have a virtual environment, first run `py -m venv venv`.

Terminal 1 — backend (leave running):

```powershell
$env:OLLAMA_HOST = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "gemma4:e4b"
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal 2 — frontend (from the same project folder):

```powershell
$env:DEADLENSE_API_URL = "http://127.0.0.1:8000"
.\venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:7860. Backend API docs: http://127.0.0.1:8000/docs.

Keep your already-installed Ollama service running. `ollama list` should contain the exact `gemma4:e4b` tag. Use **Check backend connection** in the UI. No `model.py` is required: the supplied backend is the `app/` package, while root `app.py` is the UI launcher. Do not run `python -m app`.

On macOS/Linux, activate your environment and use `python -m pip install -r requirements-ui.txt -r requirements.txt`, `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`, and `python app.py` in separate terminals. `run.sh` remains the backend-only helper supplied by Utkarsh.

## Real workflow

1. Choose **Your document**. Upload a PDF/image, or expand **Or paste notice text**.
2. For an image or text PDF, click **Analyze uploaded notice with Gemma**. Images go directly to the local model; PDFs use their selectable text. You can also use **Extract text** for page previews and local OCR first.
3. For scanned PDFs, enable **OCR PDF pages without selectable text**, click **Extract text**, review and save every page, then click **Analyze saved text with Gemma**. Missing PDF text produces an explicit OCR instruction, not guessed model facts.
4. On **Review**, inspect title, issuer, summary, audience, dates, fees, required/optional documents, actions, contacts, links, and uncertainties. Tables retain multiple dates and original source references. Leave unknown dates blank.
5. Compare original evidence with the document. `verified=true` means a quote was found in the text, not that the model's interpretation is correct. Image quotes cannot be automatically verified. Evidence remains labelled as original extraction evidence after corrections.
6. Check the review box, then **Confirm facts & generate action plan**. The UI PATCHes corrected facts to the existing analysis and requests a fresh checklist from those stored facts. It never presents the initial draft checklist as a confirmed plan.
7. On **Results**, tick required/optional actions, download the plain-text checklist, and optionally preview dates. Calendar download requires a second, explicit date confirmation. Blank or invalid dates are omitted with warnings; all-day end dates are exclusive.
8. Directory matches include source metadata and last-checked timestamps. **The bundled campus directory is fictional demo data, not a verified official directory.** Replace `data/directory.json` with checked information before using it for real recommendations. The sample's separate `helplines.csv` remains header-only until you add verified entries.

Edits and replacements invalidate previous results and late model responses. Model requests are serialized, with duplicate submissions limited. Replacing input discards its pending result but does not cancel computation already running in Ollama. Use smaller documents if inference times out. The reader's existing Cancel button stops local OCR separately.

## Demo mode remains available

Choose **Demo sample** to use `samples/demo_notice.json` and the clean/unclear synthetic images. This workflow is English-only and produces labelled demo files. It never runs automatically when real processing fails. The legacy `DEADLENSE_MODE` variable no longer controls real inference: real processing always uses **Your document**, and samples always remain samples.

## Local OCR (optional)

The frontend's existing document reader uses PDFium and Tesseract. Selectable PDF text and direct Gemma image analysis do not require Tesseract. Image OCR and scanned-PDF OCR do.

On Windows, install Tesseract if needed:

```powershell
winget install --exact --id UB-Mannheim.TesseractOCR
```

Restart your terminal so PATH updates take effect, or configure `TESSERACT_CMD` to the installed executable. Install the relevant Tesseract language data for Hindi/Kannada OCR. A missing language pack produces an error rather than fake OCR text. These OCR language choices are separate from the model's requested English/Hindi output language.

## Configuration

Environment variables are read by the relevant Python process. `.env.example` documents defaults; `.env` is not loaded automatically by `python app.py`.

| Variable | Default | Purpose |
|---|---|---|
| `DEADLENSE_API_URL` | `http://127.0.0.1:8000` | Frontend's backend address |
| `DEADLENSE_API_TIMEOUT` | `900` seconds | Frontend HTTP read timeout |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Backend model server |
| `OLLAMA_MODEL` | `gemma4:e4b` | Exact installed model tag |
| `OLLAMA_TIMEOUT` | See `app/config.py` | Backend inference timeout; retain supplied defaults unless needed |
| `APP_DATA_DIR` | `data/` | Backend directory and saved analysis location |
| `DEADLENSE_CORS_ORIGINS` | localhost ports 7860 | Comma-separated allowed browser origins |

Gradio makes server-side HTTP requests, so its integration does not need browser cross-origin requests. Both services bind to loopback in the commands above. Documents are not sent to an external OCR service. Changing either host to a remote service changes where content is sent; configure this deliberately.

Files are limited to 20 MB at the frontend (backend 25 MB), PDFs to 30 pages, images to 25 million pixels, and backend text to 90,000 characters. Large or unsupported inputs produce errors. Multi-frame images must be split before direct model analysis. Local reader JSON keeps original text, edits, page order and extraction status. Backend analyses are saved in ignored `data/analyses/`; exports/reader artifacts live in ignored `outputs/`. These local files persist until you remove them. No authentication or production hosting is included.

## Architecture and ownership

- Dhruv's UI: `app.py`, `ui_style.py`, `document_ui.py`, `model_ui.py`.
- Local file reading/OCR: `document_reader.py`.
- Real backend HTTP adapter: `backend_client.py`, preserving Utkarsh's nested Notice schema.
- Utkarsh's backend: `app/main.py`, `app/ingest.py`, `app/ollama_client.py`, `app/schemas.py`, `app/services/`, `app/store.py`.
- Saved sample adapter: `ui_adapter.py`; sample exports/resources: `ui_utils.py`.

API contracts and the integration changes are documented in `API.md` and `INTEGRATION_REPORT.md`.

## Verification

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
.\venv\Scripts\python.exe scripts/unit_test.py
.\venv\Scripts\python.exe scripts/integration_smoke.py
.\venv\Scripts\python.exe -m compileall -q app.py backend_client.py model_ui.py document_ui.py app
.\venv\Scripts\python.exe -m pip check
```

Integration tests start a real FastAPI HTTP server and replace **only model inference** with explicit test fixtures. The smoke test starts the real Gradio UI and drives its upload/review/confirmation/download APIs over HTTP. Test fixtures never enter production request handling. Existing reader tests exercise real Tesseract OCR when installed.

Live Gemma inference, Hindi output quality, and a visual browser inspection were not verified in the integration environment. No Ollama instance was listening there. Test one real notice on your laptop before presenting the demo. Human review reduces errors but does not establish model accuracy; unresolved details remain visible rather than becoming invented deadlines or contacts.

Existing `NOTICEBRIDGE_*` environment settings remain supported for compatibility. When both names are set, `DEADLENSE_*` takes precedence.
