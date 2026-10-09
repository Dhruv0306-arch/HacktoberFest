# NoticeBridge review-screen fix

Apply this update to the integrated project on `dhruv-ui`. It changes four production files:
`model_ui.py`, `app/prompts.py`, `app/ollama_client.py`, and `app/services/analyze.py`.
It also adds focused regression tests in `tests/test_structured_review.py`.

## What changed

- Empty tables receive one editable blank row rather than an empty CSV-import drop zone.
- Each table has an Add row button for dates, fees, actions, contacts, links or uncertainty.
- Text inputs explain when it is appropriate to leave them blank; the clarification checkbox label makes its meaning explicit.
- Blank rows are excluded from saved Notice objects. They never become extracted facts or checklist actions.
- Fact edits/additions invalidate previous confirmation, plans and calendar downloads.
- The JSON-mode fallback now receives the actual schema, including nested types and array names. Previously it received no schema after schema-constrained generation failed.
- Extraction instructions explicitly require dates, amounts and instructions in `dates`, `fees` and `action_items`, in addition to any prose explanation.
- Extraction/checklist prompts no longer demand a minimum step count that could encourage unsupported actions.
- Missing or malformed structured lists, and prose mentioning a date/fee with an empty corresponding list, are flagged as uncertainty for review. No dates, fees or actions are synthesized from that prose.

This fixes the UI placeholder problem and strengthens extraction. It does not guarantee that the local model will populate every field correctly. Existing saved analyses are not regenerated; analyze the notice again after restarting.

## Apply on Windows

Stop both Python services with Ctrl+C. In a terminal at the project root, confirm `git branch --show-current` prints `dhruv-ui`. Extract the archive into that project root, allowing these named files to be replaced. Keep any additional local modifications backed up if you changed those same files after the supplied source ZIP.

Restart the backend:

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Restart the frontend in a second terminal:

```powershell
.\venv\Scripts\python.exe app.py
```

No new dependency is required. Retain your already-set model/environment variables.

Analyze the sample again. Check `dates`, `fees` and `action_items`. If they are still empty,
look at the uncertainty table and paste the saved analysis JSON from `data/analyses/` for further diagnosis.
The old sample's disclaimer explicitly says it is fictional and no payment is due; preserve that distinction in the generated explanation.

## Verification

- `python -m unittest discover -s tests -v`: 40 tests passed.
- `python scripts/integration_smoke.py`: actual Gradio/FastAPI HTTP workflow passed using controlled test-only inference responses.
- `python -m compileall -q app.py model_ui.py app`: passed.

New tests cover editable blank rows, exclusion of empty/None rows, manual date/action round-trip,
prose-only structured omissions, genuinely absent facts, and schema preservation in JSON fallback.
Live local Gemma inference remains unverified in this environment; no Ollama instance is available here.
No branch was switched, and no commit or push was performed.
