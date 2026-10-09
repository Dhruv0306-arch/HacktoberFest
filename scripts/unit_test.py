"""Fast unit tests - no model calls.  Run: .venv/bin/python scripts/unit_test.py"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas import ChecklistStep, Notice, to_ollama_schema
from app.services.analyze import apply_optional_flags, annotate_evidence, validate
from app.services.calendar import CalendarError, preview_events, to_ics
from app.services.checklist import render_markdown
from app.ingest import Page, Source, from_text, read_upload

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def main():
    # --- schema conversion -------------------------------------------------
    schema = to_ollama_schema(Notice)
    check("ollama schema has no $ref/$defs leftovers", "$ref" not in str(schema) and "$defs" not in schema)
    check("ollama schema forces required", schema.get("required") == list(schema.get("properties", {})))

    # --- tolerant coercion --------------------------------------------------
    raw = {
        "title": 42,
        "dates": ["Registration closes 15 Nov 2026"],
        "evidence": ["deadline is 15 Nov 2026"],
        "action_items": [{"step": "Apply", "required": "no"}],
        "missing": [{"field": "venue", "issue": "absent"}],
    }
    notice = validate(Notice, raw)
    check("coerces numeric title to str", notice.title == "42", repr(notice.title))
    check("coerces string date item to DateInfo",
          notice.dates and notice.dates[0].value == "Registration closes 15 Nov 2026", str(notice.dates[:1]))
    check("coerces string evidence item to Evidence",
          notice.evidence and notice.evidence[0].quote.startswith("deadline"), str(notice.evidence[:1]))
    check("coerces 'no' to required=false", notice.action_items[0].required is False)

    # --- optional-step guarantee --------------------------------------------
    steps = [
        {"order": 1, "step": "Check eligibility", "detail": "", "required": True, "source_ref": ""},
        {"order": 2, "step": "Submit sports certificate", "detail": "for weightage", "required": True, "source_ref": ""},
        {"order": 3, "step": "Register online", "detail": "", "required": True, "source_ref": ""},
    ]
    fixed = apply_optional_flags(steps, {"optional_documents": ["Sports / NCC certificate"]})
    check("optional document step flagged optional", fixed[1]["required"] is False,
          str([s["required"] for s in fixed]))

    steps2 = [
        {"order": 1, "step": "Apply", "detail": "", "required": True, "source_ref": ""},
        {"order": 2, "step": "Register", "detail": "", "required": True, "source_ref": ""},
    ]
    fixed2 = apply_optional_flags(steps2, {"optional_documents": ["Sports certificate"]})
    check("appends an optional step when none exists",
          fixed2[-1]["required"] is False and fixed2[-1]["order"] == 3, str(fixed2[-1]))

    # --- evidence source refs ------------------------------------------------
    src = from_text("Deadline is 15 November 2026 in the notice.")
    n = validate(Notice, {"evidence": [{"field": "deadline", "quote": "15 November 2026", "source_ref": "page 9"}]})
    ev = annotate_evidence(n, src)
    check("text source ref forced to 'pasted text'", ev[0]["source_ref"] == "pasted text", str(ev[0]))
    check("quote verified against source text", ev[0]["verified"] is True)

    n2 = validate(Notice, {"evidence": [{"field": "x", "quote": "not in the doc at all", "source_ref": ""}]})
    ev2 = annotate_evidence(n2, src)
    check("invented quote marked verified=false", ev2[0]["verified"] is False)

    pdf = Source(kind="pdf", filename="x.pdf",
                 pages=[Page(1, "First page about fees"), Page(2, "Second page has the deadline 15 November 2026")])
    n3 = validate(Notice, {"evidence": [{"field": "deadline", "quote": "deadline 15 November 2026",
                                          "source_ref": "pasted text"}]})
    ev3 = annotate_evidence(n3, pdf)
    check("pdf quote located on its real page", ev3[0]["source_ref"] == "page 2", str(ev3[0]))

    # --- calendar -------------------------------------------------------------
    preview, warnings = preview_events([
        {"label": "Deadline", "value": "15 November 2026", "iso_date": "2026-11-15"},
        {"label": "Vague", "value": "soon", "iso_date": ""},
    ])
    check("preview keeps confirmed dates, warns on vague", len(preview) == 1 and len(warnings) == 1,
          f"events={len(preview)} warnings={len(warnings)}")
    ics = to_ics(preview, "Deadline")
    check("ics is well formed", "BEGIN:VCALENDAR" in ics and "DTSTART;VALUE=DATE:20261115" in ics
          and ics.endswith("END:VCALENDAR\r\n"), ics[:40].replace("\r\n", "|"))
    try:
        to_ics([], "none")
        check("ics rejects empty events", False)
    except CalendarError:
        check("ics rejects empty events", True)

    # --- ingestion ------------------------------------------------------------
    src_img = read_upload("a.png", "image/png", (Path(__file__).resolve().parents[1] / "fixtures/notice.png").read_bytes())
    check("image upload becomes base64 image source", src_img.kind == "image" and len(src_img.images) == 1)
    try:
        read_upload("a.pdf", "application/pdf", b"not a real pdf")
        check("corrupt pdf rejected", False)
    except Exception as exc:
        check("corrupt pdf rejected", "pdf" in str(exc).lower(), str(exc)[:50])
    try:
        read_upload("a.png", "image/png", b"")
        check("empty upload rejected", False)
    except Exception:
        check("empty upload rejected", True)

    # --- checklist markdown ----------------------------------------------------
    md = render_markdown({
        "id": "abc", "created_at": time.strftime("%Y-%m-%d"),
        "source": {"filename": "notice.txt", "kind": "text", "pages": 1},
        "notice": {"title": "Scholarship", "summary": "Apply",
                   "dates": [{"label": "Deadline", "value": "15 Nov", "iso_date": "2026-11-15"}],
                   "missing": [{"field": "venue", "issue": "absent", "note": ""}],
                   "links": [{"label": "Apply", "url": "https://x"}]},
        "checklist": {"summary": "Apply",
                      "steps": [{"order": 1, "step": "Apply", "detail": "before deadline",
                                 "required": True, "source_ref": "page 1"}]},
        "evidence": [{"field": "title", "quote": "Scholarship", "source_ref": "page 1", "verified": True}],
    })
    check("markdown checklist renders sections",
          md.startswith("# Action checklist") and "## Steps" in md and "[ ] **(Required)**" in md
          and "## Missing" in md and "verified" in md)

    step = ChecklistStep.model_validate({"order": 1, "step": "x"})
    check("checklist step model ok", step.step == "x")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
