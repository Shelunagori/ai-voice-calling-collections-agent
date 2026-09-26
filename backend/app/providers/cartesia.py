"""Cartesia adapters: Ink streaming STT and Sonic streaming TTS over WebSockets.

API shapes follow https://docs.cartesia.ai (version pinned via CARTESIA_VERSION):
* STT  wss://api.cartesia.ai/stt/websocket?model=..&language=..&encoding=pcm_s16le&sample_rate=..
       binary PCM in; text "finalize" flushes; server sends {"type":"transcript","is_final",..}.
* TTS  wss://api.cartesia.ai/tts/websocket  one socket per provider, multiplexed by
       context_id; {"type":"chunk","data":<b64>}; cancel with {"context_id","cancel":true}.

These adapters are exercised by contract tests against a local fake WebSocket server;
live calls only run when RUN_LIVE_PROVIDER_TESTS=1 and CARTESIA_API_KEY is set.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any
from urllib.parse import urlencode

import websockets

from ..observability import metrics
from .base import ErrorKind, ProviderError, STTEvent, STTEventType

log = logging.getLogger(__name__)
STT_URL = "wss://api.cartesia.ai/stt/websocket"
TTS_URL = "wss://api.cartesia.ai/tts/websocket"


def _headers(api_key: str, version: str) -> dict[str, str]:
    return {"X-API-Key": api_key, "Cartesia-Version": version}


class CartesiaSTTStream:
    def __init__(self, ws: Any, monotonic: Callable[[], float]) -> None:
        self.ws = ws
        self._mono = monotonic
        self._closed = False

    async def send_audio(self, pcm16: bytes) -> None:
        if self._closed:
            return
        try:
            await self.ws.send(pcm16)
        except websockets.ConnectionClosed as e:
            self._closed = True
            raise ProviderError("cartesia_stt", ErrorKind.UNAVAILABLE, "connection closed") from e

    async def finalize(self) -> None:
        if not self._closed:
            with contextlib.suppress(websockets.ConnectionClosed):
                await self.ws.send("finalize")

    async def events(self) -> AsyncIterator[STTEvent]:
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                if t == "transcript":
                    kind = STTEventType.FINAL if msg.get("is_final") else STTEventType.PARTIAL
                    yield STTEvent(kind, text=str(msg.get("text", "")), at=self._mono(), words=msg.get("words") or [])
                elif t == "error":
                    metrics.inc("provider_errors", {"provider": "cartesia_stt", "kind": str(msg.get("error_code"))})
                    yield STTEvent(STTEventType.ERROR, at=self._mono(), error=str(msg.get("message", "error"))[:200])
                elif t == "done":
                    break
        except websockets.ConnectionClosed:
            pass
        yield STTEvent(STTEventType.CLOSED, at=self._mono())

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            await self.ws.send("close")
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.ws.close(), 2)


class CartesiaSTT:
    name = "cartesia"
    provides_endpointing = True

    def __init__(
        self,
        api_key: str,
        model: str,
        version: str,
        monotonic: Callable[[], float] = time.monotonic,
        url: str = STT_URL,
        max_silence_s: float = 0.5,
        min_volume: float = 0.1,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.version = version
        self.url = url
        self._mono = monotonic
        self.max_silence_s = max_silence_s
        self.min_volume = min_volume

    def stream_url(self, language: str, sample_rate: int) -> str:
        q: dict[str, Any] = {
            "model": self.model,
            "encoding": "pcm_s16le",
            "sample_rate": sample_rate,
            "cartesia_version": self.version,
        }
        if self.model.startswith("ink-whisper"):
            q.update(language=language, min_volume=self.min_volume, max_silence_duration_secs=self.max_silence_s)
        return f"{self.url}?{urlencode(q)}"

    async def open_stream(self, language: str, sample_rate: int) -> CartesiaSTTStream:
        started = time.monotonic()
        try:
            ws = await asyncio.wait_for(
                websockets.connect(
                    self.stream_url(language, sample_rate),
                    additional_headers=_headers(self.api_key, self.version),
                    max_size=2**20,
                ),
                timeout=5,
            )
        except (TimeoutError, OSError, websockets.InvalidStatus, websockets.InvalidHandshake) as e:
            metrics.inc("provider_errors", {"provider": "cartesia_stt", "kind": "connect"})
            raise ProviderError("cartesia_stt", ErrorKind.UNAVAILABLE, f"connect failed: {type(e).__name__}") from e
        metrics.observe(
            "provider_latency_ms", (time.monotonic() - started) * 1000, {"provider": "cartesia_stt", "op": "connect"}
        )
        return CartesiaSTTStream(ws, self._mono)


class CartesiaTTS:
    """Streaming TTS over a shared, lazily (re)connected WebSocket."""

    name = "cartesia"

    def __init__(
        self,
        api_key: str,
        model: str,
        version: str,
        voice_id: str,
        voice_id_ja: str = "",
        sample_rate: int = 16000,
        url: str = TTS_URL,
        first_chunk_timeout_s: float = 3.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.version = version
        self.voice_id = voice_id
        self.voice_id_ja = voice_id_ja or voice_id
        self.sample_rate = sample_rate
        self.url = url
        self.first_chunk_timeout_s = first_chunk_timeout_s
        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def _ensure(self) -> Any:
        async with self._lock:
            if self._ws is not None and self._reader is not None and not self._reader.done():
                return self._ws
            try:
                self._ws = await asyncio.wait_for(
                    websockets.connect(
                        f"{self.url}?{urlencode({'cartesia_version': self.version})}",
                        additional_headers=_headers(self.api_key, self.version),
                        max_size=2**22,
                    ),
                    timeout=5,
                )
            except (TimeoutError, OSError, websockets.InvalidStatus, websockets.InvalidHandshake) as e:
                metrics.inc("provider_errors", {"provider": "cartesia_tts", "kind": "connect"})
                raise ProviderError("cartesia_tts", ErrorKind.UNAVAILABLE, f"connect failed: {type(e).__name__}") from e
            self._reader = asyncio.create_task(self._read(self._ws))
            return self._ws

    async def _read(self, ws: Any) -> None:
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                q = self._queues.get(str(msg.get("context_id", "")))
                if q is not None:
                    q.put_nowait(msg)
        except websockets.ConnectionClosed:
            pass
        finally:
            for q in self._queues.values():
                q.put_nowait({"type": "error", "message": "connection closed"})

    def request(self, text: str, language: str, context_id: str) -> dict[str, Any]:
        return {
            "model_id": self.model,
            "transcript": text,
            "voice": {"id": self.voice_id_ja if language == "ja" else self.voice_id},
            "language": language,
            "context_id": context_id,
            "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": self.sample_rate},
            "continue": False,
        }

    async def synthesize(self, text: str, language: str, *, context_id: str) -> AsyncGenerator[bytes, None]:
        ws = await self._ensure()
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._queues[context_id] = q
        started = time.monotonic()
        first = True
        done = False
        carry = b""
        try:
            await ws.send(json.dumps(self.request(text, language, context_id)))
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), self.first_chunk_timeout_s if first else 10.0)
                except TimeoutError as e:
                    raise ProviderError("cartesia_tts", ErrorKind.TIMEOUT, "no audio from TTS") from e
                t = msg.get("type")
                if t == "chunk" and msg.get("data"):
                    # Chunk sizes are not documented as sample-aligned: carry an odd trailing
                    # byte into the next chunk so consumers only ever see whole PCM16 samples.
                    raw = carry + base64.b64decode(msg["data"])
                    cut = len(raw) - (len(raw) % 2)
                    carry = raw[cut:]
                    if not cut:
                        continue
                    if first:
                        metrics.observe(
                            "provider_latency_ms",
                            (time.monotonic() - started) * 1000,
                            {"provider": "cartesia_tts", "op": "first_audio"},
                        )
                        first = False
                    yield raw[:cut]
                elif t == "done" or msg.get("done") is True:
                    done = True
                    return
                elif t == "error":
                    metrics.inc("provider_errors", {"provider": "cartesia_tts", "kind": str(msg.get("error_code"))})
                    raise ProviderError("cartesia_tts", ErrorKind.BAD_RESPONSE, str(msg.get("message", "error"))[:200])
        finally:
            self._queues.pop(context_id, None)
            if not done:
                # Barge-in or error: tell Cartesia to stop generating for this context.
                with contextlib.suppress(Exception):
                    await ws.send(json.dumps({"context_id": context_id, "cancel": True}))

    async def aclose(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
