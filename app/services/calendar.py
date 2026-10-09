"""Feature 5: turn confirmed dates into a calendar file, and render checklists/links."""

import re
import uuid
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CalendarError(ValueError):
    pass


def _weekday(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%A")


def preview_events(dates: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate extracted dates BEFORE anything is written to a calendar file."""
    if not isinstance(dates, list):
        raise CalendarError("dates must be a list")
    events, warnings = [], []
    seen = set()
    for index, date in enumerate(dates):
        if not isinstance(date, dict) or any(not isinstance(date.get(key, ""), str) for key in ("label", "value", "iso_date", "iso_end_date", "source_ref")):
            raise CalendarError("Each date must contain text fields, including YYYY-MM-DD dates.")
        label = (date.get("label") or f"Date {index + 1}").strip()
        value = (date.get("value") or "").strip()
        iso = (date.get("iso_date") or "").strip()
        end = (date.get("iso_end_date") or "").strip()

        if not iso:
            warnings.append(
                f"{label} ({value or 'no value'}) has no confirmed calendar date - "
                "fix it or confirm it before adding to the calendar."
            )
            continue
        if not ISO_DATE.match(iso):
            warnings.append(f"{label}: \"{iso}\" is not a valid YYYY-MM-DD date.")
            continue
        try:
            datetime.strptime(iso, "%Y-%m-%d")
        except ValueError:
            warnings.append(f"{label}: invalid calendar date {iso}.")
            continue
        try:
            if end:
                datetime.strptime(end, "%Y-%m-%d")
        except ValueError:
            warnings.append(f"{label}: invalid end date {end}; event omitted.")
            continue
        if end and (not ISO_DATE.match(end) or end < iso):
            warnings.append(f"{label}: invalid date range; event omitted.")
            continue

        if (end or iso) == "9999-12-31":
            warnings.append(f"{label}: date is outside the supported calendar export range.")
            continue
        key = (label.casefold(), iso, end or iso)
        if key in seen:
            warnings.append(f"{label}: duplicate date omitted.")
            continue
        seen.add(key)
        events.append(
            {
                "label": label,
                "date": value,
                "start": iso,
                "end": end,
                "weekday": _weekday(iso),
                "source_ref": date.get("source_ref", ""),
                "confirmed": False,
            }
        )
    return events, warnings


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _fold(line: str) -> str:
    """RFC 5545 line folding at 75 octets."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    chunks, current = [], b""
    for char in line:
        if len((current + char.encode("utf-8"))) > 74:
            chunks.append(current.decode("utf-8", errors="ignore"))
            current = char.encode("utf-8")
        else:
            current += char.encode("utf-8")
    chunks.append(current.decode("utf-8", errors="ignore"))
    return "\r\n ".join(chunks)


def to_ics(events: List[Dict[str, Any]], title: str = "Notice deadlines", notes: str = "", event_namespace: str = "") -> str:
    if not events:
        raise CalendarError("no confirmed dates to add")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//DeadLense//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for event in events:
        start = event["start"].replace("-", "")
        if event.get("end"):
            # all-day DTEND is exclusive

            end = (datetime.strptime(event["end"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
        else:
            end = (datetime.strptime(event["start"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
        event_title = title + " — " + event["label"] if title and title != event["label"] else event["label"]
        description = "\n".join(part for part in (notes, f"From notice: {event.get('date', '')}", f"Source: {event.get('source_ref', '')}") if part and not part.endswith(": "))
        identity = json.dumps([event_namespace, title, event["label"], event["start"], event.get("end", "")], ensure_ascii=False)
        uid = uuid.uuid5(uuid.NAMESPACE_URL, identity)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}@notice-to-action",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{start}",
            f"DTEND;VALUE=DATE:{end}",
            f"SUMMARY:{_escape(event_title)}",
            f"DESCRIPTION:{_escape(description)}",
            "BEGIN:VALARM",
            "TRIGGER:-P1D",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape(event['label'])}",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def dates_from_notice(notice: Dict[str, Any], indices: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    dates = [dict(item) for item in notice.get("dates", [])]
    gaps = [item for item in notice.get("missing", []) if item.get("issue") in {"unclear", "unreadable"}]
    for date in dates:
        label = date.get("label", "").casefold()
        for gap in gaps:
            field = gap.get("field", "").casefold()
            if field in {"date", "dates", "year"} or (field and field in label) or ("deadline" in field and "deadline" in label):
                date["iso_date"] = ""
                break
    if indices is not None and (not isinstance(indices, list) or any(type(i) is not int or i < 0 or i >= len(dates) for i in indices)):
        raise CalendarError("indices must identify existing notice dates")
    if indices is None:
        return dates
    out = []
    for index in indices:
        if 0 <= index < len(dates):
            out.append(dates[index])
    return out
