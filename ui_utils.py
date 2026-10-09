import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent

def parse_deadline(value):
    value = (value or '').strip()
    if not value:
        return None
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('Use YYYY-MM-DD for the deadline.')
    return parsed

def official_resources(category, region):
    """Require an exact category and explicit region match. Never guess contacts."""
    path = BASE / 'helplines.csv'
    if not path.exists() or not region.strip():
        return []
    matches = []
    with path.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            if row.get('category', '').casefold() != (category or '').casefold():
                continue
            if row.get('region', '').casefold() != region.strip().casefold():
                continue
            url = row.get('official_url', '').strip()
            if urlparse(url).scheme != 'https' or not urlparse(url).hostname:
                continue
            try:
                date.fromisoformat(row.get('verified_on', ''))
            except ValueError:
                continue
            matches.append([row.get('organization', ''), url, row.get('phone', ''), row['verified_on']])
    return matches

def _escape(text):
    return str(text).replace('\\', '\\\\').replace('\r', '').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')

def _fold(line):
    chunks, current = [], ''
    for char in line:
        if len((current + char).encode('utf-8')) > 75:
            chunks.append(current)
            current = ' '
        current += char
    chunks.append(current)
    return '\r\n'.join(chunks)

def export_plan(fields, guidance, allow_calendar=False, mode='demo'):
    folder = BASE / 'outputs' / uuid4().hex
    folder.mkdir(parents=True)
    title = fields.get('title') or 'Notice action plan'
    body = [f'NoticeBridge | {mode.upper()} MODE', title, '', 'CONFIRMED DETAILS']
    for key in ['issuer', 'deadline', 'deadline_original', 'amount']:
        body.append(f"{key}: {fields.get(key) or 'Not supplied / not confirmed'}")
    body += ['', guidance['explanation'], '', 'CHECKLIST']
    body += [f'[ ] {item}' for item in guidance['checklist']]
    body += ['', 'UNRESOLVED ITEMS'] + fields.get('unclear_fields', [])
    body += ['', 'ORIGINAL SOURCE EXCERPTS (model-extracted; compare with image)'] + fields.get('source_evidence', [])
    text_path = folder / 'action-plan.txt'
    text_path.write_text('\n'.join(body), encoding='utf-8')
    files = [str(text_path)]
    deadline = parse_deadline(fields.get('deadline'))
    if allow_calendar and deadline and not fields.get('unclear_fields'):
        lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//NoticeBridge//EN', 'CALSCALE:GREGORIAN',
                 'BEGIN:VEVENT', f'UID:{uuid4().hex}@noticebridge.local',
                 'DTSTAMP:' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                 'DTSTART;VALUE=DATE:' + deadline.strftime('%Y%m%d'),
                 'DTEND;VALUE=DATE:' + (deadline + timedelta(days=1)).strftime('%Y%m%d'),
                 'SUMMARY:' + _escape(('DEMO - ' if mode == 'demo' else '') + title),
                 'DESCRIPTION:' + _escape('\n'.join(body)), 'END:VEVENT', 'END:VCALENDAR']
        calendar = folder / 'deadline.ics'
        calendar.write_bytes(('\r\n'.join(_fold(x) for x in lines) + '\r\n').encode('utf-8'))
        files.append(str(calendar))
    return files
