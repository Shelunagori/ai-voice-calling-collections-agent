"""Barge-in in noisy places (street noise, horns, nearby talkers).

Loud sound without words must not cut the agent off while a recognizer is attached: the
agent's audio is only ducked and restored when the noise stops. If an automatic barge-in
does happen and the caller's "turn" turns out to be noise, the agent repeats what it was
saying instead of going silent.
"""

from __future__ import annotations

from app.domain.models import PromiseStatus
from app.voice import audio
from app.voice.lifecycle import VoiceState
from tests.conftest import drain, make_session

SR = 16000


async def _until_agent_talking(sess, clk, min_sent_s: float = 0.5):
    for _ in range(400):
        await clk.advance_async(0.02)
        if sess.state == VoiceState.AGENT_SPEAKING and sess._playback and sess._playback.sent_s > min_sent_s:
            return
    raise AssertionError("agent never started speaking")


async def _feed(sess, clk, pcm: bytes, watch=None):
    for fr in audio.frames(pcm, SR):
        await sess.on_audio(fr)
        await clk.advance_async(0.02)
        if watch:
            watch()


async def test_loud_noise_without_words_ducks_but_does_not_cut_the_agent():
    # no STT script: the recognizer hears no words in this "speech" (a horn / a passer-by)
    sess, tr, clk, _ = make_session("A", input_mode="voice", tts_cps=10.0)
    await sess.start(run_background=False)
    await _until_agent_talking(sess, clk)
    pb = sess._playback
    gains: list[float] = []
    await _feed(sess, clk, audio.synth_speechlike(1.0, SR), watch=lambda: gains.append(pb.gain))
    assert not sess.barge_ins, "noise without words cut the agent off"
    assert min(gains) == sess.cfg.duck_gain, "agent audio was not ducked while noise was heard"
    await _feed(sess, clk, audio.silence(0.6, SR))
    assert pb.gain == 1.0, "volume not restored after the noise stopped"
    assert sess.ducked_noise == 1
    await drain(sess, clk)
    agent = [t for t in sess.turns if t.speaker == "agent"]
    assert agent and not agent[0].interrupted
    assert "INTERRUPTED" not in sess.lifecycle.path()
    await sess.end("done")


async def test_words_over_the_agent_still_barge_in():
    sess, tr, clk, _ = make_session("A", input_mode="voice", stt_script=["yes this is haruto"])
    await sess.start(run_background=False)
    await _until_agent_talking(sess, clk)
    await _feed(sess, clk, audio.synth_speechlike(1.0, SR) + audio.silence(1.0, SR))
    assert sess.barge_ins and sess.barge_ins[0].source == "stt_partial"
    await drain(sess, clk)
    assert sess.c.state.identity_status.value == "NAME_CONFIRMED"
    assert sess.resumed_after_noise == 0
    await sess.end("done")


async def test_agent_repeats_itself_after_a_barge_in_that_was_only_noise():
    sess, tr, clk, _ = make_session("A", input_mode="voice", tts_cps=10.0)
    sess.cfg.barge_in_require_words = False  # the no-recognizer path: energy alone cuts
    await sess.start(run_background=False)
    await _until_agent_talking(sess, clk)
    greeting = sess._playback.text
    await _feed(sess, clk, audio.synth_speechlike(0.6, SR) + audio.silence(2.5, SR))
    assert sess.barge_ins and sess.barge_ins[0].source == "vad_sustained_speech"
    await drain(sess, clk)
    agent = [t for t in sess.turns if t.speaker == "agent"]
    assert len(agent) == 2
    assert agent[0].interrupted
    assert agent[1].text == "Sorry, as I was saying: " + greeting
    assert not agent[1].interrupted
    assert sess.resumed_after_noise == 1
    assert "resume_after_noise" in [t.cause for t in sess.lifecycle.history]
    assert sess.state == VoiceState.LISTENING
    assert not [t for t in sess.turns if t.speaker == "caller"]
    await sess.end("done")


async def test_manual_interrupt_followed_by_noise_does_not_repeat():
    sess, tr, clk, _ = make_session("A", input_mode="voice", tts_cps=10.0)
    await sess.start(run_background=False)
    await _until_agent_talking(sess, clk)
    await sess.interrupt()
    await _feed(sess, clk, audio.synth_speechlike(0.6, SR) + audio.silence(2.5, SR))
    await drain(sess, clk)
    assert sess.resumed_after_noise == 0
    assert len([t for t in sess.turns if t.speaker == "agent"]) == 1
    await sess.end("done")


async def test_resumed_readback_counts_as_fully_delivered():
    """A read-back cut off by noise and then repeated in full can be confirmed with one yes."""
    sess, tr, clk, _ = make_session("A", input_mode="voice", tts_cps=10.0)
    sess.cfg.barge_in_require_words = False
    await sess.start(run_background=False)
    await drain(sess, clk)
    for line in ["yes", "April 12, 1988"]:
        await sess.on_text(line)
        await drain(sess, clk)
    await sess.on_text("I can pay 30,000 yen in two weeks")
    await _until_agent_talking(sess, clk, min_sent_s=0.3)
    await _feed(sess, clk, audio.synth_speechlike(0.6, SR) + audio.silence(2.5, SR))
    await drain(sess, clk)
    assert sess.resumed_after_noise == 1
    assert sess.c.state.readback_delivered
    await sess.on_text("yes")
    await drain(sess, clk)
    assert sess.c.state.promise_status == PromiseStatus.CONFIRMED
    await sess.end("done")
