"""Feature 1 + 3: extract a notice from an image / PDF / text, with evidence and gaps."""

import logging
import asyncio
import re
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

from .. import ollama_client as ollama
from .. import store
from ..ingest import Source, document_block, from_text, image_payload, read_upload
from ..prompts import SYSTEM_EXTRACT
from ..schemas import Notice, MissingInfo, to_ollama_schema

log = logging.getLogger("notice.backend.analyze")

ANSWER_FALLBACK_SYSTEM = """You answer follow-up questions about an already-extracted community notice.
Use ONLY the extracted notice JSON given by the user. If it does not contain the answer, say so
and name the field that is missing. Be concise (1-4 sentences)."""


# --------------------------------------------------------------------------
# tolerant coercion - the model sometimes types numbers where we expect strings
# --------------------------------------------------------------------------
def _coerce(value: Any, annotation: Any) -> Any:
    import typing

    if annotation is str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (dict, list)):
            return str(value)
        return str(value)
    if annotation is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "yes", "y", "1", "required"}
        return bool(value)
    if annotation is int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 1
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if isinstance(value, dict):
            return _coerce_model(value, annotation)
        if isinstance(value, str) and value.strip():
            # the model sometimes emits ["quote text", ...] instead of [{...}, ...]
            primary = getattr(annotation, "_primary", None)
            if primary:
                return _coerce_model({primary: value.strip()}, annotation)
        return None
    origin = typing.get_origin(annotation)
    if origin is list:
        args = typing.get_args(annotation)
        item_ann = args[0] if args else str
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        coerced = []
        for item in value:
            item = _coerce(item, item_ann)
            if item is not None:
                coerced.append(item)
        return coerced
    return value


def _coerce_model(data: Dict[str, Any], model: Type[BaseModel]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, field in model.model_fields.items():
        default = field.get_default(call_default_factory=True)
        out[name] = _coerce(data.get(name, default), field.annotation)
    return out


def validate(model: Type[BaseModel], data: Dict[str, Any]) -> BaseModel:
    if not isinstance(data, dict) or not data or not set(data).intersection(model.model_fields):
        raise ollama.OllamaError("Model returned no recognizable structured fields. Please retry.")
    return model.model_validate(_coerce_model(data, model))


# --------------------------------------------------------------------------
# evidence verification (feature 3): quotes must exist in the source text
# --------------------------------------------------------------------------
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def annotate_evidence(notice: Notice, source: Source) -> List[Dict[str, Any]]:
    haystacks = [_norm(source.text)]
    haystacks += [_norm(p.text) for p in source.pages]
    annotated = []
    for item in notice.evidence:
        quote = _norm(item.quote)
        if source.kind == "image":
            verified: Optional[bool] = None   # cannot verify against pixels
        elif not quote:
            verified = False
        else:
            verified = any(quote in hay for hay in haystacks if hay)

        # Normalise where the quote came from: locate the page it actually sits on.
        if source.kind == "image":
            item.source_ref = "image"
        elif source.kind == "text":
            item.source_ref = "pasted text"
        elif source.kind in {"docx", "pptx", "ppt"}:
            part = next((p for p in source.pages if quote and quote in _norm(p.text)), None)
            if part:
                item.source_ref = part.reference
            elif item.source_ref not in {p.reference for p in source.pages}:
                item.source_ref = ""
        else:
            page = next(
                (p.number for p in source.pages if quote and quote in _norm(p.text)),
                None,
            )
            if page:
                item.source_ref = f"page {page}"
            elif not re.match(r"^page \d+$", item.source_ref or ""):
                item.source_ref = f"page {source.pages[0].number}" if source.pages else "page 1"

        payload = item.model_dump()
        payload["verified"] = verified
        annotated.append(payload)
    return annotated


def _first_registration_link(notice: Notice) -> str:
    for link in notice.links:
        blob = f"{link.label} {link.url}".lower()
        if any(word in blob for word in ("register", "apply", "online", "form", "portal", "link")):
            return link.url
    return notice.links[0].url if notice.links else ""


def apply_optional_flags(steps: List[Dict[str, Any]], notice: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Guarantee feature 2's required/optional split even if the model over-marks.

    Only ever rewrites flags / appends an optional step derived from the notice's own
    optional_documents - never invents required work.
    """
    optional_docs = [d.strip().lower() for d in (notice.get("optional_documents") or []) if d and d.strip()]

    def mentions_optional(step: Dict[str, Any]) -> bool:
        text = f"{step.get('step', '')} {step.get('detail', '')}".lower()
        if "optional" in text:
            return True
        for doc in optional_docs:
            tokens = [t for t in re.findall(r"[a-z0-9]+", doc) if len(t) > 3]
            if tokens and all(token in text for token in tokens):
                return True
        return False

    for step in steps:
        if step.get("required", True) and mentions_optional(step):
            step["required"] = False

    if optional_docs and not any(not step.get("required", True) for step in steps):
        steps.append(
            {
                "order": len(steps) + 1,
                "step": "Submit optional documents",
                "detail": "; ".join(notice.get("optional_documents", []))[:120],
                "required": False,
                "source_ref": "",
            }
        )
    return steps


def build_checklist(notice: Notice) -> Dict[str, Any]:
    steps = [
        {
            "order": index + 1,
            "step": item.step,
            "detail": item.detail,
            "required": item.required,
            "source_ref": item.source_ref,
        }
        for index, item in enumerate(notice.action_items)
    ]
    steps = apply_optional_flags(steps, notice.model_dump())
    return {"summary": notice.summary, "steps": steps}


def _actions(analysis_id: str, notice: Notice) -> Dict[str, Any]:
    return actions_for(analysis_id, [link.model_dump() for link in notice.links],
                       registration_url=_first_registration_link(notice))


def actions_for(analysis_id: str, links: List[Dict[str, Any]], registration_url: str = "") -> Dict[str, Any]:
    """Usable next actions (feature 5). Works from a Notice or a stored dict."""
    if not registration_url:
        for link in links:
            blob = f"{link.get('label', '')} {link.get('url', '')}".lower()
            if any(word in blob for word in ("register", "apply", "online", "form", "portal", "link")):
                registration_url = link.get("url", "")
                break
        if not registration_url and links:
            registration_url = links[0].get("url", "")
    return {
        "registration_url": registration_url,
        "all_links": links,
        "download_checklist": f"/api/analyses/{analysis_id}/checklist.md",
        "calendar_preview": "/api/calendar/preview",
        "calendar_ics": "/api/calendar/ics",
        "enrich": "/api/enrich",
    }


# --------------------------------------------------------------------------
# main entry points
# --------------------------------------------------------------------------
async def analyze_upload(filename: str, content_type: str, data: bytes, question: str = "") -> Dict[str, Any]:
    source = await asyncio.to_thread(read_upload, filename, content_type, data)
    return await _run(source, question)


async def analyze_text(text: str, question: str = "") -> Dict[str, Any]:
    return await _run(from_text(text), question)


def audit_structured_fields(notice: Notice, raw: Dict[str, Any]) -> None:
    """Flag incomplete structure without deriving new facts from generated prose."""
    prose = " ".join((notice.summary, notice.answer))
    indicators = {
        "dates": bool(re.search(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b", prose, re.I)),
        "fees": bool(re.search(r"(?:INR|Rs\.?|₹|USD|\$)\s*\d", prose, re.I)),
        "action_items": bool(notice.required_documents),
    }
    existing = {item.field for item in notice.missing}
    for name in ("dates", "fees", "action_items", "evidence"):
        malformed = name not in raw or not isinstance(raw.get(name), list)
        omitted = not getattr(notice, name) and indicators.get(name, False)
        if (malformed or omitted) and name not in existing:
            note = ("The model did not return this structured list. Review the source and enter verified details manually."
                    if malformed else "The model's explanation/documents suggest this detail, but its structured list is empty. Check the source; do not rely on prose for calendar or actions.")
            notice.missing.append(MissingInfo(field=name, issue="unclear", note=note))


async def _run(source: Source, question: str) -> Dict[str, Any]:
    document = document_block(source)
    prompt = "\n".join(
        [
            f"USER QUESTION:\n{question.strip()}" if question.strip()
            else "USER QUESTION:\n(none - give the standard: what it means / who it affects / what to do next)",
            "",
            "NOTICE TO ANALYSE:",
            document,
        ]
    )
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_EXTRACT},
        {"role": "user", "content": prompt},
    ]
    images = image_payload(source)
    if images:
        messages[-1]["images"] = images

    raw = await ollama.structured(messages, to_ollama_schema(Notice))
    notice = validate(Notice, raw)
    if not any((notice.title, notice.summary, notice.action_items, notice.missing, notice.needs_clearer_image)):
        raise ollama.OllamaError("Model returned no readable notice facts or uncertainty information. Please retry with a clearer input.")

    audit_structured_fields(notice, raw)

    if source.scanned:
        notice.needs_clearer_image = True

    evidence = annotate_evidence(notice, source)

    record = store.create(
        {
            "source": source.summary(),
            "question": question,
            "notice": notice.model_dump(),
            "checklist": build_checklist(notice),
            "evidence": evidence,
        }
    )
    record["actions"] = _actions(record["id"], notice)
    store.save(record)
    return record


async def ask(record: Dict[str, Any], question: str) -> Dict[str, Any]:
    """Feature 1 follow-up: ask a question against an already-extracted notice."""
    if not question or not question.strip():
        raise ValueError("question is required")
    messages = [
        {"role": "system", "content": ANSWER_FALLBACK_SYSTEM},
        {
            "role": "user",
            "content": (
                "EXTRACTED NOTICE JSON:\n"
                + __import__("json").dumps(record.get("notice", {}), ensure_ascii=False)
                + f"\n\nQUESTION: {question.strip()}"
            ),
        },
    ]
    answer = (await ollama.chat(messages, temperature=0.2, num_predict=1024)).strip()
    return {"analysis_id": record["id"], "question": question.strip(), "answer": answer}
