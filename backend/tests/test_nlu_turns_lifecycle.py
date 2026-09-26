from __future__ import annotations

from datetime import date

import pytest

from app.domain.commands import Action, Interpretation, ProposedAction
from app.domain.models import DialogPhase, Language
from app.domain.nlu_rules import interpret, safety_intents
from app.domain.understanding import Understanding, merge
from app.providers.mock import MockLLM
from app.voice import audio
from app.voice.lifecycle import IllegalTransition, Lifecycle, VoiceState
from app.voice.turn_detection import EndOfTurnDetector, TurnDecision
from app.voice.vad import EnergyVAD, VADEventType

T = date(2026, 10, 1)  # Thursday


@pytest.mark.parametrize(
    "text,lang,action,amount,due,days",
    [
        ("I can pay 30,000 yen on October 15", Language.EN, Action.PROPOSE_PAYMENT, 30000, date(2026, 10, 15), None),
        ("twenty five thousand yen", Language.EN, Action.PROPOSE_PAYMENT, 25000, None, None),
        ("20k next Friday", Language.EN, Action.PROPOSE_PAYMENT, 20000, date(2026, 10, 9), None),
        ("can I have 60 days", Language.EN, Action.PROPOSE_PAYMENT, None, None, 60),
        ("the 20th", Language.EN, Action.PROPOSE_PAYMENT, None, date(2026, 10, 20), None),
        ("10月15日に3万円払えます", Language.JA, Action.PROPOSE_PAYMENT, 30000, date(2026, 10, 15), None),
        ("２万５千円なら2週間後に", Language.JA, Action.PROPOSE_PAYMENT, 25000, None, 14),
        ("来週の金曜日に1万5千円で", Language.JA, Action.PROPOSE_PAYMENT, 15000, date(2026, 10, 9), None),
        ("三万円", Language.JA, Action.PROPOSE_PAYMENT, 30000, None, None),
    ],
)
def test_payment_extraction(text, lang, action, amount, due, days):
    a = interpret(text, lang, T).first(action)
    assert a is not None
    assert (a.amount, a.date, a.days_from_now) == (amount, due, days)


@pytest.mark.parametrize(
    "text,lang,dob",
    [
        ("April 12, 1988", Language.EN, date(1988, 4, 12)),
        ("12 April 1988", Language.EN, date(1988, 4, 12)),
        ("1988-04-12", Language.EN, date(1988, 4, 12)),
        ("1988年4月12日です", Language.JA, date(1988, 4, 12)),
        ("昭和63年4月12日", Language.JA, date(1988, 4, 12)),
    ],
)
def test_dob_extraction(text, lang, dob):
    a = interpret(text, lang, T, expecting_dob=True).first(Action.PROVIDE_DOB)
    assert a and a.dob == dob


@pytest.mark.parametrize(
    "text,lang,expected",
    [
        ("please stop calling me", Language.EN, Action.STOP_CONTACT),
        ("do not contact me again", Language.EN, Action.STOP_CONTACT),
        ("I want a real person", Language.EN, Action.REQUEST_HUMAN),
        ("もう電話しないで", Language.JA, Action.STOP_CONTACT),
        ("オペレーターをお願いします", Language.JA, Action.REQUEST_HUMAN),
        ("wrong number", Language.EN, Action.WRONG_PERSON),
        ("いいえ、大丈夫です", Language.JA, Action.DENY),
        ("はい、本人です", Language.JA, Action.AFFIRM),
        ("um", Language.EN, Action.UNCLEAR),
    ],
)
def test_intents(text, lang, expected):
    assert interpret(text, lang, T).names[0] == expected


def test_daijoubu_is_not_wrong_person():
    assert Action.WRONG_PERSON not in interpret("いいえ、大丈夫です", Language.JA, T).names


def test_safety_net_adds_stop_contact_missed_by_llm():
    llm = Interpretation(actions=[ProposedAction(action=Action.UNCLEAR)], source="llm")
    rules = interpret("stop calling me", Language.EN, T)
    merged, notes = merge(llm, rules)
    assert merged.has(Action.STOP_CONTACT) and "rules_added_stop_contact" in notes


def test_llm_amount_grounded_to_transcript():
    llm = Interpretation(actions=[ProposedAction(action=Action.PROPOSE_PAYMENT, amount=300000)], source="llm")
    rules = interpret("I can pay 30,000 yen", Language.EN, T)
    merged, notes = merge(llm, rules)
    assert merged.first(Action.PROPOSE_PAYMENT).amount == 30000


async def test_invalid_llm_output_falls_back_to_rules():
    u = Understanding(MockLLM(failure="invalid_json"), 1.0, lambda: 0.0)
    r = await u.interpret("stop calling me", Language.EN, T, DialogPhase.NEGOTIATION, "")
    assert not r.llm_used and r.llm_error and r.interpretation.has(Action.STOP_CONTACT)


async def test_llm_timeout_falls_back_to_rules():
    u = Understanding(MockLLM(failure="timeout"), 1.0, lambda: 0.0)
    r = await u.interpret("yes", Language.EN, T, DialogPhase.IDENTITY_NAME, "")
    assert r.interpretation.has(Action.AFFIRM) and "llm_failed_rules_fallback" in r.notes


async def test_mock_llm_path_validates_and_merges():
    u = Understanding(MockLLM(), 1.0, lambda: 0.0)
    r = await u.interpret("30,000 yen in two weeks", Language.EN, T, DialogPhase.NEGOTIATION, "")
    assert r.llm_used and r.interpretation.first(Action.PROPOSE_PAYMENT).amount == 30000


def test_safety_intents():
    assert safety_intents("I want to speak to a supervisor", Language.EN) == [Action.REQUEST_HUMAN]


# ------------------------------------------------------------------ end of turn
@pytest.mark.parametrize(
    "text,silence,final,expected",
    [
        ("yes", 300, True, TurnDecision.COMPLETE),  # short answer: quick
        ("yes", 300, False, TurnDecision.WAIT),  # partial transcript: wait for more
        ("I can pay 30,000 yen on", 700, True, TurnDecision.COMPLETE),
        ("I can pay 30,000 yen and", 700, True, TurnDecision.WAIT),  # thinking pause
        ("I can pay 30,000 yen and", 1600, True, TurnDecision.COMPLETE),
        ("um", 1600, True, TurnDecision.WAIT),
        ("um", 2600, True, TurnDecision.NOISE),
        ("", 500, False, TurnDecision.WAIT),
        ("", 1300, False, TurnDecision.NOISE),  # energy with no words: background speech
        ("えーと", 800, True, TurnDecision.WAIT),
        ("はい", 300, True, TurnDecision.COMPLETE),
        ("3万円なら払えますけど", 800, True, TurnDecision.WAIT),
        ("I can pay tomorrow", 2600, False, TurnDecision.COMPLETE),  # hard ceiling
    ],
)
def test_end_of_turn(text, silence, final, expected):
    assert EndOfTurnDetector().decide(text, silence, final) == expected


# ------------------------------------------------------------------ VAD
def _events(pcm):
    v = EnergyVAD(16000)
    out = []
    for fr in audio.frames(pcm, 16000):
        out += v.feed(fr)
    return out


def test_vad_detects_speech_and_short_pause():
    sr = 16000
    pcm = audio.silence(0.3, sr) + audio.synth_speechlike(0.6, sr) + audio.silence(0.15, sr)
    pcm += audio.synth_speechlike(0.6, sr) + audio.silence(0.5, sr)
    ev = _events(pcm)
    # a 150 ms pause is shorter than the hang time: one segment
    assert [e.type for e in ev] == [VADEventType.SPEECH_START, VADEventType.SPEECH_END]


def test_vad_long_pause_splits_segments():
    sr = 16000
    pcm = audio.silence(0.3, sr) + audio.synth_speechlike(0.6, sr) + audio.silence(0.8, sr)
    pcm += audio.synth_speechlike(0.6, sr) + audio.silence(0.5, sr)
    assert [e.type for e in _events(pcm)].count(VADEventType.SPEECH_START) == 2


def test_vad_ignores_stationary_noise_but_hears_speech_over_it():
    sr = 16000
    noise = audio.synth_noise(3.0, sr, amp=0.01)
    assert _events(noise) == []
    speech = audio.silence(1.0, sr) + audio.synth_speechlike(0.8, sr) + audio.silence(1.2, sr)
    ev = _events(audio.mix(noise, speech))
    assert [e.type for e in ev] == [VADEventType.SPEECH_START, VADEventType.SPEECH_END]


# ------------------------------------------------------------------ lifecycle
def test_lifecycle_rejects_illegal_transition():
    lc = Lifecycle(lambda: 0.0)
    lc.to(VoiceState.PROCESSING, "start")
    with pytest.raises(IllegalTransition):
        lc.to(VoiceState.INTERRUPTED, "nope")
    lc.to(VoiceState.AGENT_SPEAKING, "tts")
    lc.to(VoiceState.INTERRUPTED, "barge")
    lc.to(VoiceState.USER_SPEAKING, "speech")
    lc.to(VoiceState.PROCESSING, "eot")
    lc.to(VoiceState.TRANSFER_REQUESTED, "human")
    with pytest.raises(IllegalTransition):
        lc.to(VoiceState.LISTENING, "no way back")
    lc.to(VoiceState.ENDED, "done")
    assert lc.terminal
    with pytest.raises(IllegalTransition):
        lc.to(VoiceState.LISTENING, "after end")


def test_mulaw_roundtrip_and_resample():
    pcm = audio.synth_tone(0.1, 8000, amp=0.5)
    back = audio.ulaw_to_pcm16(audio.pcm16_to_ulaw(pcm))
    assert len(back) == len(pcm)
    assert abs(audio.rms_dbfs(back) - audio.rms_dbfs(pcm)) < 1.0
    up = audio.upsample_8k_to_16k(pcm)
    assert len(up) == 2 * len(pcm)
    assert len(audio.downsample_16k_to_8k(up)) == len(pcm)
