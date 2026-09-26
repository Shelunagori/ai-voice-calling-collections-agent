"""Deterministic mock providers for local development, CI and evaluation.

They implement the same interfaces as the real adapters and are clearly labelled
`mock` everywhere their output is surfaced (API, UI, latency reports).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from datetime import date
from typing import Any

from ..domain import nlu_rules
from ..domain.commands import Action
from ..domain.models import DialogPhase, Language
from ..domain.turn_context import context_for
from ..voice.vad import EnergyVAD, VADEventType
from .base import (
    CallHandle,
    ErrorKind,
    NotificationChannel,
    NotificationResult,
    ProviderError,
    STTEvent,
    STTEventType,
    TransferResult,
)

Sleep = Callable[[float], Awaitable[None]]


class MockLLM:
    """Rule-backed stand-in for a hosted LLM.

    `complete_json` runs the deterministic parser and returns its output *as JSON*, so
    the full LLM code path (schema validation, merge, fallback) is exercised in CI.
    `failure` injects faults for tests: "timeout", "invalid_json", "hallucinate".
    """

    name = "mock"
    model = "mock-rules-v1"

    def __init__(self, latency_s: float = 0.0, failure: str | None = None, sleep: Sleep = asyncio.sleep) -> None:
        self.latency_s = latency_s
        self.failure = failure
        self._sleep = sleep
        self.calls: list[dict[str, Any]] = []

    async def complete_json(self, system: str, user: str, schema: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        self.calls.append({"kind": "json", "user": user})
        if self.latency_s:
            await self._sleep(self.latency_s)
        if self.failure == "timeout":
            raise ProviderError(self.name, ErrorKind.TIMEOUT, "injected timeout", retryable=True)
        if self.failure == "invalid_json":
            return {"actions": [{"action": "SET_IDENTITY_VERIFIED", "verified": True}]}
        if self.failure == "hallucinate":
            return {"actions": [{"action": "PROPOSE_PAYMENT", "amount": 1, "days_from_now": 365}]}
        m = re.search(r"Today is (\d{4}-\d{2}-\d{2})", system)
        today = date.fromisoformat(m[1]) if m else date.today()
        lang = Language.JA if "Japanese" in system else Language.EN
        step = re.search(r"Current step: ([A-Z_]+)", system)
        ctx = context_for(DialogPhase(step[1])) if step and step[1] in DialogPhase.__members__ else None
        interp = nlu_rules.interpret(user, lang, today, context=ctx)
        partial = interp.first(Action.PARTIAL_DOB)
        if self.failure == "partial_dob_string" and partial and partial.dob_year:
            # What Cloudflare returned on the real call: a partial date squeezed into `dob`.
            dob = f"{partial.dob_year}" + (f"-{partial.dob_month:02d}" if partial.dob_month else "")
            return {"actions": [{"action": "PROVIDE_DOB", "dob": dob}]}
        return json.loads(interp.model_dump_json(exclude={"source", "notes"}, exclude_none=True))

    async def stream_text(self, system: str, user: str, *, timeout: float) -> AsyncIterator[str]:
        self.calls.append({"kind": "stream", "user": user})
        if self.failure == "timeout":
            raise ProviderError(self.name, ErrorKind.TIMEOUT, "injected timeout", retryable=True)
        m = re.search(r"<message>(.*)</message>", user, re.S)
        text = m[1].strip() if m else user
        if self.failure == "hallucinate":
            text = text + " We can also waive ¥50,000 for you."
        for tok in re.findall(r"\S+\s*", text):
            if self.latency_s:
                await self._sleep(self.latency_s / 10)
            yield tok


class MockSTTStream:
    """Simulated streaming STT.

    Scripted utterances are released one per detected speech segment: a PARTIAL
    (first word) once ~300 ms of speech has been heard and the FINAL when the segment
    ends or `finalize()` is called. With no script it emits empty finals, which the
    turn detector treats as noise. Latency before the final can be injected.
    """

    def __init__(
        self, sample_rate: int, script: deque[str], final_delay_s: float, monotonic: Callable[[], float], sleep: Sleep
    ) -> None:
        self.vad = EnergyVAD(sample_rate)
        self.script = script
        self.final_delay_s = final_delay_s
        self._mono = monotonic
        self._sleep = sleep
        self._q: asyncio.Queue[STTEvent] = asyncio.Queue()
        self._current: str | None = None
        self._partial_sent = False
        self._closed = False
        self.audio_bytes = 0

    async def send_audio(self, pcm16: bytes) -> None:
        if self._closed:
            return
        self.audio_bytes += len(pcm16)
        for ev in self.vad.feed(pcm16):
            if ev.type == VADEventType.SPEECH_START:
                self._current = self.script.popleft() if self.script else ""
                self._partial_sent = False
                await self._q.put(STTEvent(STTEventType.SPEECH_STARTED, at=self._mono()))
            elif ev.type == VADEventType.SPEECH_END:
                await self._emit_final()
        if self.vad.speaking and self._current and not self._partial_sent and self.vad.current_speech_ms >= 300:
            self._partial_sent = True
            first = self._current.split()[0] if " " in self._current else self._current[:2]
            await self._q.put(STTEvent(STTEventType.PARTIAL, text=first, at=self._mono()))

    async def _emit_final(self) -> None:
        if self._current is None:
            return
        text, self._current = self._current, None
        if self.final_delay_s:
            await self._sleep(self.final_delay_s)
        await self._q.put(STTEvent(STTEventType.FINAL, text=text, at=self._mono()))

    async def finalize(self) -> None:
        await self._emit_final()

    async def events(self) -> AsyncIterator[STTEvent]:
        while True:
            ev = await self._q.get()
            yield ev
            if ev.type == STTEventType.CLOSED:
                return

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._q.put(STTEvent(STTEventType.CLOSED, at=self._mono()))


class MockSTT:
    name = "mock"
    model = "mock-stt-scripted"
    provides_endpointing = False

    def __init__(
        self,
        monotonic: Callable[[], float],
        script: list[str] | None = None,
        final_delay_s: float = 0.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._mono = monotonic
        self.script: deque[str] = deque(script or [])
        self.final_delay_s = final_delay_s
        self._sleep = sleep
        self.streams: list[MockSTTStream] = []

    async def open_stream(self, language: str, sample_rate: int) -> MockSTTStream:
        s = MockSTTStream(sample_rate, self.script, self.final_delay_s, self._mono, self._sleep)
        self.streams.append(s)
        return s


class MockTTS:
    """Produces near-silent PCM whose duration tracks the text length, in 20 ms chunks.
    The browser may voice mock responses locally with the Web Speech API; the server
    still paces this audio so interruption timing behaves like a real stream."""

    name = "mock"
    model = "mock-tts-silence"

    def __init__(
        self,
        sample_rate: int = 16000,
        first_chunk_delay_s: float = 0.0,
        chars_per_s_en: float = 15.0,
        chars_per_s_ja: float = 7.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.sample_rate = sample_rate
        self.first_chunk_delay_s = first_chunk_delay_s
        self.cps = {"en": chars_per_s_en, "ja": chars_per_s_ja}
        self._sleep = sleep
        self.requests: list[dict[str, Any]] = []
        self.cancelled: list[str] = []

    async def synthesize(self, text: str, language: str, *, context_id: str) -> AsyncGenerator[bytes, None]:
        self.requests.append({"text": text, "language": language, "context_id": context_id})
        seconds = max(0.4, len(text) / self.cps.get(language, 12.0))
        chunk = int(self.sample_rate * 0.02) * 2
        total = int(seconds / 0.02)
        try:
            if self.first_chunk_delay_s:
                await self._sleep(self.first_chunk_delay_s)
            for _ in range(total):
                yield bytes(chunk)
        except (asyncio.CancelledError, GeneratorExit):
            self.cancelled.append(context_id)
            raise


class FakeTelephony:
    """No-network telephony used when Twilio is disabled or unconfigured."""

    name = "fake"
    live = False

    def __init__(self, auth_token: str | None = None) -> None:
        import secrets

        self.auth_token = auth_token or secrets.token_hex(32)
        self.calls: list[dict[str, str]] = []
        self.transfers: list[dict[str, str]] = []
        self.hangups: list[str] = []

    async def place_call(self, to_e164: str, session_id: str) -> CallHandle:
        cid = f"FAKE{len(self.calls) + 1:04d}"
        self.calls.append({"to": to_e164, "session_id": session_id, "call_id": cid})
        return CallHandle(self.name, cid, "simulated")

    async def transfer(self, call_id: str, to_e164: str) -> TransferResult:
        self.transfers.append({"call_id": call_id, "to": to_e164})
        return TransferResult(ok=True, status="simulated", detail="telephony disabled; transfer recorded only")

    async def hangup(self, call_id: str) -> None:
        self.hangups.append(call_id)

    def validate_signature(self, url: str, params: dict[str, str], signature: str) -> bool:
        from ..telephony.signature import validate_twilio_signature

        return validate_twilio_signature(self.auth_token, url, params, signature)


class MockNotifier:
    """Records outbound SMS/e-mail instead of sending. Demonstrates channel extensibility."""

    name = "mock"

    def __init__(self) -> None:
        self.outbox: list[dict[str, Any]] = []

    async def send(
        self, channel: NotificationChannel, to: str, template: str, data: dict[str, Any]
    ) -> NotificationResult:
        self.outbox.append({"channel": channel.value, "to": to, "template": template, "data": data})
        return NotificationResult(channel, delivered=False, provider=self.name, detail="recorded in mock outbox")
