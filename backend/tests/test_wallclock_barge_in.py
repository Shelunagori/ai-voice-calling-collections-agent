"""Wall-clock measurement of the barge-in cancel path with the real asyncio loop and
SystemClock (not virtual time). This measures *our* code path (detect -> cancel ->
transport clear -> TTS task stopped); provider-side stop latency is not included."""

from __future__ import annotations

import asyncio
import time

from app.config import settings_for_tests
from app.domain.clock import SystemClock
from app.domain.models import Channel, Language
from app.domain.scenarios import get_scenario
from app.providers.mock import FakeTelephony, MockNotifier, MockTTS
from app.runtime import Providers, build_session
from tests.conftest import RecordingTransport


async def test_barge_in_cancel_latency_wall_clock():
    clock = SystemClock()
    providers = Providers(None, None, MockTTS(16000), FakeTelephony(), MockNotifier(), {"tts": "mock"})
    sc = get_scenario("A")
    tr = RecordingTransport()
    sess = build_session(
        settings=settings_for_tests(),
        providers=providers,
        debtor=sc.debtor,
        account=sc.account,
        language=Language.EN,
        channel=Channel.EVAL,
        transport=tr,
        clock=clock,
    )
    await sess.start(run_background=False)
    for _ in range(100):
        await asyncio.sleep(0.01)
        if sess._playback and sess._playback.sent_s > 0.3:
            break
    t0 = time.perf_counter()
    await sess.interrupt()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    b = sess.barge_ins[0]
    measured = (b.tts_stopped_at - b.barge_in_detected_at) * 1000
    assert measured < 50, measured
    assert elapsed_ms < 50, elapsed_ms
    n = len(tr.audio)
    await asyncio.sleep(0.3)
    assert len(tr.audio) == n  # nothing sent after the cancel
    await sess.end("done")
