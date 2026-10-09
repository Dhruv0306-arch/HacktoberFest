# Keep your plan: automatic checklist download

The checklist file widget was initially empty because the file was created only after a separate Download checklist click. This update creates it immediately when Build my action plan successfully returns the confirmed backend plan.

The exported plain-text file is generated from that actual confirmed backend record using the existing checklist renderer. It includes the plan, dates, unresolved details and source evidence; no sample data is substituted. The Download checklist button can regenerate the file without another network request. Unspecified requirement classifications remain labelled Not classified rather than invented.

If writing the file fails, the plan remains visible and the status explains the error instead of falsely claiming a download exists. Editing facts continues to invalidate old files. Calendar export still requires valid previewed dates and explicit confirmation; it is not created automatically.

Changed files: `model_ui.py`, `app/services/checklist.py`, `scripts/integration_smoke.py`.

Verification: 51 automated tests passed; Gradio/FastAPI HTTP smoke test verified that `checklist.txt` is available immediately after plan generation and remains downloadable. Model inference in the smoke test uses explicit controlled fixtures; live local Gemma inference remains unverified here. No new dependency, branch switch, commit or push.

Apply: stop both services, extract the update into the existing project root on dhruv-ui, replace the included files, and restart backend/frontend. Refresh the browser and build a plan again. Old browser-session plans will not retroactively acquire a file.
