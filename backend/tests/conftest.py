from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

os.environ.setdefault("APP_ENV", "test")

from app.config import settings_for_tests
from app.domain.audit import AuditLog
from app.domain.clock import FakeClock, VirtualClock
from app.domain.controller import ConversationController
from app.domain.models import Channel, Language
from app.domain.policy import PolicyEngine
from app.domain.scenarios import get_scenario
from app.providers.mock import FakeTelephony, MockLLM, MockNotifier, MockSTT, MockTTS
from app.runtime import Providers, build_session


@dataclass
class RecordingTransport:
    audio: list[tuple[int, int]] = field(default_factory=list)  # (generation, bytes)
    clears: list[int] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    current_gen: int = 0
    dropped_stale: int = 0

    async def send_audio(self, pcm16: bytes, generation: int) -> None:
        # Mirrors the browser client: frames from an older generation are dropped.
        if generation < self.current_gen:
            self.dropped_stale += 1
            return
        self.current_gen = generation
        self.audio.append((generation, len(pcm16)))

    async def clear_audio(self, generation: int) -> None:
        self.clears.append(generation)
        self.current_gen = generation

    async def send_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def of(self, type_: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == type_]


def make_controller(key: str = "A", lang: Language = Language.EN, clock: FakeClock | None = None):
    sc = get_scenario(key)
    clk = clock or FakeClock()
    sid = uuid.uuid4()
    c = ConversationController(
        session_id=sid,
        debtor=sc.debtor,
        account=sc.account,
        language=lang,
        channel=Channel.EVAL,
        policy=PolicyEngine(),
        clock=clk,
        audit=AuditLog(sid, clk.now),
    )
    return c, clk


def say(c: ConversationController, text: str, delivered: float = 1.0):
    """Interpret with the deterministic parser, apply, and mark the reply as played."""
    from app.domain import nlu_rules

    interp = nlu_rules.interpret(text, c.lang, c.today(), expecting_dob=c.state.phase.value == "IDENTITY_DOB")
    out = c.apply(interp, text)
    c.mark_delivered(out.plan, delivered)
    return out


def verify(c: ConversationController) -> None:
    c.start()
    dob = c.debtor.date_of_birth
    say(c, "yes this is me")
    say(c, f"{dob:%B} {dob.day}, {dob.year}")
    assert c.state.identity_status.value == "VERIFIED"


@pytest.fixture
def vclock() -> VirtualClock:
    return VirtualClock()


def make_session(
    key: str = "A",
    lang: Language = Language.EN,
    clock: VirtualClock | None = None,
    input_mode: str = "text",
    stt_script: list[str] | None = None,
    llm: MockLLM | None = None,
    tts_cps: float = 40.0,
    settings_overrides: dict[str, Any] | None = None,
    channel: Channel = Channel.BROWSER,
    call_id: str | None = None,
):
    clk = clock or VirtualClock()
    s = settings_for_tests(**(settings_overrides or {}))
    tel = FakeTelephony()
    providers = Providers(
        llm=llm,
        stt=MockSTT(clk.monotonic, stt_script or [], sleep=clk.sleep),
        tts=MockTTS(16000, chars_per_s_en=tts_cps, chars_per_s_ja=tts_cps / 2, sleep=clk.sleep),
        telephony=tel,
        notifier=MockNotifier(),
        labels={"llm": "mock", "stt": "mock", "tts": "mock"},
    )
    sc = get_scenario(key)
    transport = RecordingTransport()
    sess = build_session(
        settings=s,
        providers=providers,
        debtor=sc.debtor,
        account=sc.account,
        language=lang,
        channel=channel,
        transport=transport,
        clock=clk,
        sleep=clk.sleep,
        input_mode=input_mode,
        call_id=call_id,
    )
    return sess, transport, clk, providers


async def drain(sess, clk: VirtualClock, max_s: float = 30.0, step: float = 0.05) -> None:
    """Advance virtual time until the session is idle (listening/ended)."""
    from app.voice.lifecycle import VoiceState

    t = 0.0
    while t < max_s:
        await clk.advance_async(step)
        await sess.tick()
        t += step
        busy = sess._process_task is not None and not sess._process_task.done()
        if not busy and sess.state in (VoiceState.LISTENING, VoiceState.ENDED, VoiceState.INTERRUPTED):
            return
    raise AssertionError(f"session did not become idle; state={sess.state}")
