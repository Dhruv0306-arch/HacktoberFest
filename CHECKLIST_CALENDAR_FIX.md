# Checklist and calendar fixes

Apply to the existing integrated NoticeBridge project on `dhruv-ui`. No branch switch, commit or push was performed.

## Why Markdown was visible

The backend can include Markdown in checklist summaries, step titles and details. Gradio Textbox and CheckboxGroup labels display those strings literally; they do not render Markdown. The UI also offered a raw `.md` download.

The UI now converts Markdown through Gradio's existing Markdown parser before displaying summaries and checkbox labels. Heading/emphasis/code syntax is removed while words, links, order and requirement flags remain. Markdown-only checklist responses are adapted into actual checkbox items; unsupported requirement classification is labelled “Not classified” rather than invented. Non-task notes remain in the summary. Empty checklists have an explicit message. Downloads are now plain-text `.txt` files, generated from the actual backend download response. The original backend Markdown endpoint remains compatible with existing callers.

## Calendar issues found and fixed

The supplied screenshot was not readable in the environment, so its precise symptom could not be inspected. Code inspection identified these actual issues:

- Notice title was ignored in ICS SUMMARY; only generic date labels appeared.
- New random event IDs were generated for every export, encouraging duplicate imports.
- Invalid/reversed date ranges could silently become single-day events.
- Missing or uncertain dates had no clear user-facing preview state; the UI exposed raw JSON and allowed premature export attempts.
- Export read backend analysis dates again rather than using the exact date snapshot the user previewed.

Fixes:

- A readable preview table shows event titles, start/end dates and source references. Dedicated calendar status displays warnings, loading, empty, failure and success states.
- Confirmation and export stay disabled until usable dates have been previewed. Export enables after explicit date confirmation. Editing facts invalidates the preview/download.
- Missing, impossible, reversed and out-of-range dates are omitted with warnings; date-related uncertainty flags block affected notice dates until resolved on Review.
- Export uses only the date events the user previewed and confirmed through the actual backend ICS endpoint.
- ICS titles include the notice title and date label. Descriptions preserve confirmed notice summary, original date/time wording and source reference.
- Duplicate date rows are omitted. Stable event UIDs are retained across identical exports. Submission serialization and existing duplicate-click protection remain.
- All-day DTEND is the exclusive next day, including ranges. Escaping, Unicode line folding and CRLF formatting remain valid.

The backend schema supplies calendar dates, not structured times/timezones. Exports therefore remain **all-day**; any time in the original wording stays in the description. No missing year, time or timezone is inferred. No Google Calendar link existed or was added; download the ICS and import it into your calendar application.

## Files changed

- `presentation_utils.py` (new): Markdown-to-readable-text conversion and Markdown checklist adaptation, using an existing Gradio dependency.
- `model_ui.py`: clean checklist labels/summary, plain-text download, empty checklist message, readable calendar preview and confirmation/export gating.
- `backend_client.py`: canonical checklist validation, Markdown response support, confirmed calendar snapshot export.
- `app.py`: clean text rendering for the existing sample checklist workflow.
- `app/main.py`: calendar preview title context and export description/UID namespace parameters.
- `app/services/calendar.py`: date uncertainty/range validation, deduplication, title/description and stable UID generation.
- `tests/test_checklist_calendar.py` (new): presentation, Markdown, calendar and actual endpoint regressions.
- `scripts/integration_smoke.py`: updated plain-text download assertions.
- `README.md`: plain-text checklist download instructions.

No new application dependency is required. An independent `icalendar` parser was installed only in the verification environment; it is not needed to run this project or its bundled tests.

## Verification

- `python -m unittest discover -s tests -v`: **51 tests passed**.
- `python scripts/unit_test.py`: **20 backend checks passed**.
- `python scripts/integration_smoke.py`: actual Gradio/FastAPI HTTP upload, review, confirmation, checkbox checklist, calendar preview/confirmation/download, directory and failure flow passed with controlled test-only model inference.
- Actual backend calendar endpoint output independently parsed using `icalendar 7.3.0`: valid date, exclusive end, Unicode title, original time wording, notes, source and stable UID passed.
- `python -m compileall -q app.py model_ui.py backend_client.py presentation_utils.py app`: passed.
- `python -m pip check`: no broken requirements.

Tests cover valid/missing/invalid/reversed/uncertain/duplicate dates, calendar confirmation, structured and Markdown checklist fields, optional actions, missing content, and Markdown formatting in actual backend HTTP responses. Test-generated notices never enter production fallback behavior.

Unverified: live Gemma inference (no Ollama service here), visual browser review, and manual import into Google Calendar/Outlook/Apple Calendar. Independent ICS parsing establishes structural validity but is not a claim that those applications were opened and tested.

## Apply

Stop both Python services using Ctrl+C. Extract the update into the existing project root, replacing only the included files. Preserve any additional local edits you have made to these same files since supplying the source ZIP.

Backend terminal:

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend terminal:

```powershell
.\venv\Scripts\python.exe app.py
```

Refresh the browser. Read a notice, review/confirm facts, and build a plan. Open Calendar, preview dates, check the date-confirmation box and download. If no dates appear, return to Review and correct the source-backed date or resolve its uncertainty; do not enter a guessed year.
