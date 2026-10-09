# Community Notice → Action — backend API

The model-backed backend for **PS 01 — Multimodal Community Intelligence**: upload a poster,
notice, form or screenshot and get **what it means, who it affects, and what to do next** —
with evidence, missing-info flags, a directory lookup and a ready-to-use next action.

Model: **local Ollama `gemma4:e4b`** (vision + 131k context). This file documents `app/` only;
the Gradio UI in `app.py` is separate (see README.md).

## Run it

```bash
# 1. deps (one-time)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. start (auto-starts `ollama serve` if needed)
./run.sh                      # http://127.0.0.1:8000
NOTICE_PORT=9000 ./run.sh     # port override
# interactive API docs: http://127.0.0.1:8000/docs
```

Env overrides: `OLLAMA_HOST`, `OLLAMA_MODEL` (default `gemma4:e4b`), `OLLAMA_NUM_CTX`
(default 16384 — the server default of 4096 is too small for a long PDF + JSON output),
`OLLAMA_TIMEOUT`, `OLLAMA_THINK` (default `false`; reasoning tokens roughly double latency on
extraction, set `auto` to let the model decide).

## Verify it

```bash
.venv/bin/python scripts/unit_test.py            # fast, no model (coercion, evidence, ICS, ingest)

.venv/bin/pip install -r requirements-dev.txt    # fixture generation only
.venv/bin/python scripts/make_fixtures.py        # fixtures/notice.{txt,png,pdf}
.venv/bin/python scripts/smoke_test.py           # 26 end-to-end checks, ~15-20 min
```

## Priority-1 features → endpoints

| # | Feature | Endpoint(s) |
|---|---------|-------------|
| 1 | Understand images, PDFs and text together (+ a question) | `POST /api/analyze` — multipart `file` (image/PDF) or form `text`, optional `question`. Returns title, dates, venue, eligibility, required/optional documents, fees, contacts, links, answer. |
| 2 | Turn information into an action checklist (required vs optional) | Checklist is in every `/api/analyze` response; `POST /api/analyses/{id}/checklist` re-derives it. |
| 3 | Show evidence, flag missing info, let users correct | `evidence[]` (verbatim quotes + `page N`/`image`/`pasted text` + server-verified flag), `missing[]` (`absent`/`unreadable`/`unclear`), `needs_clearer_image`, `PATCH /api/analyses/{id}` for corrections, `POST /api/analyses/{id}/ask` for follow-ups. |
| 4 | Enrich with one useful external source | `POST /api/enrich` (by `analysis_id`, `notice` or `text`) → matched directory entry with `source.name`, `source.url`, `last_checked`. `GET /api/directory` lists the source. |
| 5 | Make the next action usable | `actions.registration_url`, `GET /api/analyses/{id}/checklist.md` (download), `POST /api/calendar/preview` → `POST /api/calendar/ics` (refuses without `confirmed: true`). |

Also: `GET /api/health`, `GET /api/analyses`, `GET /api/analyses/{id}`.

### Example

```bash
curl -s -X POST http://127.0.0.1:8000/api/analyze \
  -F file=@fixtures/notice.png \
  -F question="Where do I submit and by when?" | python3 -m json.tool
```

```bash
# calendar is a two-step flow on purpose: preview, then confirm
curl -s -X POST http://127.0.0.1:8000/api/calendar/preview -H 'content-type: application/json' \
  -d '{"analysis_id":"<id>"}'
curl -s -X POST http://127.0.0.1:8000/api/calendar/ics -H 'content-type: application/json' \
  -d '{"analysis_id":"<id>","confirmed":true}' -o notice.ics
```

## Layout

```
app/
  __init__.py           makes `import app` resolve here, not to root app.py (the Gradio UI)
  main.py               FastAPI routes + error mapping
  ollama_client.py      chat + structured-output (schema → json fallback)
  ingest.py             image / PDF (pypdf) / pasted text → model input
  prompts.py            extraction / checklist / enrichment prompts
  schemas.py            Notice, Checklist, EnrichmentResult + Ollama JSON schema
  store.py              one JSON file per analysis under data/analyses/
  services/
    analyze.py          feature 1 + 3 (extraction, evidence verification, coercion)
    checklist.py        feature 2 (+ markdown rendering)
    enrich.py           feature 4 (keyword prefilter → model picks from directory)
    calendar.py         feature 5 (preview, ICS, date confirmation gate)
data/
  directory.json        the "external source" for enrichment (swap for a real feed)
  analyses/*.json       stored analyses (gitignored)
scripts/                unit tests, fixtures, 26-check smoke test
fixtures/               generated test notices (txt / png / pdf)
```

## Design notes

- **Structured output**: requests use Ollama's JSON-schema-constrained `format`, falling back
  to `format:"json"`, then to a parse error — never a silent garbage reply. Empty/thinking-only
  responses are rejected and retried rather than returned.
- **Evidence is checked server-side**: quotes from text/PDF sources are re-matched against the
  extracted text and the page they actually appear on; `verified: true|false` plus a normalised
  `page N` ref. Image quotes return `verified: null` (nothing to match against pixels).
- **Scanned PDFs** (no text layer) are detected and reported instead of guessed:
  `needs_clearer_image: true` + `missing: unreadable` + a request for a photo/pasted text.
- **Dates are never guessed**: ambiguous years/days come back with `iso_date: ""` and an
  `unclear` flag, which also blocks them from the calendar until a user confirms a value.
- **Directory enrichment** is offline-friendly: keyword prefilter over
  `data/directory.json`, then the model picks the genuinely relevant entry (it cannot invent
  ids). Replace that file with a real campus directory / official event feed export.
- **Analysis store is file-backed**, so corrections, checklists and downloads survive a
  restart. It's per-process, single-user demo storage — swap for a real DB if this grows.
- **Model latency**: ~70-250 s per extraction on an M2 (6.6 GB model, ~10 tok/s). Prompt budget
  (≤8 evidence quotes, short details) and `OLLAMA_THINK=false` keep it at the low end.

## UI integration (October 2026)

The Gradio UI now uses these existing routes through `backend_client.py`:

| Route | UI use |
|---|---|
| `GET /api/health` | Backend/model readiness |
| `POST /api/analyze` | Multipart image/PDF or form `text`, with a language request in `question` |
| `PATCH /api/analyses/{id}` | Full reviewed Notice object, before plan generation |
| `POST /api/analyses/{id}/checklist` | Fresh checklist; `hint` requests English/Hindi |
| `GET /api/analyses/{id}/checklist.md` | Checklist download |
| `POST /api/enrich` | Directory match using `analysis_id`; sample provenance remains visible |
| `POST /api/calendar/preview` | Review parseable dates, warnings and omissions |
| `POST /api/calendar/ics` | Export with `analysis_id` and boolean `confirmed: true` |

The backend has no dedicated fact-confirmation endpoint. The UI review checkbox gates
PATCH + checklist regeneration; it is not presented as a separate server confirmation contract.
Original extraction evidence remains original evidence after user corrections.

Compatible validation changes: correction payloads are checked against Notice types before
mutating records; unsupported/empty/corrupt uploads and password-protected PDFs are rejected;
PDFs with missing text layers return instructions to use the existing local OCR workflow.
Direct images are decoded/validated and converted to PNG, with a pixel limit. Oversized uploads
are bounded before inference. No changes were made to the successful response schema.

Calendar export now requires JSON boolean `true`, rejects impossible dates, and uses the next
calendar day as the exclusive end for single-day events. Download filenames are ASCII-safe,
including when a Hindi notice title is supplied. CORS defaults to local UI origins and is
configurable through `DEADLENSE_CORS_ORIGINS`.

The supplied `data/directory.json` is fictional demo campus data. Its timestamps are supplied
metadata, not independent verification. Hindi is requested through existing prompt fields;
there is no separate translation service or verified Hindi quality guarantee.
