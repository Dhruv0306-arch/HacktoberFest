"""Server-side HTTP boundary. Never substitutes demo data for a failed request."""
import mimetypes
import os
from pathlib import Path
import re

import httpx
from pydantic import ValidationError
from app.schemas import Notice, Checklist
from presentation_utils import markdown_checklist, plain_markdown

API_URL = os.environ.get('NOTICEBRIDGE_API_URL', 'http://127.0.0.1:8000').rstrip('/')
API_TIMEOUT = float(os.environ.get('NOTICEBRIDGE_API_TIMEOUT', '900'))
MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 90000
EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff', '.gif', '.txt'}


class BackendError(ValueError):
    pass


def validate_record(data, checklist=False):
    if not isinstance(data, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(data.get('id', ''))):
        raise BackendError('Backend returned an invalid analysis identifier.')
    if not isinstance(data.get('notice'), dict) or not data['notice']:
        raise BackendError('Backend returned no notice facts.')
    try:
        notice = Notice.model_validate(data['notice'], strict=True)
        if not any((notice.title, notice.summary, notice.action_items, notice.missing, notice.needs_clearer_image)):
            raise BackendError('Backend returned no readable notice facts or uncertainty information.')
        if checklist:
            if isinstance(data.get('checklist'), str):
                data['checklist'] = markdown_checklist(data['checklist'])
            if not isinstance(data.get('checklist'), dict) or 'steps' not in data['checklist']:
                raise BackendError('Backend returned no checklist.')
            original_steps = data['checklist']['steps']
            canonical = Checklist.model_validate(data['checklist'], strict=True).model_dump()
            for new, old in zip(canonical['steps'], original_steps):
                new['_requirement_known'] = old.get('_requirement_known', 'required' in old)
            data['checklist'] = canonical
    except ValidationError as exc:
        raise BackendError('Backend response has invalid field types. No result was accepted.') from exc
    return data


class BackendClient:
    def __init__(self, base_url=None, transport=None):
        self.base_url = base_url or API_URL
        self.transport = transport

    def request(self, method, path, *, binary=False, **kwargs):
        try:
            with httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(API_TIMEOUT, connect=5),
                              transport=self.transport, trust_env=False) as client:
                response = client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise BackendError('Backend/model timed out. Try a shorter notice, check Ollama, then retry. No sample result was substituted.') from exc
        except httpx.HTTPError as exc:
            raise BackendError(f'Cannot reach the backend at {self.base_url}. Start python -m uvicorn app.main:app --port 8000.') from exc
        if response.is_error:
            try:
                body = response.json()
                message = body.get('error') or body.get('detail') or response.reason_phrase
            except (ValueError, AttributeError):
                message = response.reason_phrase
            raise BackendError(f'Backend {response.status_code}: {str(message)[:700]}')
        if binary:
            return response.content
        try:
            data = response.json()
        except ValueError as exc:
            raise BackendError('Backend returned invalid JSON.') from exc
        if not isinstance(data, dict):
            raise BackendError('Backend returned an unexpected response format.')
        return data

    def health(self):
        return self.request('GET', '/api/health', timeout=10)

    def analyze(self, path=None, text='', language='English'):
        if language not in {'English', 'Hindi'}:
            raise BackendError('Choose English or Hindi.')
        question = f'Explain the notice in {language}. Preserve source quotations exactly. Never infer missing dates or years.'
        if path:
            file = Path(path)
            if file.suffix.lower() not in EXTENSIONS:
                raise BackendError('Unsupported file format. Choose PDF, an image, or TXT.')
            if not file.is_file() or not 0 < file.stat().st_size <= MAX_BYTES:
                raise BackendError('File is empty, missing, or larger than 20 MB.')
            with file.open('rb') as stream:
                result = self.request('POST', '/api/analyze', data={'question': question},
                                      files={'file': (file.name, stream, mimetypes.guess_type(file.name)[0] or 'application/octet-stream')})
        else:
            if not text.strip() or len(text) > MAX_TEXT:
                raise BackendError('Enter readable text (up to 90,000 characters).')
            result = self.request('POST', '/api/analyze', data={'text': text, 'question': question})
        return validate_record(result)

    def confirm(self, record, notice, language):
        validate_record(record)
        Notice.model_validate(notice, strict=True)
        path = f"/api/analyses/{record['id']}"
        validate_record(self.request('PATCH', path, json=notice))
        result = self.request('POST', path + '/checklist', json={'hint': f'Write in {language}. Use only these user-reviewed facts. Do not invent dates, required actions, or source references.'})
        return validate_record(result, checklist=True)

    def calendar_preview(self, record):
        result = self.request('POST', '/api/calendar/preview', json={'analysis_id': record['id'], 'title': record['notice'].get('title', '')})
        if not isinstance(result.get('events'), list) or not isinstance(result.get('warnings'), list):
            raise BackendError('Backend returned an invalid calendar preview.')
        return result

    def calendar_file(self, record, preview=None):
        preview = preview or self.calendar_preview(record)
        dates = [{'label': e['label'], 'value': e.get('date', ''), 'iso_date': e['start'],
                  'iso_end_date': e.get('end', ''), 'source_ref': e.get('source_ref', '')}
                 for e in preview.get('events', [])]
        data = self.request('POST', '/api/calendar/ics', binary=True,
                            json={'dates': dates, 'confirmed': True, 'title': plain_markdown(record['notice']['title']),
                                  'notes': plain_markdown(record['notice'].get('summary', '')), 'event_namespace': record['id']})
        if not data.startswith(b'BEGIN:VCALENDAR'):
            raise BackendError('Backend returned an invalid calendar file.')
        return data

    def checklist_file(self, record):
        return self.request('GET', f"/api/analyses/{record['id']}/checklist.md", binary=True)

    def enrich(self, record):
        result = self.request('POST', '/api/enrich', json={'analysis_id': record['id']})
        if not isinstance(result.get('matches'), list) or not isinstance(result.get('source'), dict):
            raise BackendError('Backend returned an invalid directory response.')
        return result
