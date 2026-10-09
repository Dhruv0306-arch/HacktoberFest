"""File-backed analysis store: one JSON document per analysis under data/analyses/."""

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from .config import ANALYSES_DIR

# Reentrant: update() holds the lock while calling get()/save().
_lock = threading.RLock()


class NotFound(KeyError):
    pass


def _ensure_dir() -> None:
    ANALYSES_DIR.mkdir(parents=True, exist_ok=True)


def _path(analysis_id: str) -> Path:
    safe = "".join(c for c in analysis_id if c.isalnum() or c in "-_")
    if safe != analysis_id:
        raise NotFound(analysis_id)
    return ANALYSES_DIR / f"{safe}.json"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def save(record: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_dir()
    record["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with _lock:
        _path(record["id"]).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


def create(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload.setdefault("id", new_id())
    payload.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    payload.setdefault("corrections", [])
    return save(payload)


def get(analysis_id: str) -> Dict[str, Any]:
    path = _path(analysis_id)
    if not path.exists():
        raise NotFound(analysis_id)
    with _lock:
        return json.loads(path.read_text(encoding="utf-8"))


def update(analysis_id: str, mutator) -> Dict[str, Any]:
    with _lock:
        record = get(analysis_id)
        mutator(record)
        return save(record)


def list_all(limit: int = 50) -> Optional[Dict[str, Any]]:
    _ensure_dir()
    files = sorted(ANALYSES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for path in files[:limit]:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "id": record.get("id"),
                "created_at": record.get("created_at"),
                "title": (record.get("notice") or {}).get("title", ""),
                "source": record.get("source", {}),
            }
        )
    return {"analyses": out}
