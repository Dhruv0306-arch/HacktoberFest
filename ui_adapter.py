"""Saved sample adapter only. Real inference lives in backend_client.py."""
import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
MODE = 'demo'  # Real uploads always use FastAPI, regardless of legacy NOTICEBRIDGE_MODE.
SCALARS = ['title', 'doc_type', 'issuer', 'deadline', 'deadline_original', 'amount']
LISTS = ['eligibility', 'required_actions', 'documents_needed', 'source_evidence', 'unclear_fields']

def normalize(data):
    if not isinstance(data, dict):
        raise ValueError('Backend must return a dictionary, not a JSON string.')
    result = {}
    for key in SCALARS:
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f'{key} must be text or null.')
        result[key] = value
    for key in LISTS:
        value = data.get(key, [])
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            raise ValueError(f'{key} must be a list of strings.')
        result[key] = value
    return result

def extract_notice(image_path, sample='Clean notice'):
    if not image_path:
        raise ValueError('Choose a notice image first.')
    if MODE == 'demo':
        data = json.loads((BASE / 'samples/demo_notice.json').read_text(encoding='utf-8-sig'))
        if sample == 'Unclear deadline':
            data['deadline'] = None
            data['deadline_original'] = None
            data['source_evidence'] = ['Second-year students must pay INR 2500.', 'Bring your student ID.']
            data['unclear_fields'] = ['deadline: unreadable in this sample; check with the issuer.']
    else:
        raise ValueError('Use Your document for real backend processing.')
    return normalize(data)

def build_guidance(confirmed_fields, language):
    if MODE == 'demo':
        if language != 'English':
            raise ValueError('Demo fixtures support English only. Test other languages in live mode.')
        d = confirmed_fields
        explanation = f"{d.get('issuer') or 'The issuer'} issued this {d.get('doc_type') or 'notice'}."
        if d.get('amount'):
            explanation += f" Stated amount: {d['amount']}."
        explanation += f" Deadline: {d.get('deadline') or 'not confirmed'}."
        result = {'explanation': explanation, 'checklist': list(d['required_actions'])}
        if d['documents_needed']:
            result['checklist'].append('Prepare: ' + ', '.join(d['documents_needed']))
    else:
        raise ValueError('Use Your document for real backend processing.')
    if not isinstance(result, dict) or not isinstance(result.get('explanation'), str):
        raise ValueError('Guidance must contain a text explanation.')
    checklist = result.get('checklist')
    if not isinstance(checklist, list) or any(not isinstance(x, str) for x in checklist):
        raise ValueError('Guidance checklist must be a list of strings.')
    return result
