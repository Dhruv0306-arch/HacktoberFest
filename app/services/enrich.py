"""Feature 4: enrich a notice with one useful external source (campus / service directory)."""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .. import ollama_client as ollama
from ..config import DIRECTORY_FILE
from ..prompts import SYSTEM_ENRICH
from ..schemas import EnrichmentResult, to_ollama_schema
from .analyze import validate

_STOP = {
    "the", "and", "for", "with", "from", "please", "submit", "must", "shall", "will",
    "this", "that", "your", "you", "are", "was", "has", "have", "not", "any", "all",
    "office", "hall", "room", "block", "campus", "student", "students",
}


def load_directory() -> Dict[str, Any]:
    if not DIRECTORY_FILE.exists():
        return {
            "source": {"name": "directory", "url": "", "last_checked": ""},
            "entries": [],
        }
    data = json.loads(DIRECTORY_FILE.read_text(encoding="utf-8"))
    data.setdefault("entries", [])
    data.setdefault("source", {})
    return data


def _tokens(text: str) -> set:
    return {
        token for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(token) > 2 and token not in _STOP
    }


def _entry_tokens(entry: Dict[str, Any]) -> set:
    blob = " ".join(
        [entry.get("name", ""), entry.get("category", "")]
        + list(entry.get("aliases", []))
    )
    return _tokens(blob)


def candidates(notice: Dict[str, Any], question: str, limit: int = 6) -> Tuple[str, List[Dict[str, Any]]]:
    """Keyword-score directory entries against the notice. Returns (query_text, candidates)."""
    parts = [
        notice.get("title", ""),
        notice.get("summary", ""),
        notice.get("eligibility", ""),
        " ".join(item.get("step", "") + " " + item.get("detail", "") for item in notice.get("action_items", [])),
        question or "",
    ]
    query = "\n".join(p for p in parts if p)
    query_tokens = _tokens(query)

    scored = []
    for entry in load_directory()["entries"]:
        overlap = len(query_tokens & _entry_tokens(entry))
        alias_bonus = sum(
            3 for alias in entry.get("aliases", []) if alias and alias.lower() in query.lower()
        )
        score = overlap + alias_bonus
        if score >= 2:
            scored.append((score, entry))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return query, [entry for _, entry in scored[:limit]]


async def enrich(notice: Dict[str, Any], question: str = "") -> Dict[str, Any]:
    directory = load_directory()
    source_meta = directory.get("source", {})
    query, cands = candidates(notice, question)

    if not cands:
        return {
            "source": source_meta,
            "matches": [],
            "candidates_considered": len(directory["entries"]),
            "note": "no directory entry scored high enough for this notice",
        }

    listing = [
        {"id": entry.get("id"), "name": entry.get("name"), "category": entry.get("category", ""),
         "location": entry.get("location", ""), "hours": entry.get("hours", ""),
         "phone": entry.get("phone", ""), "email": entry.get("email", ""), "url": entry.get("url", "")}
        for entry in cands
    ]
    messages = [
        {"role": "system", "content": SYSTEM_ENRICH},
        {
            "role": "user",
            "content": (
                "NOTICE:\n" + json.dumps(notice, ensure_ascii=False)
                + "\n\nCANDIDATE DIRECTORY ENTRIES:\n" + json.dumps(listing, ensure_ascii=False)
                + ("\n\nUSER QUESTION: " + question if question else "")
            ),
        },
    ]

    matches: List[Dict[str, Any]] = []
    try:
        raw = await ollama.structured(messages, to_ollama_schema(EnrichmentResult), num_predict=1024)
        picked = validate(EnrichmentResult, raw)
        by_id = {entry.get("id"): entry for entry in cands}
        for match in picked.matches:
            entry = by_id.get(match.id)
            if not entry:
                continue
            matches.append({"entry": entry, "reason": match.reason})
    except Exception as exc:  # noqa: BLE001 - enrichment must never break the analysis
        top = cands[0]
        matches = [{
            "entry": top,
            "reason": f"keyword match on directory entry \"{top.get('name')}\" (LLM selection failed: {exc})",
        }]

    for match in matches:
        match["source"] = source_meta
        match["checked_at"] = source_meta.get("last_checked", "")

    return {
        "source": source_meta,
        "matches": matches,
        "candidates_considered": len(directory["entries"]),
        "note": "",
    }
