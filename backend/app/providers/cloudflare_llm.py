"""Cloudflare Workers AI adapter (REST).

POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}
* `complete_json`: JSON mode via `response_format: {type: json_schema}` (non-streaming;
  JSON mode does not support streaming).
* `stream_text`: `stream: true`, server-sent events `data: {"response": "..."}`.

Timeouts are enforced per request, retries are bounded and only for errors that are
safe to retry (timeouts before any output, 429, 5xx). The model id is configuration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..observability import metrics
from .base import ErrorKind, ProviderError

log = logging.getLogger(__name__)
API = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"


def classify(status: int) -> tuple[ErrorKind, bool]:
    if status == 429:
        return ErrorKind.RATE_LIMITED, True
    if status in (401, 403):
        return ErrorKind.AUTH, False
    if 400 <= status < 500:
        return ErrorKind.BAD_REQUEST, False
    return ErrorKind.UNAVAILABLE, True


class CloudflareLLM:
    name = "cloudflare"

    def __init__(
        self, account_id: str, api_token: str, model: str, max_retries: int = 1, client: httpx.AsyncClient | None = None
    ) -> None:
        if not account_id or not api_token:
            raise ValueError("Cloudflare credentials missing")
        self.model = model
        self._url = API.format(account=account_id, model=model)
        self._headers = {"Authorization": f"Bearer {api_token}"}
        self.max_retries = max_retries
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete_json(self, system: str, user: str, schema: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        body = {
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": schema},
            "max_tokens": 256,
            "temperature": 0,
        }
        attempt = 0
        while True:
            started = time.monotonic()
            try:
                resp = await asyncio.wait_for(
                    self._client.post(self._url, headers=self._headers, json=body), timeout=timeout
                )
            except (TimeoutError, httpx.TimeoutException) as e:
                # Not retried: a second full timeout would blow the turn's latency budget;
                # the caller falls back to the deterministic parser instead.
                metrics.inc("provider_errors", {"provider": "cloudflare_llm", "kind": "timeout"})
                raise ProviderError(self.name, ErrorKind.TIMEOUT, f"no response in {timeout}s", retryable=True) from e
            except httpx.HTTPError as e:
                metrics.inc("provider_errors", {"provider": "cloudflare_llm", "kind": "unavailable"})
                if attempt < self.max_retries:
                    attempt += 1
                    await asyncio.sleep(0.2 * attempt)
                    continue
                raise ProviderError(self.name, ErrorKind.UNAVAILABLE, type(e).__name__, retryable=True) from e
            metrics.observe(
                "provider_latency_ms", (time.monotonic() - started) * 1000, {"provider": "cloudflare_llm", "op": "json"}
            )
            if resp.status_code != 200:
                kind, retryable = classify(resp.status_code)
                metrics.inc("provider_errors", {"provider": "cloudflare_llm", "kind": kind.value})
                if retryable and attempt < self.max_retries:
                    attempt += 1
                    await asyncio.sleep(0.2 * attempt)
                    continue
                raise ProviderError(self.name, kind, f"HTTP {resp.status_code}", retryable=retryable)
            return _extract_json(resp.json())

    async def stream_text(self, system: str, user: str, *, timeout: float) -> AsyncIterator[str]:
        body = {
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": True,
            "max_tokens": 200,
            "temperature": 0.3,
        }
        # No retry once streaming has started: tokens may already have been consumed.
        try:
            async with self._client.stream(
                "POST", self._url, headers=self._headers, json=body, timeout=httpx.Timeout(timeout, connect=3.0)
            ) as resp:
                if resp.status_code != 200:
                    kind, retryable = classify(resp.status_code)
                    raise ProviderError(self.name, kind, f"HTTP {resp.status_code}", retryable=retryable)
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    tok = obj.get("response")
                    if isinstance(tok, str) and tok:
                        yield tok
        except httpx.TimeoutException as e:
            raise ProviderError(self.name, ErrorKind.TIMEOUT, "stream timeout", retryable=False) from e
        except httpx.HTTPError as e:
            raise ProviderError(self.name, ErrorKind.UNAVAILABLE, type(e).__name__) from e


def _extract_json(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload.get("success", True):
        raise ProviderError("cloudflare", ErrorKind.BAD_RESPONSE, "unsuccessful response")
    result = payload.get("result", payload)
    resp = result.get("response") if isinstance(result, dict) else None
    if isinstance(resp, dict):
        return resp
    if isinstance(resp, str):
        try:
            obj = json.loads(resp)
        except json.JSONDecodeError as e:
            raise ProviderError("cloudflare", ErrorKind.BAD_RESPONSE, "response is not JSON") from e
        if isinstance(obj, dict):
            return obj
    raise ProviderError("cloudflare", ErrorKind.BAD_RESPONSE, "missing response object")
