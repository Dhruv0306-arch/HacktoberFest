"""Feature 5: turn confirmed dates into a calendar file, and render checklists/links."""

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CalendarError(ValueError):
    pass


def _weekday(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%A")


def preview_events(dates: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate extracted dates BEFORE anything is written to a calendar file."""
    events, warnings = [], []
    for index, date in enumerate(dates):
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
        if end and (not ISO_DATE.match(end) or end < iso):
            warnings.append(f"{label}: end date \"{end}\" is invalid; using the start date only.")
            end = ""

        events.append(
            {
                "label": label,
                "date": value,
                "start": iso,
                "end": end,
                "weekday": _weekday(iso),
                "source_ref": date.get("source_ref", ""),
                "confirmed": True,
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


def to_ics(events: List[Dict[str, Any]], title: str = "Notice deadlines", notes: str = "") -> str:
    if not events:
        raise CalendarError("no confirmed dates to add")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Community Notice to Action//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for event in events:
        start = event["start"].replace("-", "")
        if event.get("end"):
            # all-day DTEND is exclusive
            from datetime import timedelta

            end = (datetime.strptime(event["end"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
        else:
            end = start
        description = notes or f"From notice: {event.get('date', '')}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uuid.uuid4()}@notice-to-action",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{start}",
            f"DTEND;VALUE=DATE:{end}",
            f"SUMMARY:{_escape(event['label'])}",
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
    dates = notice.get("dates", [])
    if indices is None:
        return dates
    out = []
    for index in indices:
        if 0 <= index < len(dates):
            out.append(dates[index])
    return out
