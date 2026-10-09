"""Thin async client for the local Ollama server.

Structured output strategy:
  1. Ask Ollama for a JSON-schema-constrained response (`format: <schema>`).
  2. If the server rejects the schema, fall back to `format: "json"`.
  3. If the model still returns something unparseable, fail loudly instead of
     returning garbage to the caller.
"""

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from .config import KEEP_ALIVE, NUM_CTX, NUM_PREDICT, OLLAMA_HOST, OLLAMA_MODEL, REQUEST_TIMEOUT, THINK

log = logging.getLogger("notice.backend.ollama")

_client: Optional[httpx.AsyncClient] = None


class OllamaError(RuntimeError):
    """Raised when the model cannot produce a usable answer."""


class OllamaUnavailable(OllamaError):
    """Raised when the Ollama server cannot be reached."""


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=OLLAMA_HOST,
            trust_env=False,
            timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=5.0),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def ping() -> Dict[str, Any]:
    """Return {"ok": bool, "model": str, "detail": str}."""
    try:
        resp = await get_client().get("/api/tags")
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return {"ok": False, "model": OLLAMA_MODEL, "detail": f"ollama unreachable: {exc}"}

    names = [m.get("name", "") for m in resp.json().get("models", [])]
    present = any(n == OLLAMA_MODEL or (":" not in OLLAMA_MODEL and n == OLLAMA_MODEL + ":latest") for n in names)
    return {
        "ok": present,
        "model": OLLAMA_MODEL,
        "detail": "model ready" if present else f"model not pulled (available: {', '.join(names) or 'none'})",
    }


def _options(temperature: float, num_predict: int) -> Dict[str, Any]:
    return {
        "temperature": temperature,
        "num_ctx": NUM_CTX,
        "num_predict": num_predict,
    }


async def chat(
    messages: List[Dict[str, Any]],
    *,
    temperature: float = 0.2,
    num_predict: int = NUM_PREDICT,
    fmt: Any = None,
    timeout: Optional[float] = None,
) -> str:
    payload: Dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": _options(temperature, num_predict),
    }
    if fmt is not None:
        payload["format"] = fmt
    if THINK is not None:
        payload["think"] = THINK

    try:
        resp = await get_client().post("/api/chat", json=payload, timeout=timeout or REQUEST_TIMEOUT)
    except httpx.HTTPError as exc:
        raise OllamaUnavailable(f"could not reach Ollama at {OLLAMA_HOST}: {exc}") from exc

    if resp.status_code >= 400:
        raise OllamaError(f"Ollama returned {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    content = (data.get("message") or {}).get("content") or ""
    if data.get("done_reason") == "length":
        log.warning("response truncated at num_predict=%s", num_predict)
    if not content.strip():
        raise OllamaError(f"empty response from {OLLAMA_MODEL}: {json.dumps(data)[:400]}")
    return content


def _parse(content: str) -> Dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in response: {text[:200]}")
    return json.loads(text[start : end + 1])


async def structured(
    messages: List[Dict[str, Any]],
    schema: Dict[str, Any],
    *,
    temperature: float = 0.15,
    num_predict: int = NUM_PREDICT,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Run a chat turn constrained to `schema`, with graceful degradation."""
    try:
        return _parse(await chat(messages, temperature=temperature, num_predict=num_predict, fmt=schema, timeout=timeout))
    except OllamaUnavailable:
        raise
    except (OllamaError, ValueError) as exc:
        log.warning("schema-constrained call failed (%s); falling back to json mode", exc)

    # JSON mode does not itself supply the field names or nested types.
    # Preserve the schema contract even when schema-constrained decoding fails.
    fallback_messages = [dict(message) for message in messages]
    contract = "\n\nReturn exactly the JSON structure described by this schema. Include all properties, using empty strings/lists for absent values. Dates, fees and actions must be objects in their named arrays, not just mentioned in prose.\n" + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    if fallback_messages and fallback_messages[0].get("role") == "system":
        fallback_messages[0]["content"] += contract
    else:
        fallback_messages.insert(0, {"role": "system", "content": contract})
    try:
        return _parse(
            await chat(
                fallback_messages,
                temperature=temperature,
                num_predict=num_predict,
                fmt="json",
                timeout=timeout,
            )
        )
    except (OllamaError, ValueError) as exc:
        raise OllamaError(f"{OLLAMA_MODEL} did not return usable JSON: {exc}") from exc
