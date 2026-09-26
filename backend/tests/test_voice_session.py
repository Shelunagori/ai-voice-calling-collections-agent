from __future__ import annotations

import asyncio

from app.domain.audit import AuditType
from app.domain.models import Channel, Language, PromiseStatus
from app.voice import audio
from app.voice.lifecycle import VoiceState
from tests.conftest import drain, make_session


async def test_typed_happy_path_reaches_confirmed_promise():
    sess, tr, clk, prov = make_session("A")
    await sess.start(run_background=False)
    await drain(sess, clk)
    for line in ["Yes, this is Haruto.", "April 12, 1988", "I can pay 30,000 yen in two weeks", "Yes, correct"]:
        await sess.on_text(line)
        await drain(sess, clk)
    st = sess.c.state
    assert st.promise_status == PromiseStatus.CONFIRMED
    assert st.promise is not None and st.promise.amount == 30_000
    assert prov.notifier.outbox and prov.notifier.outbox[0]["template"] == "ptp_confirmation"
    path = sess.lifecycle.path()
    assert path[:4] == ["IDLE", "PROCESSING", "AGENT_SPEAKING", "LISTENING"]
    # every agent turn produced audio and latency was measured, not invented
    assert tr.audio
    lat = [x for x in sess.latencies if x.input_mode == "text"]
    assert lat and all("turn_commit_to_first_audio" in x.stages_ms() for x in lat)
    await sess.end("test_done")
    assert sess.state == VoiceState.ENDED


async def _speak_until_agent_talking(sess, clk):
    await sess.start(run_background=False)
    for _ in range(200):
        await clk.advance_async(0.02)
        if sess.state == VoiceState.AGENT_SPEAKING and sess._playback and sess._playback.sent_s > 0.5:
            return
    raise AssertionError("agent never started speaking")


async def test_barge_in_cancels_tts_and_drops_stale_audio():
    sess, tr, clk, prov = make_session("F", input_mode="voice", stt_script=["yes this is mei"])
    await _speak_until_agent_talking(sess, clk)
    gen_before = sess.gen
    # caller starts talking over the greeting
    speech = audio.synth_speechlike(1.2, 16000)
    barge_seen = False
    for fr in audio.frames(speech, 16000):
        await sess.on_audio(fr)
        await clk.advance_async(0.02)
        if sess.barge_ins:
            barge_seen = True
    assert barge_seen, "barge-in not detected"
    b = sess.barge_ins[0]
    assert b.barge_in_detected_at <= b.tts_cancel_requested_at <= b.tts_stopped_at
    assert sess.gen > gen_before
    assert tr.clears == [sess.gen] or tr.clears[0] > gen_before
    # no audio from the interrupted generation after the cancel request
    sent_after = [g for g, _ in tr.audio if g == gen_before]
    count_at_cancel = len(sent_after)
    for fr in audio.frames(audio.silence(1.5, 16000), 16000):
        await sess.on_audio(fr)
        await clk.advance_async(0.02)
    assert len([g for g, _ in tr.audio if g == gen_before]) == count_at_cancel
    assert prov.tts.cancelled, "TTS generation was not cancelled"
    assert any(e.type == AuditType.BARGE_IN for e in sess.c.audit.events)
    agent_turns = [t for t in sess.turns if t.speaker == "agent"]
    assert agent_turns[0].interrupted
    # caller's new turn was accepted and processed after the interruption
    await drain(sess, clk)
    assert sess.c.state.identity_status.value == "NAME_CONFIRMED"
    assert "INTERRUPTED" in sess.lifecycle.path()
    await sess.end("done")


async def test_short_noise_does_not_barge_in():
    sess, tr, clk, _ = make_session("A", input_mode="voice")
    await _speak_until_agent_talking(sess, clk)
    click = audio.synth_speechlike(0.12, 16000)  # 120 ms: cough / click
    for fr in audio.frames(click + audio.silence(0.5, 16000), 16000):
        await sess.on_audio(fr)
        await clk.advance_async(0.02)
    assert not sess.barge_ins


async def test_background_noise_floor_does_not_trigger_speech():
    sess, tr, clk, _ = make_session("A", input_mode="voice")
    await sess.start(run_background=False)
    await drain(sess, clk)
    noise = audio.synth_noise(3.0, 16000, amp=0.01)
    for fr in audio.frames(noise, 16000):
        await sess.on_audio(fr)
        await clk.advance_async(0.02)
    assert sess.state == VoiceState.LISTENING
    assert not [t for t in sess.turns if t.speaker == "caller"]


async def test_interrupted_readback_yes_does_not_confirm_promise():
    """A 'yes' spoken over an unfinished confirmation read-back is not consent."""
    sess, tr, clk, _ = make_session("A", tts_cps=10.0)
    await sess.start(run_background=False)
    await drain(sess, clk)
    for line in ["yes", "April 12, 1988", "30,000 yen in two weeks"]:
        await sess.on_text(line)
        await drain(sess, clk)
    # now the read-back is being spoken; wait until it has started, then say yes over it
    await sess.on_text("I can pay 30,000 yen in two weeks")
    for _ in range(40):
        await clk.advance_async(0.02)
        if sess.state == VoiceState.AGENT_SPEAKING:
            break
    await clk.advance_async(0.3)
    await sess.on_text("yes")
    await drain(sess, clk)
    assert sess.c.state.promise_status != PromiseStatus.CONFIRMED
    assert sess.c.state.promise is None
    assert sess.barge_ins
    # after the read-back finally plays in full, yes confirms exactly once
    await sess.on_text("yes")
    await drain(sess, clk)
    assert sess.c.state.promise_status == PromiseStatus.CONFIRMED
    promises = [e for e in sess.c.audit.events if e.type == AuditType.PROMISE_CONFIRMED]
    assert len(promises) == 1
    await sess.end("done")


async def test_voice_turn_with_scripted_stt_and_latency_marks():
    sess, tr, clk, _ = make_session("A", input_mode="voice", stt_script=["yes this is haruto"])
    await sess.start(run_background=False)
    await drain(sess, clk)
    utter = audio.synth_speechlike(0.8, 16000) + audio.silence(1.5, 16000)
    for fr in audio.frames(utter, 16000):
        await sess.on_audio(fr)
        await clk.advance_async(0.02)
        await sess.tick()
    await drain(sess, clk)
    assert sess.c.state.identity_status.value == "NAME_CONFIRMED"
    voice_lat = [x for x in sess.latencies if x.input_mode == "voice"]
    assert voice_lat
    stages = voice_lat[0].stages_ms()
    assert "speech_end_to_first_audio" in stages
    # end-of-turn waited for silence: at least the quick threshold, at most the ceiling
    assert 200 <= stages["speech_end_to_first_audio"] <= 2600


async def test_silence_reprompts_then_ends():
    sess, tr, clk, _ = make_session("A", input_mode="voice", settings_overrides={})
    sess.cfg.silence_timeout_s = 2.0
    await sess.start(run_background=False)
    await drain(sess, clk)
    for _ in range(600):
        await clk.advance_async(0.05)
        await sess.tick()
        if sess.state == VoiceState.ENDED:
            break
    assert sess.state == VoiceState.ENDED
    assert sess.c.state.ended_reason == "silence_timeout"


async def test_human_transfer_is_simulated_without_telephony():
    sess, tr, clk, prov = make_session("G")
    await sess.start(run_background=False)
    await drain(sess, clk)
    await sess.on_text("yes")
    await drain(sess, clk)
    await sess.on_text("I want to talk to a real person")
    await drain(sess, clk)
    st = sess.c.state
    assert st.human_transfer_requested and st.transfer_status.value == "SIMULATED"
    assert "TRANSFER_REQUESTED" in sess.lifecycle.path()
    assert sess.state == VoiceState.ENDED
    assert not prov.telephony.transfers  # browser session never touches telephony


async def test_phone_transfer_uses_telephony_provider_when_live():
    sess, tr, clk, prov = make_session("G", channel=Channel.PHONE, call_id="CA123")
    prov.telephony.live = True
    sess.cfg.transfer_number = "+15550000000"
    await sess.start(run_background=False)
    await drain(sess, clk)
    await sess.on_text("operator please")
    await drain(sess, clk)
    assert prov.telephony.transfers == [{"call_id": "CA123", "to": "+15550000000"}]
    assert sess.c.state.transfer_status.value == "DIALING"


async def test_japanese_session_flow():
    sess, tr, clk, _ = make_session("A", lang=Language.JA)
    await sess.start(run_background=False)
    await drain(sess, clk)
    for line in ["はい、本人です。", "1988年4月12日です。", "2週間後に3万円払えます。", "はい、お願いします。"]:
        await sess.on_text(line)
        await drain(sess, clk)
    assert sess.c.state.promise_status == PromiseStatus.CONFIRMED
    agent = [t.text for t in sess.turns if t.speaker == "agent"]
    assert "30,000円" in agent[-1]


async def test_end_is_idempotent_and_cleans_up_tasks():
    sess, tr, clk, _ = make_session("A", input_mode="voice")
    await sess.start(run_background=True)
    await clk.advance_async(0.2)
    await sess.end("caller_hangup")
    await sess.end("caller_hangup")
    await asyncio.sleep(0)
    assert sess.state == VoiceState.ENDED
    assert len([e for e in sess.c.audit.events if e.type == AuditType.SESSION_ENDED]) == 1
    assert sess._ticker is None or sess._ticker.done() or sess._ticker.cancelled()
