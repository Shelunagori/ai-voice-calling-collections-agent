"""Provider interfaces. Domain code depends only on these protocols, never on SDKs."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class ErrorKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH = "auth"
    BAD_REQUEST = "bad_request"
    BAD_RESPONSE = "bad_response"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"


class ProviderError(Exception):
    def __init__(self, provider: str, kind: ErrorKind, message: str, retryable: bool = False) -> None:
        super().__init__(f"{provider}: {kind.value}: {message}")
        self.provider = provider
        self.kind = kind
        self.retryable = retryable


# ---------------------------------------------------------------------- LLM
@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    async def complete_json(self, system: str, user: str, schema: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        """Return a JSON object constrained (where supported) by `schema`."""

    def stream_text(self, system: str, user: str, *, timeout: float) -> AsyncIterator[str]:
        """Yield text deltas. Cancelling the consumer must stop the request."""


# ---------------------------------------------------------------------- STT
class STTEventType(StrEnum):
    PARTIAL = "partial"
    FINAL = "final"
    SPEECH_STARTED = "speech_started"
    ERROR = "error"
    CLOSED = "closed"


@dataclass(frozen=True)
class STTEvent:
    type: STTEventType
    text: str = ""
    at: float = 0.0  # monotonic time the event was received
    words: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class STTStream(Protocol):
    async def send_audio(self, pcm16: bytes) -> None: ...

    async def finalize(self) -> None:
        """Ask the provider to flush buffered audio into a final transcript."""

    def events(self) -> AsyncIterator[STTEvent]: ...

    async def close(self) -> None: ...


class STTProvider(Protocol):
    name: str
    model: str
    provides_endpointing: bool

    async def open_stream(self, language: str, sample_rate: int) -> STTStream: ...


# ---------------------------------------------------------------------- TTS
class TTSProvider(Protocol):
    name: str
    model: str
    sample_rate: int

    def synthesize(self, text: str, language: str, *, context_id: str) -> AsyncGenerator[bytes, None]:
        """Yield raw PCM16 mono chunks. Cancelling the consumer cancels generation."""


# ---------------------------------------------------------------------- telephony
@dataclass(frozen=True)
class CallHandle:
    provider: str
    call_id: str
    status: str


@dataclass(frozen=True)
class TransferResult:
    ok: bool
    status: str  # "dialing" | "simulated" | "failed"
    detail: str = ""


class TelephonyProvider(Protocol):
    name: str
    live: bool

    async def place_call(self, to_e164: str, session_id: str) -> CallHandle: ...

    async def transfer(self, call_id: str, to_e164: str) -> TransferResult: ...

    async def hangup(self, call_id: str) -> None: ...

    def validate_signature(self, url: str, params: dict[str, str], signature: str) -> bool: ...


# ---------------------------------------------------------------------- notifications
class NotificationChannel(StrEnum):
    SMS = "sms"
    EMAIL = "email"


@dataclass(frozen=True)
class NotificationResult:
    channel: NotificationChannel
    delivered: bool
    provider: str
    detail: str = ""


class Notifier(Protocol):
    name: str

    async def send(
        self, channel: NotificationChannel, to: str, template: str, data: dict[str, Any]
    ) -> NotificationResult: ...
