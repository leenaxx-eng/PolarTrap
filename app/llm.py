"""Minimal OpenAI-compatible chat client for the Hostinger AI Router
(base https://router.hostinger.com/v1, POST /chat/completions).
Returns None on any failure; callers must map None to `unknown`, never to a guess."""
from __future__ import annotations

import logging
import time

import httpx

from . import config

log = logging.getLogger("polartrap.llm")


class LLMStatus:
    """Process-wide counters so the UI can show a banner when the router fails."""
    calls = 0
    failures = 0
    last_error: str | None = None


def available() -> bool:
    return bool(config.AI_ROUTER_API_KEY and config.AI_ROUTER_BASE_URL and config.AI_MODEL_CHEAP)


def _adapt(body: dict, error_text: str) -> bool:
    """Some OpenAI-compatible backends (newer OpenAI reasoning models) reject `max_tokens`
    or a non-default `temperature`. Adjust the request once per parameter; True if changed."""
    err = error_text.lower()
    changed = False
    if "max_tokens" in err and "max_tokens" in body:
        # reasoning models spend part of the budget on hidden reasoning
        body["max_completion_tokens"] = body.pop("max_tokens") + 2000
        changed = True
    if "temperature" in err and "temperature" in body:
        body.pop("temperature")
        changed = True
    return changed


def chat(messages: list[dict], model: str | None = None, max_tokens: int = 400,
         temperature: float = 0.0, timeout: float = 45.0, retries: int = 2) -> str | None:
    if not available():
        LLMStatus.last_error = "AI Router not configured"
        return None
    model = model or config.AI_MODEL_CHEAP
    url = config.AI_ROUTER_BASE_URL.rstrip("/") + "/chat/completions"
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    headers = {"Authorization": f"Bearer {config.AI_ROUTER_API_KEY}"}
    delay = 2.0
    attempt = -1
    while attempt < retries:
        attempt += 1
        LLMStatus.calls += 1
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=timeout)
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"].get("content")
                if isinstance(content, list):  # some providers return content parts
                    content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                return content
            LLMStatus.last_error = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code == 400 and _adapt(body, r.text):
                attempt -= 1  # parameter dialect fixed (e.g. newer OpenAI models); free immediate retry
                continue
            if r.status_code not in (408, 409, 429, 500, 502, 503, 504):
                break
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            LLMStatus.last_error = str(e)[:200]
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
    LLMStatus.failures += 1
    log.warning("LLM call failed: %s", LLMStatus.last_error)
    return None
