"""Wiring: build a fully-assembled `VoiceSession` from settings + providers.

Shared by the browser WebSocket, the Twilio media stream, the evaluation harness
and the tests, so all of them exercise exactly the same runtime.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .domain.audit import AuditLog
from .domain.clock import Clock
from .domain.controller import ConversationController
from .domain.models import AccountTerms, Channel, DebtorProfile, Language
from .domain.policy import PolicyConfig, PolicyEngine
from .domain.realizer import LLMRealizer, TemplateRealizer
from .domain.understanding import Understanding
from .providers.base import LLMProvider, Notifier, STTProvider, TelephonyProvider, TTSProvider
from .voice.session import Recorder, RuntimeConfig, SessionDeps, Sleep, Transport, VoiceSession


@dataclass
class Providers:
    llm: LLMProvider | None
    stt: STTProvider | None
    tts: TTSProvider
    telephony: TelephonyProvider
    notifier: Notifier
    labels: dict[str, str] = field(default_factory=dict)


def policy_from_settings(s: Settings) -> PolicyEngine:
    return PolicyEngine(
        PolicyConfig(
            timezone=s.policy_timezone,
            calling_start_hour=s.policy_calling_start_hour,
            calling_end_hour=s.policy_calling_end_hour,
            max_contact_attempts=s.policy_max_contact_attempts,
            max_identity_attempts=s.policy_max_identity_attempts,
        )
    )


def build_session(
    *,
    settings: Settings,
    providers: Providers,
    debtor: DebtorProfile,
    account: AccountTerms,
    language: Language,
    channel: Channel,
    transport: Transport,
    clock: Clock,
    sleep: Sleep = asyncio.sleep,
    recorder: Recorder | None = None,
    input_mode: str = "text",
    session_id: uuid.UUID | None = None,
    call_id: str | None = None,
    use_llm: bool = True,
    runtime_overrides: dict[str, Any] | None = None,
) -> VoiceSession:
    sid = session_id or uuid.uuid4()
    audit = AuditLog(sid, clock.now)
    controller = ConversationController(
        session_id=sid,
        debtor=debtor,
        account=account,
        language=language,
        channel=channel,
        policy=policy_from_settings(settings),
        clock=clock,
        audit=audit,
    )
    llm = providers.llm if use_llm else None
    understanding = Understanding(llm, settings.llm_timeout_s, clock.monotonic)
    realizer: TemplateRealizer | LLMRealizer
    if settings.response_mode == "llm" and llm is not None:
        realizer = LLMRealizer(llm, clock.monotonic, settings.llm_timeout_s)
    else:
        realizer = TemplateRealizer(clock.monotonic)
    cfg = RuntimeConfig(
        sample_rate=settings.audio_sample_rate,
        barge_in_min_speech_ms=settings.barge_in_min_speech_ms,
        transfer_number=settings.twilio_transfer_number,
        **(runtime_overrides or {}),
    )
    deps = SessionDeps(
        controller=controller,
        understanding=understanding,
        realizer=realizer,
        tts=providers.tts,
        stt=providers.stt,
        telephony=providers.telephony,
        notifier=providers.notifier,
        monotonic=clock.monotonic,
        sleep=sleep,
        provider_labels=dict(providers.labels),
    )
    if recorder is not None:
        deps.recorder = recorder
    return VoiceSession(deps, transport, cfg, input_mode=input_mode, call_id=call_id)
