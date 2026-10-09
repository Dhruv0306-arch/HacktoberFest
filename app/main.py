"""Community Notice -> Action backend (FastAPI + local Ollama / gemma4:e4b)."""

import logging
from typing import Any, Dict, List, Optional

from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from . import ollama_client as ollama
from . import store
from .config import ANALYSES_DIR, DATA_DIR, OLLAMA_HOST, OLLAMA_MODEL
from .ingest import IngestError
from .schemas import Notice
from .services import calendar as calendar_service
from .services.calendar import CalendarError
from .services import checklist as checklist_service
from .services import analyze as analyze_service
from .services import enrich as enrich_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("notice.backend")

app = FastAPI(
    title="Community Notice -> Action",
    version="0.1.0",
    description="Backend that turns a poster / notice / PDF / screenshot into evidence-backed actions.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
@app.exception_handler(IngestError)
async def _ingest_error(_: Request, exc: IngestError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(store.NotFound)
async def _not_found(_: Request, exc: store.NotFound):
    return JSONResponse(status_code=404, content={"error": f"analysis not found: {exc}"})


@app.exception_handler(ollama.OllamaUnavailable)
async def _ollama_down(_: Request, exc: ollama.OllamaUnavailable):
    return JSONResponse(status_code=503, content={"error": str(exc)})


@app.exception_handler(ollama.OllamaError)
async def _ollama_error(_: Request, exc: ollama.OllamaError):
    return JSONResponse(status_code=502, content={"error": str(exc)})


@app.exception_handler(calendar_service.CalendarError)
async def _calendar_error(_: Request, exc: calendar_service.CalendarError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# health / meta
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health() -> Dict[str, Any]:
    ollama_state = await ollama.ping()
    return {
        "ok": bool(ollama_state["ok"]),
        "model": OLLAMA_MODEL,
        "ollama_host": OLLAMA_HOST,
        "detail": ollama_state["detail"],
        "data_dir": str(DATA_DIR),
    }


@app.get("/api/directory")
async def directory() -> Dict[str, Any]:
    """The external source used for enrichment, with when it was last checked."""
    data = enrich_service.load_directory()
    return {
        "source": data.get("source", {}),
        "entries": data.get("entries", []),
        "count": len(data.get("entries", [])),
    }


# ---------------------------------------------------------------------------
# feature 1: understand images, PDFs and text together
# ---------------------------------------------------------------------------
@app.post("/api/analyze")
async def analyze(
    file: Optional[UploadFile] = File(default=None),
    text: Optional[str] = Form(default=None),
    question: Optional[str] = Form(default=""),
) -> Dict[str, Any]:
    """Accept a notice photo / PDF / pasted text plus an optional question.

    Returns the extracted notice, evidence, missing fields and an action checklist.
    """
    question = (question or "").strip()
    if file is not None and file.filename:
        data = await file.read()
        record = await analyze_service.analyze_upload(
            file.filename, file.content_type or "", data, question
        )
    elif text and text.strip():
        record = await analyze_service.analyze_text(text, question)
    else:
        raise IngestError("provide a file (image/PDF) or a text field")
    return record


@app.get("/api/analyses")
async def list_analyses() -> Dict[str, Any]:
    return store.list_all() or {"analyses": []}


@app.get("/api/analyses/{analysis_id}")
async def get_analysis(analysis_id: str) -> Dict[str, Any]:
    return store.get(analysis_id)


# ---------------------------------------------------------------------------
# feature 3: let users correct extracted details
# ---------------------------------------------------------------------------
@app.patch("/api/analyses/{analysis_id}")
async def correct_analysis(analysis_id: str, patch: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Apply user corrections to extracted fields: {"venue": "...", "dates": [...], ...}."""
    allowed = set(Notice.model_fields)
    unknown = [key for key in patch if key not in allowed]
    if unknown:
        raise IngestError(f"unknown notice field(s): {', '.join(sorted(unknown))}")

    def apply(record: Dict[str, Any]) -> None:
        notice = record.setdefault("notice", {})
        changed = {}
        for key, value in patch.items():
            before = notice.get(key)
            if before != value:
                changed[key] = {"before": before, "after": value}
                notice[key] = value
        if changed:
            record.setdefault("corrections", []).append(
                {"at": record.get("updated_at"), "changed": changed}
            )
            record["checklist_stale"] = True
            record["actions"] = analyze_service.actions_for(
                record["id"], list(notice.get("links", []) or [])
            )

    return store.update(analysis_id, apply)


@app.post("/api/analyses/{analysis_id}/ask")
async def ask(analysis_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Follow-up question about an already-extracted notice."""
    record = store.get(analysis_id)
    return await analyze_service.ask(record, str(payload.get("question", "")))


# ---------------------------------------------------------------------------
# feature 2: action checklist
# ---------------------------------------------------------------------------
@app.post("/api/analyses/{analysis_id}/checklist")
async def regenerate_checklist(
    analysis_id: str, payload: Optional[Dict[str, Any]] = Body(default=None)
) -> Dict[str, Any]:
    """Re-derive the ordered required/optional checklist (also used after corrections)."""
    record = store.get(analysis_id)
    hint = str((payload or {}).get("hint", ""))
    checklist = await checklist_service.generate(record.get("notice", {}), hint)

    def apply(rec: Dict[str, Any]) -> None:
        rec["checklist"] = checklist
        rec["checklist_stale"] = False

    return store.update(analysis_id, apply)


@app.get("/api/analyses/{analysis_id}/checklist.md", response_class=PlainTextResponse)
async def download_checklist(analysis_id: str) -> Response:
    """Download the checklist as Markdown (feature 5: make the next action usable)."""
    record = store.get(analysis_id)
    markdown = checklist_service.render_markdown(record)
    title = (record.get("notice", {}).get("title") or "notice")[:40]
    slug = "".join(c if c.isalnum() or c in "- " else "" for c in title).strip().replace(" ", "-") or "notice"
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slug}-checklist.md"'},
    )


# ---------------------------------------------------------------------------
# feature 4: enrich with one useful external source
# ---------------------------------------------------------------------------
@app.post("/api/enrich")
async def enrich(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Match the notice against the campus / service directory.

    Body: {"analysis_id": "..."} (preferred) or {"notice": {...}} or {"text": "..."}.
    """
    question = str(payload.get("question", "") or "")
    if payload.get("analysis_id"):
        record = store.get(str(payload["analysis_id"]))
        notice = record.get("notice", {})
    elif payload.get("notice"):
        notice = dict(payload["notice"])
    elif payload.get("text"):
        record = await analyze_service.analyze_text(str(payload["text"]), question)
        notice = record.get("notice", {})
    else:
        raise IngestError("provide analysis_id, notice or text")
    return await enrich_service.enrich(notice, question)


# ---------------------------------------------------------------------------
# feature 5: calendar (preview first, then ICS)
# ---------------------------------------------------------------------------
def _dates_for(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if payload.get("dates") is not None:
        return list(payload["dates"])
    if payload.get("analysis_id"):
        record = store.get(str(payload["analysis_id"]))
        return calendar_service.dates_from_notice(record.get("notice", {}), payload.get("indices"))
    raise IngestError("provide dates or analysis_id")


@app.post("/api/calendar/preview")
async def calendar_preview(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Show extracted dates for confirmation before any calendar file is created."""
    dates = _dates_for(payload)
    events, warnings = calendar_service.preview_events(dates)
    skipped = [
        {"label": date.get("label", ""), "value": date.get("value", "")}
        for date in dates
        if not date.get("iso_date")
    ]
    return {
        "events": events,
        "warnings": warnings,
        "skipped": skipped,
        "confirmed": False,
        "note": "Review these dates, then POST them to /api/calendar/ics with confirmed=true.",
    }


@app.post("/api/calendar/ics")
async def calendar_ics(payload: Dict[str, Any] = Body(...)) -> Response:
    """Create the .ics file. Requires confirmed=true so dates are reviewed first."""
    if not payload.get("confirmed"):
        raise CalendarError("dates must be confirmed first: call /api/calendar/preview, review, then resend with confirmed=true")

    dates = _dates_for(payload)
    events, warnings = calendar_service.preview_events(dates)
    if not events:
        raise CalendarError(
            "no usable dates: " + ("; ".join(warnings) if warnings else "the notice has no parseable dates")
        )
    title = str(payload.get("title") or "Notice deadlines")
    ics = calendar_service.to_ics(events, title=title)
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_slug(title)}.ics"'},
    )


def _slug(text: str) -> str:
    slug = "".join(c if c.isalnum() or c in "- " else "" for c in text).strip().replace(" ", "-")
    return slug or "notice"


@app.on_event("startup")
async def _startup() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ANALYSES_DIR.mkdir(parents=True, exist_ok=True)
    state = await ollama.ping()
    log.info("ollama: %s (%s)", "ready" if state["ok"] else "NOT READY", state["detail"])
