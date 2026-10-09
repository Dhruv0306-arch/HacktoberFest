# Read Document: match the demo experience

This update applies to the existing integrated DeadLense project on `dhruv-ui`.

## Reference inspected

The demo in root `app.py` uses the existing `ui_style.py` theme, surface cards, stage introductions, and Upload → Review → Results navigation. Its upload is a 4/6 two-column layout; review uses compact paired fields in a 6-column card and source excerpts/confirmation in a 4-column card. Results use an explanation/checklist card beside a downloads/edit card. The synthetic data is in `samples/demo_notice.json`.

The previous real workflow instead emphasized OCR controls, large backend-schema tables, and JSON outputs. These caused a visibly different experience even though it shared the theme.

## Differences fixed

- Upload now has a primary **Read document** button and explanation language in the same left-hand control column as the file picker, beside the existing preview.
- Local PDF text extraction/OCR remains under Reading options; page editing and reader JSON exports remain in collapsed Review/Results sections.
- Review uses the same demo stage heading, paired title/category/issuer/date/amount fields, multiline audience/actions/documents, and source-excerpt card with a **Build my action plan** confirmation button.
- Backend source references and quote-verification status remain available. All dates/fees, optional documents/actions, contacts, links, uncertainty and clarification fields remain editable under Detailed fields.
- Results use the same demo heading, **What this notice means**, checklist card, **Keep your plan**, downloads, and **Edit the details** navigation. Required and optional backend actions remain separately labelled.
- Main fields are projected only from the actual backend response. The main date prefers a labelled deadline when available. Unknown values stay blank. No date/amount is parsed out of the model's prose to manufacture a fact.
- Main-field edits map back to the nested Notice schema before the existing backend PATCH/checklist calls. Additional dates/fees and optional details remain intact when unchanged.
- If both a main field and its advanced counterpart are edited, changed main fields take precedence; the UI explains this rule.
- Empty/loading/error states clear stale data. Old confirmed plans and downloads are invalidated after edits.

No new dependencies or styling system. The existing theme, responsive columns, min-widths and mobile CSS breakpoint are reused. No sample data fallback was added, and the sample adapter itself was not changed.

## Changed files

- `document_ui.py`: preserve OCR/upload/preview tools while collapsing detailed reader sections; expose the existing upload-control column for real workflow controls.
- `model_ui.py`: demo-aligned cards, primary-field projection/edit mapping, evidence excerpts, and navigation.
- `tests/test_structured_review.py`: projection, missing values, nested-data preservation, manual edits and date-validation regression checks.
- `scripts/integration_smoke.py`: drives the new primary fields and verifies a corrected main date reaches calendar preview through real UI/backend HTTP calls.

## Verification

- `python -m unittest discover -s tests -v`: **43 tests passed**.
- `python scripts/integration_smoke.py`: real uploaded PDF and image → real backend ingestion → controlled test inference → field presentation → user edits → PATCH → checklist → directory/download/calendar flow passed. The main-date correction also reached the calendar preview.
- `python -m compileall -q document_ui.py model_ui.py`: passed.
- `python -m pip check`: no broken requirements.
- Existing sample, reader, OCR, cancellation, malformed-input and API-error checks remain passing.

Model inference in these tests is explicitly mocked. Live Gemma inference could not be checked without an Ollama service here. No browser screenshot inspection was available; responsive behavior is inherited from the demo's existing layout/CSS rather than independently visually verified.

## Apply

Stop the backend and frontend with Ctrl+C. Extract this update into the existing project root on `dhruv-ui`, replacing only the included files. Back up any additional local edits you have made to these same files since supplying the source ZIP.

Backend terminal:

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend terminal:

```powershell
.\venv\Scripts\python.exe app.py
```

No dependency reinstall is needed. Keep existing environment/model settings. Refresh the browser and analyze the uploaded notice again.

No branch switch, commit or push was performed.
