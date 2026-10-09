"""End-to-end smoke test against the running backend + local gemma4:e4b.

Usage:  .venv/bin/python scripts/smoke_test.py [base_url]

26 checks covering the five Priority-1 features. Model-bound: takes ~15-20 min.
"""

import json
import sys
import time
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

PASS, FAIL = 0, 0


def call(fn, *args, **kwargs):
    """One retry on transport errors (idle keep-alive sockets get reset)."""
    for attempt in (1, 2):
        try:
            return fn(*args, **kwargs)
        except (httpx.TransportError, httpx.ReadError) as exc:
            if attempt == 2:
                raise
            print(f"  (transport error, retrying: {exc})", flush=True)
            time.sleep(1)


def check(name: str, ok: bool, detail: str = "", elapsed: float = 0.0) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name} ({elapsed:.1f}s) {detail}", flush=True)


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=httpx.Timeout(600.0, connect=10.0))

    # 1. health -------------------------------------------------------------
    t = time.time()
    health = call(client.get, "/api/health").json()
    check("health: ollama reachable + model present", health.get("ok") is True,
          json.dumps({k: health.get(k) for k in ("model", "detail")}), time.time() - t)

    # 2. feature 1 - pasted text -------------------------------------------
    text = (FIXTURES / "notice.txt").read_text(encoding="utf-8")
    t = time.time()
    resp = call(client.post, "/api/analyze", data={"text": text})
    body = resp.json() if resp.status_code == 200 else {"error": resp.text[:300]}
    ok = resp.status_code == 200
    notice = body.get("notice", {}) if ok else {}
    dates = notice.get("dates", [])
    deadline = [d for d in dates if d.get("iso_date") == "2026-11-15"]
    check("analyze(text): title recognised", ok and "scholarship" in notice.get("title", "").lower(),
          repr(notice.get("title", ""))[:80], time.time() - t)
    check("analyze(text): deadline parsed to ISO", bool(deadline), json.dumps(dates)[:200], 0)
    check("analyze(text): required vs optional documents split",
          len(notice.get("required_documents", [])) >= 3 and len(notice.get("optional_documents", [])) >= 1,
          f"required={len(notice.get('required_documents', []))} optional={len(notice.get('optional_documents', []))}", 0)
    check("analyze(text): evidence attached", len(body.get("evidence", [])) >= 3,
          f"{len(body.get('evidence', []))} quotes", 0)
    check("analyze(text): evidence verified against source text",
          any(e.get("verified") is True for e in body.get("evidence", [])),
          f"{sum(1 for e in body.get('evidence', []) if e.get('verified') is True)} verified", 0)
    steps = body.get("checklist", {}).get("steps", [])
    check("analyze(text): ordered checklist with required flags",
          len(steps) >= 3 and any(s["required"] for s in steps) and not all(s["required"] for s in steps),
          f"{len(steps)} steps", 0)
    check("analyze(text): registration link surfaced",
          "example.edu" in (body.get("actions", {}).get("registration_url") or ""),
          body.get("actions", {}).get("registration_url", ""), 0)
    check("analyze(text): contacts extracted",
          any(c.get("phone") or c.get("email") for c in notice.get("contacts", [])),
          json.dumps(notice.get("contacts", []))[:160], 0)
    analysis_id = body.get("id", "")

    # 3. feature 1 - question ----------------------------------------------
    t = time.time()
    resp = call(client.post, "/api/analyze", data={"text": text, "question": "Where do I submit and by when?"})
    qbody = resp.json() if resp.status_code == 200 else {}
    answer = qbody.get("notice", {}).get("answer", "")
    check("analyze(text, question): answers where + when",
          resp.status_code == 200 and "room 12" in answer.lower() and "15" in answer,
          answer[:160], time.time() - t)

    # 4. feature 1 - image ---------------------------------------------------
    t = time.time()
    with (FIXTURES / "notice.png").open("rb") as fh:
        resp = call(client.post, "/api/analyze",
                    files={"file": ("notice.png", fh, "image/png")},
                    data={"question": "What is the last date to apply?"})
    ibody = resp.json() if resp.status_code == 200 else {"error": resp.text[:300]}
    inotice = ibody.get("notice", {})
    check("analyze(image): understands the poster",
          resp.status_code == 200 and "scholarship" in inotice.get("title", "").lower(),
          repr(inotice.get("title", ""))[:80], time.time() - t)
    check("analyze(image): deadline found in image",
          any(d.get("iso_date") == "2026-11-15" for d in inotice.get("dates", [])),
          json.dumps(inotice.get("dates", []))[:200], 0)
    check("analyze(image): answers question from pixels",
          "15" in inotice.get("answer", "") or "november" in inotice.get("answer", "").lower(),
          inotice.get("answer", "")[:160], 0)
    check("analyze(image): evidence present (unverifiable against pixels)",
          len(ibody.get("evidence", [])) >= 2 and all(e.get("verified") is None for e in ibody.get("evidence", [])),
          f"{len(ibody.get('evidence', []))} quotes", 0)

    # 5. feature 1 - PDF ------------------------------------------------------
    t = time.time()
    with (FIXTURES / "notice.pdf").open("rb") as fh:
        resp = call(client.post, "/api/analyze", files={"file": ("notice.pdf", fh, "application/pdf")})
    pbody = resp.json() if resp.status_code == 200 else {"error": resp.text[:300]}
    pnotice = pbody.get("notice", {})
    check("analyze(pdf): text extracted and understood",
          resp.status_code == 200 and "scholarship" in pnotice.get("title", "").lower(),
          f"source={pbody.get('source')}", time.time() - t)
    check("analyze(pdf): page-referenced evidence",
          any("page" in str(e.get("source_ref", "")) for e in pbody.get("evidence", [])),
          json.dumps(pbody.get("evidence", [])[:2])[:200], 0)

    # 6. feature 3 - corrections ---------------------------------------------
    t = time.time()
    resp = call(client.patch, f"/api/analyses/{analysis_id}", json={"venue": "Admin Block, Room 12 (counter 3)"})
    cbody = resp.json() if resp.status_code == 200 else {"error": resp.text[:300]}
    check("patch: user correction applied + logged",
          resp.status_code == 200
          and cbody.get("notice", {}).get("venue", "").startswith("Admin Block")
          and len(cbody.get("corrections", [])) >= 1,
          f"venue={cbody.get('notice', {}).get('venue', '')!r} corrections={len(cbody.get('corrections', []))}",
          time.time() - t)

    # 7. feature 2 - checklist regeneration + download -------------------------
    t = time.time()
    resp = call(client.post, f"/api/analyses/{analysis_id}/checklist", json={"hint": ""})
    rbody = resp.json() if resp.status_code == 200 else {"error": resp.text[:300]}
    rsteps = rbody.get("checklist", {}).get("steps", [])
    check("checklist: regenerated ordered steps with required/optional",
          resp.status_code == 200 and len(rsteps) >= 3 and any(s["required"] for s in rsteps)
          and any(not s["required"] for s in rsteps),
          f"{len(rsteps)} steps", time.time() - t)

    t = time.time()
    resp = call(client.get, f"/api/analyses/{analysis_id}/checklist.md")
    md = resp.text if resp.status_code == 200 else ""
    check("checklist.md: downloadable markdown",
          resp.status_code == 200 and md.startswith("# Action checklist") and "## Steps" in md
          and "attachment" in resp.headers.get("content-disposition", ""),
          f"{len(md)} chars", time.time() - t)

    # 8. feature 4 - enrichment ------------------------------------------------
    t = time.time()
    resp = call(client.post, "/api/enrich", json={"analysis_id": analysis_id})
    ebody = resp.json() if resp.status_code == 200 else {"error": resp.text[:300]}
    matches = ebody.get("matches", [])
    names = [m["entry"]["name"] for m in matches]
    check("enrich: matches a directory entry with source + last_checked",
          resp.status_code == 200 and bool(matches)
          and all(m.get("source", {}).get("last_checked") for m in matches),
          f"matches={names} source={ebody.get('source', {}).get('name', '')}", time.time() - t)
    check("enrich: scholarship office picked", any("scholarship" in n.lower() for n in names), str(names), 0)

    # 9. feature 5 - calendar ---------------------------------------------------
    t = time.time()
    resp = call(client.post, "/api/calendar/preview", json={"analysis_id": analysis_id})
    pv = resp.json() if resp.status_code == 200 else {"error": resp.text[:300]}
    events = pv.get("events", [])
    check("calendar: preview returns confirmable events, no file yet",
          resp.status_code == 200 and any(e["start"] == "2026-11-15" for e in events)
          and pv.get("confirmed") is False,
          json.dumps(events)[:220], time.time() - t)

    t = time.time()
    resp = call(client.post, "/api/calendar/ics", json={"analysis_id": analysis_id})
    check("calendar: ICS blocked until dates confirmed",
          resp.status_code == 400 and "confirmed" in resp.text, resp.text[:120], time.time() - t)

    t = time.time()
    resp = call(client.post, "/api/calendar/ics",
                json={"analysis_id": analysis_id, "confirmed": True, "title": "Scholarship deadline"})
    ics = resp.text if resp.status_code == 200 else ""
    check("calendar: valid .ics generated after confirmation",
          resp.status_code == 200 and "BEGIN:VCALENDAR" in ics and "DTSTART;VALUE=DATE:20261115" in ics
          and "text/calendar" in resp.headers.get("content-type", ""),
          f"{len(ics)} chars", time.time() - t)

    # 10. follow-up question ----------------------------------------------------
    t = time.time()
    resp = call(client.post, f"/api/analyses/{analysis_id}/ask",
                json={"question": "Can I apply if my attendance is 70%?"})
    abody = resp.json() if resp.status_code == 200 else {"error": resp.text[:300]}
    answer = abody.get("answer", "")
    check("ask: follow-up answered from extracted notice",
          resp.status_code == 200 and len(answer) > 20, answer[:160], time.time() - t)

    # 11. missing-image handling -------------------------------------------------
    t = time.time()
    resp = call(client.post, "/api/analyze",
                data={"text": "NOTICE\nFun fest on 12th. Venue: Main ground. Contact 98765."})
    mbody = resp.json() if resp.status_code == 200 else {"error": resp.text[:300]}
    mnotice = mbody.get("notice", {})
    check("missing info: unclear year flagged instead of guessed",
          resp.status_code == 200 and any("unclear" in (m.get("issue") or "") for m in mnotice.get("missing", []))
          and all(not d.get("iso_date") for d in mnotice.get("dates", [])),
          json.dumps({"dates": mnotice.get("dates"), "missing": mnotice.get("missing")})[:260],
          time.time() - t)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
