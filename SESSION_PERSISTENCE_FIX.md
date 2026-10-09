# DeadLense session persistence fix

Apply to the existing integrated DeadLense project on `dhruv-ui`. No dependencies,
branch changes, commits, merges or pushes were added.

## Apply

Stop the frontend, extract this update into the existing project folder, then run:

```powershell
.\venv\Scripts\python.exe app.py
```

Refresh once to load the update and process your document. Subsequent navigation
and switching between Your document and Demo sample retain that session's values.
The backend code and its setup are unchanged.

## Root cause

`model_ui.py` attached `invalidate_input()` to the source selector. That callback
called `clear_all()`, so selecting Demo sample erased the real document's record,
editable fields, confirmation, plans and downloads. File selection, request start
and request failure also called destructive clear helpers. Local reading cleared
old text at startup/failure, and cancelled completed jobs deleted their previews.
The static Gradio layout did not need remounting or a new frontend framework.

## Changes

- Added `ui_session.py`: a per-browser-session workspace holding separate raw
  working drafts and last-stage history for Your document and Demo sample. Drafts
  preserve values exactly; navigation does not reload an original backend record.
- Removed source selection from model invalidation. Source switching updates only
  visibility and the active tab. Demo and document use their existing independent
  Gradio record and component states.
- Input changes discard late requests, but leave the last successful data, edited
  fields and plan intact. Pending/failed model requests no longer clear results.
- Successful new analysis replaces all model facts and clears derived plan/calendar
  output atomically. A dataset key prevents queued old-field submissions from
  modifying a newer document. No sample content is used when a real request fails.
- Fact edits are captured in the session draft and invalidate derived plans only.
  Confirmation updates both summary and detailed fields, preventing an old detailed
  date from undoing a confirmed correction on subsequent plan generation.
- Local reader failures and unreadable replacement reads retain prior successful
  text. Completed preview files remain available until successful replacement or
  explicit reset. Local page edits are automatically saved into session state;
  edit/save/page-change events are serialized to keep page drafts in order.
- Added **New document / reset**, which intentionally clears the document workflow
  and its files without erasing the demo dataset. Selecting another demo sample
  explicitly starts that new demo dataset, preserving document results separately.
- Failed checklist/calendar requests and sample reads retain previous successful
  output while showing the error. Navigation itself never regenerates results.

## Scope

Persistence covers the current application/browser session. No localStorage,
new document-draft files, database or refresh/restart restoration was introduced.
Existing backend analysis storage is untouched. Intentionally selecting a different
sample replaces the active demo dataset; successfully processing another document
replaces the active document dataset. Editing facts still requires re-confirmation
before a fresh action plan can be trusted.

## Files

Created: ui_session.py, tests/test_session_persistence.py,
scripts/persistence_smoke.py, SESSION_PERSISTENCE_FIX.md.

Modified: app.py, model_ui.py, document_ui.py, tests/test_document_ui.py,
scripts/integration_smoke.py, README.md.

## Verification

Passed:

- `python -m unittest discover -s tests -q`: 69 tests.
- `python scripts/unit_test.py`: 20 backend checks.
- `python scripts/integration_smoke.py`: real Gradio/FastAPI upload, review,
  confirmation, checklist/calendar/directory flow; PDF, image, DOCX, PPTX, PPT.
- `python scripts/persistence_smoke.py`: actual HTTP callbacks and per-client state
  checked after edits, tab navigation, repeated demo/document switches, confirmation,
  failed model requests, corrupted uploads, a new successful dataset, stale queued
  requests, local PDF page edit autosave and an explicitly scoped reset.
- `python -m compileall -q app.py model_ui.py document_ui.py ui_session.py tests scripts`.
- `python -m pip check`: no broken requirements.

Parsing and the UI/backend services were real. Model inference was controlled
ONLY inside tests; live Gemma processing and visual browser behavior were not
verified in this environment. Existing real PDF/OCR and Office parser tests passed.
