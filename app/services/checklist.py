"""Feature 2: ordered action checklist with required / optional steps."""

import json
from typing import Any, Dict, List

from .. import ollama_client as ollama
from ..prompts import SYSTEM_CHECKLIST
from ..schemas import Checklist, to_ollama_schema
from .analyze import apply_optional_flags, validate


async def generate(notice: Dict[str, Any], hint: str = "") -> Dict[str, Any]:
    """Re-derive the checklist from an extracted notice (used on demand and after corrections)."""
    messages = [
        {"role": "system", "content": SYSTEM_CHECKLIST},
        {
            "role": "user",
            "content": "EXTRACTED NOTICE JSON:\n"
            + json.dumps(notice, ensure_ascii=False)
            + (f"\n\nFOCUS: {hint}" if hint else ""),
        },
    ]
    raw = await ollama.structured(messages, to_ollama_schema(Checklist))
    checklist = validate(Checklist, raw)

    # Guarantee ordering and numbering even if the model shuffled it.
    steps: List[Dict[str, Any]] = [step.model_dump() for step in checklist.steps]
    steps.sort(key=lambda s: (s.get("order") or 99, s.get("step") or ""))
    for index, step in enumerate(steps, start=1):
        step["order"] = index
    steps = apply_optional_flags(steps, notice or {})
    return {"summary": checklist.summary or (notice or {}).get("summary", ""), "steps": steps}


def render_markdown(record: Dict[str, Any]) -> str:
    notice = record.get("notice", {})
    checklist = record.get("checklist", {})
    source = record.get("source", {})
    lines = [
        f"# Action checklist: {notice.get('title') or 'Untitled notice'}",
        "",
        f"_Source: {source.get('filename') or source.get('kind', 'unknown')} "
        f"({source.get('kind', '?')}, {source.get('pages', '?')} page(s)) - "
        f"generated {record.get('created_at', '')}_",
        "",
        f"**Summary:** {checklist.get('summary') or notice.get('summary', '')}",
        "",
        "## Steps",
        "",
    ]
    for step in checklist.get("steps", []):
        tag = ("Required" if step.get("required") else "Optional") if step.get("_requirement_known", True) else "Not classified"
        detail = f" - {step['detail']}" if step.get("detail") else ""
        src = f" _({step['source_ref']})_" if step.get("source_ref") else ""
        lines.append(f"{step.get('order', 0)}. [ ] **({tag})** {step.get('step', '')}{detail}{src}")

    missing = notice.get("missing", [])
    if missing:
        lines += ["", "## Missing / needs confirmation", ""]
        for gap in missing:
            note = f" - {gap.get('note')}" if gap.get("note") else ""
            lines.append(f"- **{gap.get('field', '?')}**: {gap.get('issue', 'absent')}{note}")

    key_dates = [d for d in notice.get("dates", []) if d.get("value")]
    if key_dates:
        lines += ["", "## Key dates (confirm before calendaring)", ""]
        for date in key_dates:
            iso = f" -> {date['iso_date']}" if date.get("iso_date") else " -> date not confirmed"
            lines.append(f"- {date.get('label', 'Date')}: {date['value']}{iso}")

    links = notice.get("links", [])
    if links:
        lines += ["", "## Links", ""]
        for link in links:
            lines.append(f"- [{link.get('label') or link.get('url')}]({link.get('url')})")

    lines += ["", "## Evidence", ""]
    for item in record.get("evidence", []):
        flag = {True: "verified", False: "NOT found in source text", None: "from image (unverified)"}[
            item.get("verified")
        ]
        lines.append(f"- {item.get('field', '?')} [{flag}] \"{item.get('quote', '')}\" ({item.get('source_ref', '')})")

    return "\n".join(lines) + "\n"
