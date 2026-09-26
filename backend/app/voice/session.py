"""Real-time voice session runtime.

Owns the audio-in -> audio-out loop for one conversation:

    audio frames -> VAD + streaming STT -> end-of-turn decision -> understanding
    -> ConversationController.apply (authoritative, under a lock) -> realizer
    -> streaming TTS (paced, generation-tagged) -> transport

Barge-in: while the agent is speaking, sustained caller speech (VAD) or a
non-filler partial transcript cancels playback. The playback generation counter is
bumped *before* the TTS task is cancelled, so any chunk already in flight is
dropped, and the transport is told to flush its buffer. Timestamps
`barge_in_detected_at`, `tts_cancel_requested_at` and `tts_stopped_at` are recorded
for every interruption.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..domain.audit import AuditEvent, AuditType
from ..domain.controller import ConversationController, Effect, TurnOutcome
from ..domain.models import Channel, TransferStatus, Turn
from ..domain.nlu_rules import is_filler_only
from ..domain.realizer import LLMRealizer, TemplateRealizer
from ..domain.understanding import Understanding
from ..providers.base import (
    ErrorKind,
    NotificationChannel,
    Notifier,
    ProviderError,
    STTEventType,
    STTProvider,
    STTStream,
    TelephonyProvider,
    TTSProvider,
)
from .latency import TurnLatency
from .lifecycle import Lifecycle, Transition, VoiceState
from .turn_detection import EndOfTurnDetector, TurnDecision
from .vad import EnergyVAD, VADEventType

log = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]


class Transport(Protocol):
    """Where audio and events go (browser WebSocket, Twilio media stream, test sink)."""

    async def send_audio(self, pcm16: bytes, generation: int) -> None: ...

    async def clear_audio(self, generation: int) -> None: ...

    async def send_event(self, event: dict[str, Any]) -> None: ...


class Recorder(Protocol):
    async def record(self, kind: str, payload: dict[str, Any]) -> None: ...


class NullRecorder:
    async def record(self, kind: str, payload: dict[str, Any]) -> None:
        return None


@dataclass
class RuntimeConfig:
    sample_rate: int = 16000
    barge_in_min_speech_ms: int = 250
    barge_in_partial_min_ms: int = 120
    playback_lead_s: float = 0.25
    silence_timeout_s: float = 8.0
    max_session_s: float = 900.0
    tick_s: float = 0.05
    transfer_number: str = ""


@dataclass
class BargeIn:
    turn_index: int
    source: str
    barge_in_detected_at: float
    tts_cancel_requested_at: float
    tts_stopped_at: float
    played_s: float
    interrupted_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_index": self.turn_index,
            "source": self.source,
            "barge_in_detected_at": self.barge_in_detected_at,
            "tts_cancel_requested_at": self.tts_cancel_requested_at,
            "tts_stopped_at": self.tts_stopped_at,
            "cancel_latency_ms": round((self.tts_stopped_at - self.barge_in_detected_at) * 1000, 2),
            "played_s": round(self.played_s, 3),
            "interrupted_text": self.interrupted_text,
        }


@dataclass
class _Playback:
    generation: int
    text: str
    started_at: float | None = None
    sent_s: float = 0.0
    synthesis_complete: bool = False
    interrupted: bool = False
    task: asyncio.Task[None] | None = None
    estimate_s: float = 1.0

    def played_s(self, now: float) -> float:
        if self.started_at is None:
            return 0.0
        return max(0.0, min(self.sent_s, now - self.started_at))

    def fraction(self, now: float) -> float:
        total = self.sent_s if self.synthesis_complete else max(self.sent_s, self.estimate_s)
        return 1.0 if total <= 0 else min(1.0, self.played_s(now) / total)


@dataclass
class SessionDeps:
    controller: ConversationController
    understanding: Understanding
    realizer: TemplateRealizer | LLMRealizer
    tts: TTSProvider
    stt: STTProvider | None
    telephony: TelephonyProvider | None
    notifier: Notifier
    monotonic: Callable[[], float]
    sleep: Sleep = asyncio.sleep
    recorder: Recorder = field(default_factory=NullRecorder)
    provider_labels: dict[str, str] = field(default_factory=dict)


class VoiceSession:
    def __init__(
        self,
        deps: SessionDeps,
        transport: Transport,
        config: RuntimeConfig | None = None,
        input_mode: str = "text",
        call_id: str | None = None,
    ) -> None:
        self.d = deps
        self.c = deps.controller
        self.transport = transport
        self.cfg = config or RuntimeConfig()
        self.input_mode = input_mode  # "voice" | "text"
        self.call_id = call_id
        self._mono = deps.monotonic
        self.lifecycle = Lifecycle(self._mono, self._on_transition)
        self.vad = EnergyVAD(self.cfg.sample_rate)
        self.eot = EndOfTurnDetector()
        self.gen = 0
        self.turns: list[Turn] = []
        self.latencies: list[TurnLatency] = []
        self.barge_ins: list[BargeIn] = []
        self.errors: list[str] = []
        self._lock = asyncio.Lock()
        self._outq: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()
        self._sender: asyncio.Task[None] | None = None
        self._ticker: asyncio.Task[None] | None = None
        self._stt_stream: STTStream | None = None
        self._stt_task: asyncio.Task[None] | None = None
        self._process_task: asyncio.Task[None] | None = None
        self._applied = False
        self._playback: _Playback | None = None
        self._turn_text: list[str] = []
        self._partial = ""
        self._carry_text = ""
        self._speech_end_at: float | None = None
        self._final_at: float | None = None
        self._listening_since = self._mono()
        self._started_at = self._mono()
        self._pending_text: str | None = None
        self._first_audio_logged = False
        self._first_partial_logged = False
        self._first_final_logged = False
        self._stt_reopens = 0
        self._bg: set[asyncio.Task[Any]] = set()
        self.ended = asyncio.Event()
        self.c.audit.subscribe(self._on_audit)

    # ------------------------------------------------------------------ plumbing
    @property
    def state(self) -> VoiceState:
        return self.lifecycle.state

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        self._outq.put_nowait((kind, payload))

    def _on_audit(self, ev: AuditEvent) -> None:
        # On the wire the envelope type is "audit"; the audit event type travels as event_type.
        self.emit("audit", {**ev.to_dict(), "event_type": ev.type.value})

    def _on_transition(self, t: Transition) -> None:
        if t.to_state == VoiceState.LISTENING:
            self._listening_since = self._mono()
        self.emit("lifecycle", {"from": t.from_state.value, "to": t.to_state.value, "cause": t.cause, "at": t.at})

    async def _send_loop(self) -> None:
        while True:
            item = await self._outq.get()
            if item is None:
                return
            kind, payload = item
            try:
                await self.transport.send_event({**payload, "type": kind})
            except Exception as e:  # client gone: keep recording, stop sending
                log.debug("transport_send_failed", extra={"error": str(e)[:100]})
            try:
                await self.d.recorder.record(kind, payload)
            except Exception:
                log.exception("recorder_failed")

    def _spawn(self, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        t = asyncio.ensure_future(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    def _emit_state(self) -> None:
        self.emit("state", {"collection": self.c.state.snapshot(), "voice_state": self.state.value})

    def _log(self, event: str, level: int = logging.INFO, **fields: Any) -> None:
        """Structured milestone log. Never includes audio bytes or transcript text."""
        log.log(
            level,
            f"voice.{event}",
            extra={
                "voice_event": event,
                "session_id": str(self.c.state.session_id),
                "channel": self.c.state.channel.value,
                **fields,
            },
        )

    # ------------------------------------------------------------------ lifecycle
    async def start(self, run_background: bool = True) -> None:
        self._sender = asyncio.create_task(self._send_loop())
        self._log(
            "session_started", input_mode=self.input_mode, language=self.c.lang.value, providers=self.d.provider_labels
        )
        self.c.audit.record(
            AuditType.CALL_STARTED,
            0,
            channel=self.c.state.channel.value,
            input_mode=self.input_mode,
            providers=self.d.provider_labels,
        )
        if self.d.stt is not None and self.input_mode == "voice":
            try:
                self._stt_stream = await self.d.stt.open_stream(self.c.lang.value, self.cfg.sample_rate)
                self._stt_task = asyncio.create_task(self._stt_loop(self._stt_stream))
                self._log(
                    "stt_stream_ready",
                    provider=self.d.provider_labels.get("stt", "?"),
                    sample_rate=self.cfg.sample_rate,
                )
            except ProviderError as e:
                self._provider_failure("stt", e)
                self.input_mode = "text"
                self.emit("input_mode", {"input_mode": "text", "reason": "speech recognition unavailable"})
        if run_background:
            self._ticker = asyncio.create_task(self._tick_loop())
        self.lifecycle.to(VoiceState.PROCESSING, "session_start")
        lat = TurnLatency(0, "system")
        lat.mark("turn_commit", self._mono())
        lat.mark("nlu_done", self._mono())
        outcome = self.c.start()
        self._applied = True
        lat.mark("policy_done", self._mono())
        self._emit_state()
        self._process_task = asyncio.create_task(self._respond(outcome, lat))

    async def end(self, reason: str) -> None:
        if self.lifecycle.terminal:
            return
        cur = asyncio.current_task()
        pending = []
        if self._playback and self._playback.task and not self._playback.task.done():
            self.gen += 1
            self._playback.task.cancel()
        for t in (self._process_task, self._ticker, self._stt_task):
            if t and t is not cur and not t.done():
                t.cancel()
                pending.append(t)
        if pending:
            # Let cancelled tasks emit their final events before the session-ended event.
            await asyncio.wait(pending, timeout=1.0)
        self.c.on_disconnect(reason)
        with contextlib.suppress(Exception):
            if self._stt_stream:
                await self._stt_stream.close()
        self.lifecycle.to(VoiceState.ENDED, reason)
        self.c.audit.record(
            AuditType.SESSION_ENDED, self.c.turn_index, reason=reason, call_status=self.c.state.call_status.value
        )
        bg = [t for t in self._bg if t is not cur and not t.done()]
        if bg:  # e.g. a promise confirmation still being sent: record it before the end event
            await asyncio.wait(bg, timeout=2.0)
        self._emit_state()
        self.emit("session.ended", {"reason": reason, "summary": self.summary()})
        self._log(
            "session_ended",
            reason=reason,
            call_status=self.c.state.call_status.value,
            turns=len(self.turns),
            barge_ins=len(self.barge_ins),
            errors=len(self.errors),
        )
        self._outq.put_nowait(None)
        if self._sender:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._sender, timeout=5)
        self.ended.set()

    def summary(self) -> dict[str, Any]:
        return {
            "session_id": str(self.c.state.session_id),
            "lifecycle_path": self.lifecycle.path(),
            "turns": len(self.turns),
            "barge_ins": [b.to_dict() for b in self.barge_ins],
            "latency": [
                {"turn_index": lt.turn_index, "mode": lt.input_mode, **lt.stages_ms()} for lt in self.latencies
            ],
            "collection": self.c.state.snapshot(),
        }

    # ------------------------------------------------------------------ inputs
    async def on_audio(self, pcm16: bytes) -> None:
        if self.lifecycle.terminal:
            return
        if not self._first_audio_logged:
            self._first_audio_logged = True
            self._log("first_audio_frame", bytes=len(pcm16), stt_attached=self._stt_stream is not None)
        if self._stt_stream is not None:
            try:
                await self._stt_stream.send_audio(pcm16)
            except ProviderError as e:
                self._provider_failure("stt", e)
        for ev in self.vad.feed(pcm16):
            if ev.type == VADEventType.SPEECH_START:
                await self._on_speech_start()
            else:
                lag = self.vad.audio_time - ev.audio_time
                await self._on_speech_end(self._mono() - lag)
        if (
            self.state == VoiceState.AGENT_SPEAKING
            and self.vad.speaking
            and self.vad.current_speech_ms >= self.cfg.barge_in_min_speech_ms
        ):
            await self.barge_in("vad_sustained_speech")

    async def on_text(self, text: str) -> None:
        """Typed turn (browser fallback / evaluation). Treated like a final transcript."""
        text = text.strip()
        if not text or self.lifecycle.terminal:
            return
        now = self._mono()
        if self.state == VoiceState.AGENT_SPEAKING:
            await self.barge_in("typed_input")
        elif self.state == VoiceState.PROCESSING:
            if not self._applied and self._process_task and not self._process_task.done():
                self._process_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._process_task
                self.lifecycle.to(VoiceState.LISTENING, "caller_added_text")
            else:
                # Keep every queued line (joined), not just the last one.
                self._pending_text = f"{self._pending_text} {text}" if self._pending_text else text
                return
        if not self._accepting_turns():
            self.emit("input.ignored", {"text": text, "reason": "conversation_closed"})
            return
        self.emit("transcript.final", {"text": text, "source": "typed"})
        # Typed turns have no speech end; latency is measured from text arrival.
        self._commit_turn(text, "text", speech_end=None, final_at=now)

    async def interrupt(self) -> None:
        """Explicit barge-in (UI button / test): same code path as detected speech."""
        await self.barge_in("manual_interrupt")

    # ------------------------------------------------------------------ STT
    async def _stt_loop(self, stream: STTStream) -> None:
        try:
            async for ev in stream.events():
                if ev.type == STTEventType.PARTIAL:
                    if not self._first_partial_logged:
                        self._first_partial_logged = True
                        self._log("first_partial_transcript", chars=len(ev.text))
                    self._partial = ev.text
                    self.emit("transcript.partial", {"text": ev.text})
                    if (
                        self.state == VoiceState.AGENT_SPEAKING
                        and self.vad.speaking
                        and self.vad.current_speech_ms >= self.cfg.barge_in_partial_min_ms
                        and not is_filler_only(ev.text)
                    ):
                        await self.barge_in("stt_partial")
                elif ev.type == STTEventType.FINAL:
                    text = ev.text.strip()
                    if text and not self._first_final_logged:
                        self._first_final_logged = True
                        self._log("first_final_transcript", chars=len(text), state=self.state.value)
                    if self.state == VoiceState.PROCESSING:
                        # Late final for a turn that was already committed: never leak it forward.
                        if text:
                            self.emit("transcript.discarded", {"text": text, "state": self.state.value})
                        continue
                    if (
                        self.state in (VoiceState.AGENT_SPEAKING, VoiceState.LISTENING)
                        and text
                        and not is_filler_only(text)
                    ):
                        # Words the VAD did not (yet) treat as a turn: a quick answer over the agent,
                        # or quiet speech. They are a turn, never silently dropped.
                        if self.state == VoiceState.AGENT_SPEAKING:
                            await self.barge_in("stt_final")
                        if self.state in (VoiceState.LISTENING, VoiceState.INTERRUPTED):
                            if self.state == VoiceState.LISTENING:
                                self.lifecycle.to(VoiceState.USER_SPEAKING, "stt_final_without_vad")
                            if self._speech_end_at is None and not self.vad.speaking:
                                self._speech_end_at = self._mono()
                    if self.state not in (VoiceState.USER_SPEAKING, VoiceState.INTERRUPTED):
                        continue
                    if ev.text.strip():
                        self._turn_text.append(ev.text.strip())
                        self._final_at = ev.at or self._mono()
                        self.emit("transcript.final", {"text": ev.text.strip(), "source": "stt"})
                    await self._evaluate_turn()
                elif ev.type == STTEventType.ERROR:
                    self._provider_failure("stt", ProviderError("stt", ErrorKind.UNAVAILABLE, ev.error or "error"))
                elif ev.type == STTEventType.CLOSED:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._provider_failure("stt", e)
        if not self.lifecycle.terminal:
            await self._stt_lost()

    async def _stt_lost(self) -> None:
        """The STT stream ended while the call is live: reopen, or degrade explicitly."""
        self._provider_failure("stt", ProviderError("stt", ErrorKind.UNAVAILABLE, "stream closed unexpectedly"))
        if self.d.stt is not None and self._stt_reopens < 2:
            self._stt_reopens += 1
            try:
                self._stt_stream = await self.d.stt.open_stream(self.c.lang.value, self.cfg.sample_rate)
                self._stt_task = asyncio.create_task(self._stt_loop(self._stt_stream))
                return
            except ProviderError as e:
                self._provider_failure("stt", e)
        self._stt_stream = None
        if self.c.state.channel == Channel.PHONE:
            await self.end("stt_unavailable")
        else:
            self.input_mode = "text"
            self.emit("input_mode", {"input_mode": "text", "reason": "speech recognition unavailable"})

    # ------------------------------------------------------------------ turn taking
    async def _on_speech_start(self) -> None:
        st = self.state
        if st == VoiceState.LISTENING:
            self.lifecycle.to(VoiceState.USER_SPEAKING, "vad_speech_start")
        elif st == VoiceState.INTERRUPTED:
            self.lifecycle.to(VoiceState.USER_SPEAKING, "vad_speech_start")
        elif st == VoiceState.PROCESSING and not self._applied and self._process_task and not self._process_task.done():
            # Caller kept talking before we answered: drop the in-flight interpretation
            # (it had no side effects) and merge the text into the continuing turn.
            self._process_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._process_task
            self.lifecycle.to(VoiceState.USER_SPEAKING, "caller_resumed_before_reply")
        self._speech_end_at = None

    async def _on_speech_end(self, at: float) -> None:
        if self.state in (VoiceState.USER_SPEAKING, VoiceState.INTERRUPTED):
            self._speech_end_at = at
            if self._stt_stream is not None:
                try:
                    await self._stt_stream.finalize()
                except ProviderError as e:
                    self._provider_failure("stt", e)
            await self._evaluate_turn()

    async def _evaluate_turn(self) -> None:
        if self.state not in (VoiceState.USER_SPEAKING, VoiceState.INTERRUPTED) or self.vad.speaking:
            return
        if self._speech_end_at is None:
            return
        silence_ms = (self._mono() - self._speech_end_at) * 1000
        text = " ".join(self._turn_text) if self._turn_text else self._partial
        decision = self.eot.decide(text, silence_ms, transcript_final=bool(self._turn_text))
        if decision == TurnDecision.COMPLETE:
            self._commit_turn(text, "voice", speech_end=self._speech_end_at, final_at=self._final_at)
        elif decision == TurnDecision.NOISE:
            self.emit("turn.discarded", {"reason": "no_words", "silence_ms": round(silence_ms)})
            self._reset_turn()
            self.lifecycle.to(VoiceState.LISTENING, "noise_discarded")

    def _reset_turn(self) -> None:
        self._turn_text = []
        self._partial = ""
        self._speech_end_at = None
        self._final_at = None

    def _accepting_turns(self) -> bool:
        """False once the conversation is over or handed off: late input must not reopen it."""
        return not (self.lifecycle.terminal or self.state == VoiceState.TRANSFER_REQUESTED or self.c.state.ended)

    def _commit_turn(self, text: str, mode: str, speech_end: float | None, final_at: float | None) -> None:
        if not self._accepting_turns():
            self._reset_turn()
            return
        full = " ".join(x for x in (self._carry_text, text) if x)
        self._carry_text = ""
        self._reset_turn()
        lat = TurnLatency(self.c.turn_index + 1, mode)
        now = self._mono()
        if speech_end is not None:
            lat.mark("speech_end", speech_end)
        lat.mark("final_transcript", final_at if final_at is not None else now)
        lat.mark("turn_commit", now)
        self._applied = False
        self._process_task = asyncio.create_task(self._process(full, lat))

    async def _process(self, text: str, lat: TurnLatency) -> None:
        if not self.lifecycle.can(VoiceState.PROCESSING) or not self._accepting_turns():
            return
        self.lifecycle.to(VoiceState.PROCESSING, "turn_committed")
        caller_turn = Turn(len(self.turns), "caller", text, self.d.controller.clock.now())
        self.turns.append(caller_turn)
        try:
            lat.mark("nlu_start", self._mono())
            res = await self.d.understanding.interpret(
                text, self.c.lang, self.c.today(), self.c.state.phase, self.c.last_agent_text
            )
            lat.mark("nlu_done", self._mono())
            async with self._lock:
                self._applied = True
                if res.llm_error:
                    self.c.audit.record(
                        AuditType.PROVIDER_FAILURE,
                        self.c.turn_index + 1,
                        provider="llm",
                        stage="nlu",
                        error=res.llm_error,
                        fallback="rules",
                    )
                outcome = self.c.apply(res.interpretation, text)
            lat.mark("policy_done", self._mono())
            caller_turn.intent = ",".join(a.value for a in res.interpretation.names)
            self.emit(
                "turn.caller",
                {
                    "index": caller_turn.index,
                    "text": text,
                    "turn_index": self.c.turn_index,
                    "interpretation": res.interpretation.model_dump(mode="json"),
                    "understanding": {"llm_used": res.llm_used, "notes": res.notes},
                },
            )
            self._emit_state()
        except asyncio.CancelledError:
            if not self._applied:
                # Interpretation had no side effects; keep the words for the merged turn.
                if caller_turn in self.turns:
                    self.turns.remove(caller_turn)
                self._carry_text = text
            raise
        await self._respond(outcome, lat)

    # ------------------------------------------------------------------ speaking
    async def _respond(self, outcome: TurnOutcome, lat: TurnLatency) -> None:
        real = await self.d.realizer.realize(outcome.plan)
        if real.first_token_at is not None:
            lat.mark("response_first_token", real.first_token_at)
        lat.mark("response_ready", self._mono())
        if real.source == "template_fallback":
            self.c.audit.record(
                AuditType.RESPONSE_GUARD_BLOCKED, self.c.turn_index, violations=real.guard_violations, error=real.error
            )
        self.c.last_agent_text = real.text
        agent_turn = Turn(len(self.turns), "agent", real.text, self.d.controller.clock.now())
        agent_turn.intent = ",".join(a.value for a in outcome.plan.acts)
        self.turns.append(agent_turn)
        if Effect.SEND_PTP_CONFIRMATION in outcome.effects:
            self._spawn(self._send_ptp_confirmation())

        self.emit(
            "agent.speaking",
            {"index": agent_turn.index, "text": real.text, "acts": agent_turn.intent, "realizer": real.source},
        )
        try:
            interrupted, fraction = await self._speak(real.text, lat)
        except asyncio.CancelledError:
            # Session ended mid-utterance: keep the transcript/audit complete.
            agent_turn.interrupted = True
            agent_turn.spoken_text = ""
            self.emit(
                "turn.agent",
                {
                    "index": agent_turn.index,
                    "text": real.text,
                    "acts": agent_turn.intent,
                    "interrupted": True,
                    "spoken_text": None,
                    "realizer": real.source,
                    "turn_index": self.c.turn_index,
                    "cut_by": "session_end",
                },
            )
            raise
        agent_turn.interrupted = interrupted
        if interrupted:
            words = real.text.split(" ") if " " in real.text else list(real.text)
            k = int(len(words) * fraction)
            agent_turn.spoken_text = (" " if " " in real.text else "").join(words[:k])
        self.c.mark_delivered(outcome.plan, fraction)
        self.latencies.append(lat)
        self.emit(
            "turn.agent",
            {
                "index": agent_turn.index,
                "text": real.text,
                "acts": agent_turn.intent,
                "interrupted": interrupted,
                "spoken_text": agent_turn.spoken_text,
                "realizer": real.source,
                "turn_index": self.c.turn_index,
            },
        )
        self.emit(
            "latency",
            {
                "turn_index": lat.turn_index,
                "input_mode": lat.input_mode,
                "stages": lat.stages_ms(),
                "providers": self.d.provider_labels,
            },
        )
        self._emit_state()

        # Caller-rights effects execute even if the utterance was interrupted.
        if Effect.TRANSFER in outcome.effects:
            await self._transfer()
            return
        if Effect.END_CALL in outcome.effects:
            await self.end(self.c.state.ended_reason or "completed")
            return
        if not interrupted and self.state == VoiceState.AGENT_SPEAKING:
            self.lifecycle.to(VoiceState.LISTENING, "agent_finished")
        if self._pending_text:
            text, self._pending_text = self._pending_text, None
            await self.on_text(text)

    async def _speak(self, text: str, lat: TurnLatency) -> tuple[bool, float]:
        self.gen += 1
        pb = _Playback(self.gen, text, estimate_s=max(0.5, len(text) / (7.0 if self.c.lang.value == "ja" else 15.0)))
        self._playback = pb
        if self.state != VoiceState.AGENT_SPEAKING:
            self.lifecycle.to(VoiceState.AGENT_SPEAKING, "tts_start")
        pb.task = asyncio.create_task(self._play(pb, lat))
        if self._pending_text:
            self._spawn(self.on_text(self._pending_text))
            self._pending_text = None
        await asyncio.wait({pb.task})
        if not pb.task.cancelled() and pb.task.exception() is not None:
            self._provider_failure("tts", pb.task.exception())  # type: ignore[arg-type]
            self.emit("agent.text_only", {"text": text})
            # Browser callers see the text on screen; a phone caller heard nothing.
            return False, (0.0 if self.c.state.channel == Channel.PHONE else 1.0)
        return pb.interrupted, pb.fraction(self._mono())

    async def _play(self, pb: _Playback, lat: TurnLatency) -> None:
        lat.mark("tts_request", self._mono())
        self._log("tts_started", generation=pb.generation, chars=len(pb.text), turn_index=self.c.turn_index)
        sr = self.d.tts.sample_rate
        ctx = f"{self.c.state.session_id}:{pb.generation}"
        # aclosing(): when playback is cancelled (barge-in) the provider generator is
        # closed immediately, so real adapters send their cancel message right away.
        async with contextlib.aclosing(self.d.tts.synthesize(pb.text, self.c.lang.value, context_id=ctx)) as stream:
            async for chunk in stream:
                if pb.generation != self.gen:
                    return  # stale generation: never send
                now = self._mono()
                if "tts_first_audio" not in lat.marks:
                    self._log(
                        "first_tts_audio",
                        generation=pb.generation,
                        ms_after_request=round((now - lat.marks["tts_request"]) * 1000, 1),
                    )
                lat.mark("tts_first_audio", now)
                if pb.started_at is None:
                    pb.started_at = now
                ahead = pb.sent_s - (now - pb.started_at)
                if ahead > self.cfg.playback_lead_s:
                    await self.d.sleep(ahead - self.cfg.playback_lead_s)
                    if pb.generation != self.gen:
                        return
                await self.transport.send_audio(chunk, pb.generation)
                lat.mark("first_audio_sent", self._mono())
                pb.sent_s += len(chunk) / 2 / sr
        pb.synthesis_complete = True
        if pb.started_at is not None:
            remaining = pb.sent_s - (self._mono() - pb.started_at)
            if remaining > 0:
                await self.d.sleep(remaining)

    async def barge_in(self, source: str) -> None:
        pb = self._playback
        if self.state != VoiceState.AGENT_SPEAKING or pb is None or pb.task is None or pb.task.done():
            return
        detected = self._mono()
        self.lifecycle.to(VoiceState.INTERRUPTED, source)
        pb.interrupted = True
        # Invalidate first, then cancel: anything already in flight is dropped.
        self.gen += 1
        cancel_requested = self._mono()
        pb.task.cancel()
        with contextlib.suppress(Exception):
            await self.transport.clear_audio(self.gen)
        await asyncio.wait({pb.task})
        stopped = self._mono()
        b = BargeIn(self.c.turn_index, source, detected, cancel_requested, stopped, pb.played_s(stopped), pb.text)
        self.barge_ins.append(b)
        self.c.audit.record(AuditType.BARGE_IN, self.c.turn_index, **b.to_dict())
        self.emit("barge_in", b.to_dict())
        if self.vad.speaking:
            self.lifecycle.to(VoiceState.USER_SPEAKING, "caller_speaking_after_barge_in")

    # ------------------------------------------------------------------ effects
    async def _transfer(self) -> None:
        if self.state != VoiceState.TRANSFER_REQUESTED:
            if self.lifecycle.can(VoiceState.TRANSFER_REQUESTED):
                self.lifecycle.to(VoiceState.TRANSFER_REQUESTED, "controller_effect")
            else:  # e.g. INTERRUPTED/USER_SPEAKING: route via PROCESSING
                self.lifecycle.to(VoiceState.PROCESSING, "transfer_effect")
                self.lifecycle.to(VoiceState.TRANSFER_REQUESTED, "controller_effect")
        tel = self.d.telephony
        if (
            self.c.state.channel == Channel.PHONE
            and tel is not None
            and tel.live
            and self.call_id
            and self.cfg.transfer_number
        ):
            self.c.mark_transfer_status(TransferStatus.DIALING, "twilio call update")
            try:
                res = await tel.transfer(self.call_id, self.cfg.transfer_number)
                self.c.mark_transfer_status(TransferStatus.DIALING if res.ok else TransferStatus.FAILED, res.detail)
            except Exception as e:
                self._provider_failure("telephony", e)
                self.c.mark_transfer_status(TransferStatus.FAILED, str(e)[:120])
        else:
            self.c.mark_transfer_status(
                TransferStatus.SIMULATED, "no live telephony/transfer number: transfer recorded at domain level only"
            )
        self._emit_state()
        await self.end("transferred_to_human")

    async def _send_ptp_confirmation(self) -> None:
        p = self.c.state.promise
        if p is None:
            return
        try:
            res = await self.d.notifier.send(
                NotificationChannel.SMS,
                self.c.debtor.phone_e164,
                "ptp_confirmation",
                {
                    "amount": p.amount,
                    "currency": p.currency,
                    "due_date": p.due_date.isoformat(),
                    "promise_id": str(p.promise_id),
                    "language": self.c.lang.value,
                },
            )
            self.emit(
                "notification",
                {
                    "channel": res.channel.value,
                    "provider": res.provider,
                    "delivered": res.delivered,
                    "detail": res.detail,
                },
            )
        except Exception as e:
            self._provider_failure("notifier", e)

    def _provider_failure(self, provider: str, e: BaseException) -> None:
        msg = str(e)[:200]
        self.errors.append(f"{provider}: {msg}")
        self._log("provider_error", logging.WARNING, provider=provider, error_type=type(e).__name__, error=msg)
        self.c.audit.record(AuditType.PROVIDER_FAILURE, self.c.turn_index, provider=provider, error=msg)
        self.emit("error", {"provider": provider, "message": msg, "degraded": True})

    # ------------------------------------------------------------------ timers
    async def tick(self) -> None:
        """Periodic housekeeping; called by the background loop or directly by tests."""
        if self.lifecycle.terminal:
            return
        now = self._mono()
        if now - self._started_at > self.cfg.max_session_s:
            await self.end("max_session_duration")
            return
        if self.state in (VoiceState.USER_SPEAKING, VoiceState.INTERRUPTED):
            await self._evaluate_turn()
        elif (
            self.state == VoiceState.LISTENING
            and self.input_mode == "voice"
            and now - self._listening_since > self.cfg.silence_timeout_s
        ):
            self._listening_since = now
            lat = TurnLatency(self.c.turn_index, "silence")
            lat.mark("turn_commit", now)
            self.lifecycle.to(VoiceState.PROCESSING, "silence_timeout")
            outcome = self.c.on_silence()
            self._applied = True
            self._process_task = asyncio.create_task(self._respond(outcome, lat))

    async def _tick_loop(self) -> None:
        while not self.lifecycle.terminal:
            await asyncio.sleep(self.cfg.tick_s)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("tick_failed")

    async def wait_idle(self) -> None:
        """Test helper: wait until the current processing/speaking task finishes."""
        while self._process_task is not None and not self._process_task.done():
            await asyncio.wait({self._process_task})
