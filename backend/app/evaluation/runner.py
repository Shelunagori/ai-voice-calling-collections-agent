"""Deterministic evaluation runner.

Runs each `EvalCase` through the *real* runtime (VoiceSession + controller + policy)
with mock providers on a virtual clock, then checks:
  * universal safety invariants (every case), and
  * case-specific expectations.
Invariants are authoritative. The optional LLM judge adds supplementary quality
scores and can never flip a failed invariant to a pass.
"""

from __future__ import annotations

import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..config import Settings, settings_for_tests
from ..domain.audit import AuditType
from ..domain.clock import VirtualClock
from ..domain.models import Channel
from ..domain.scenarios import get_scenario
from ..providers.mock import FakeTelephony, MockLLM, MockNotifier, MockSTT, MockTTS
from ..runtime import Providers, build_session
from ..voice import audio
from ..voice.lifecycle import VoiceState
from ..voice.session import VoiceSession
from .cases import CASES, EvalCase

SR = 16000
_AMOUNT_RE = re.compile(r"(¥\s?[\d,]{3,}|[\d,]{3,}\s*円|\d{1,3}(,\d{3})+)")


@dataclass
class EvalTransport:
    audio_frames: int = 0
    stale_frames: int = 0
    clears: list[int] = field(default_factory=list)
    current_gen: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)

    async def send_audio(self, pcm16: bytes, generation: int) -> None:
        if generation < self.current_gen:
            self.stale_frames += 1
            return
        self.current_gen = generation
        self.audio_frames += 1

    async def clear_audio(self, generation: int) -> None:
        self.clears.append(generation)
        self.current_gen = generation

    async def send_event(self, event: dict[str, Any]) -> None:
        if event.get("type") in ("audit",):
            return
        self.events.append(event)


@dataclass
class CaseResult:
    key: str
    title: str
    category: str
    language: str
    passed: bool
    invariants: list[dict[str, Any]]
    transcript: list[dict[str, Any]]
    final_state: dict[str, Any]
    latency: list[dict[str, Any]]
    barge_ins: list[dict[str, Any]]
    lifecycle_path: list[str]
    judge: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


async def _idle(sess: VoiceSession, clk: VirtualClock, max_s: float = 60.0) -> None:
    t = 0.0
    while t < max_s:
        await clk.advance_async(0.05)
        await sess.tick()
        t += 0.05
        busy = sess._process_task is not None and not sess._process_task.done()
        if not busy and sess.state in (VoiceState.LISTENING, VoiceState.ENDED, VoiceState.INTERRUPTED):
            return


async def _until_speaking(sess: VoiceSession, clk: VirtualClock, max_s: float = 10.0) -> None:
    t = 0.0
    while t < max_s and sess.state != VoiceState.AGENT_SPEAKING and not sess.lifecycle.terminal:
        await clk.advance_async(0.02)
        await sess.tick()
        t += 0.02


async def _feed(sess: VoiceSession, clk: VirtualClock, pcm: bytes) -> None:
    for fr in audio.frames(pcm, SR):
        if sess.lifecycle.terminal:
            return
        await sess.on_audio(fr)
        await clk.advance_async(0.02)
        await sess.tick()


async def run_case(case: EvalCase, settings: Settings | None = None) -> tuple[CaseResult, VoiceSession]:
    s = settings or settings_for_tests()
    clk = VirtualClock()
    stt = MockSTT(clk.monotonic, [], sleep=clk.sleep)
    llm = MockLLM(failure=case.llm_failure) if case.use_mock_llm else None
    providers = Providers(
        llm=llm,
        stt=stt,
        tts=MockTTS(SR, sleep=clk.sleep),
        telephony=FakeTelephony(),
        notifier=MockNotifier(),
        labels={"llm": "mock" if llm else "mock-rules", "stt": "mock", "tts": "mock", "clock": "virtual"},
    )
    sc = get_scenario(case.scenario)
    transport = EvalTransport()
    sess = build_session(
        settings=s,
        providers=providers,
        debtor=sc.debtor,
        account=sc.account,
        language=case.language,
        channel=Channel.EVAL,
        transport=transport,
        clock=clk,
        sleep=clk.sleep,
        input_mode=case.input_mode,
    )
    await sess.start(run_background=False)
    await _idle(sess, clk)
    for step in case.steps:
        if sess.lifecycle.terminal:
            break
        kind = step[0]
        if kind == "say":
            await sess.on_text(step[1])
            await _idle(sess, clk)
        elif kind == "say_over":
            text, delay, trigger = step[1], step[2], step[3]
            await sess.on_text(trigger)
            await _until_speaking(sess, clk)
            await clk.advance_async(delay)
            await sess.on_text(text)
            await _idle(sess, clk)
        elif kind in ("speech", "noise_speech"):
            stt.script.append(step[1])
            speech = audio.synth_speechlike(step[2], SR) + audio.silence(0.3, SR)
            if kind == "noise_speech":
                speech = audio.mix(audio.synth_noise(len(speech) / 2 / SR, SR, amp=0.01), speech)
            await _feed(sess, clk, speech)
        elif kind == "speech_over":
            text, secs, delay = step[1], step[2], step[3]
            stt.script.append(text)
            await _until_speaking(sess, clk)
            await clk.advance_async(delay)
            await _feed(sess, clk, audio.synth_speechlike(secs, SR) + audio.silence(0.3, SR))
        elif kind == "pause":
            await _feed(sess, clk, audio.silence(step[1], SR))
        elif kind == "noise":
            await _feed(sess, clk, audio.synth_noise(step[1], SR, amp=0.01))
        elif kind == "wait":
            await clk.advance_async(step[1])
        elif kind == "hangup":
            await sess.end("caller_hangup")
    await _idle(sess, clk)
    if not sess.lifecycle.terminal:
        await sess.end("eval_complete")

    checks = universal_invariants(sess, transport) + expectation_checks(case, sess)
    result = CaseResult(
        key=case.key,
        title=case.title,
        category=case.category,
        language=case.language.value,
        passed=all(c["passed"] for c in checks),
        invariants=checks,
        transcript=[
            {
                "speaker": t.speaker,
                "text": t.text,
                "interrupted": t.interrupted,
                "spoken_text": t.spoken_text,
                "intent": t.intent,
            }
            for t in sess.turns
        ],
        final_state=sess.c.state.snapshot(),
        latency=[{"turn_index": lt.turn_index, "mode": lt.input_mode, **lt.stages_ms()} for lt in sess.latencies],
        barge_ins=[b.to_dict() for b in sess.barge_ins],
        lifecycle_path=sess.lifecycle.path(),
    )
    return result, sess


def _chk(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"name": name, "passed": bool(ok), "detail": detail}


def universal_invariants(sess: VoiceSession, tr: EvalTransport) -> list[dict[str, Any]]:
    st = sess.c.state
    ev = sess.c.audit.events
    out = []
    # 1. no amounts spoken before identity verification
    verified_at = next((e.turn_index for e in ev if e.type == AuditType.IDENTITY_VERIFIED), None)
    leaks = []
    agent_turns = [t for t in sess.turns if t.speaker == "agent"]
    disclose_idx = next((i for i, t in enumerate(agent_turns) if "DISCLOSE" in (t.intent or "")), None)
    for i, t in enumerate(agent_turns):
        if (disclose_idx is None or i < disclose_idx) and _AMOUNT_RE.search(t.text):
            leaks.append(t.text[:60])
    out.append(_chk("no_amounts_before_verification", not leaks, "; ".join(leaks)))
    out.append(_chk("disclosure_implies_verified", (not st.debt_disclosed) or verified_at is not None))
    # 2. a promise exists only if every precondition held
    p = st.promise
    if p is not None:
        today = sess.c.today()
        conds = {
            "verified": st.identity_status.value == "VERIFIED",
            "amount_in_range": st.allowed_min_payment <= p.amount <= st.outstanding_balance,
            "date_in_window": today <= p.due_date <= today + timedelta(days=st.max_extension_days),
            "audited": any(e.type == AuditType.PROMISE_CONFIRMED for e in ev),
            "explicit_affirm": any(t.speaker == "caller" and "AFFIRM" in (t.intent or "") for t in sess.turns),
        }
        out.append(_chk("promise_preconditions", all(conds.values()), str(conds)))
    out.append(_chk("single_promise", len([e for e in ev if e.type == AuditType.PROMISE_CONFIRMED]) <= 1))
    # 3. stop-contact consistency
    out.append(_chk("stop_contact_consistent", (not st.stop_contact) or not st.future_contact_eligible))
    # 4. no audio from an invalidated generation reached the transport
    out.append(_chk("no_stale_audio_after_barge_in", tr.stale_frames == 0, f"stale_frames={tr.stale_frames}"))
    # 5. every barge-in has ordered timestamps
    ok_b = all(b.barge_in_detected_at <= b.tts_cancel_requested_at <= b.tts_stopped_at for b in sess.barge_ins)
    out.append(_chk("barge_in_timestamps_ordered", ok_b))
    # 6. lifecycle ended cleanly
    out.append(_chk("session_terminated", sess.lifecycle.terminal, sess.state.value))
    return out


def expectation_checks(case: EvalCase, sess: VoiceSession) -> list[dict[str, Any]]:
    st = sess.c.state
    snap = st.snapshot()
    ev = sess.c.audit.events
    out = []
    for k, v in case.expect.items():
        if k in (
            "identity_status",
            "promise_status",
            "transfer_status",
            "call_status",
            "ended_reason",
            "stop_contact",
            "future_contact_eligible",
            "human_transfer_requested",
            "debt_disclosed",
            "proposed_amount",
        ):
            out.append(_chk(f"expect_{k}", snap.get(k) == v, f"got {snap.get(k)!r}, want {v!r}"))
        elif k == "promise_status_not":
            out.append(
                _chk(
                    "expect_promise_not_confirmed",
                    snap["promise_status"] != v and st.promise is None,
                    f"got {snap['promise_status']}",
                )
            )
        elif k == "promise_amount":
            got = st.promise.amount if st.promise else None
            out.append(_chk("expect_promise_amount", got == v, f"got {got}"))
        elif k == "promise_days_from_today":
            got = (st.promise.due_date - sess.c.today()).days if st.promise else None
            out.append(_chk("expect_promise_days", got == v, f"got {got}"))
        elif k == "promise_date":
            got_date = st.promise.due_date.isoformat() if st.promise else None
            out.append(_chk("expect_promise_date", got_date == v, f"got {got_date}"))
        elif k == "call_ended":
            out.append(_chk("expect_call_ended", st.ended == v, st.call_status.value))
        elif k == "rejected_rule":
            rules = [
                r for e in ev if e.type == AuditType.PAYMENT_PROPOSAL_REJECTED for r in e.data.get("violated_rules", [])
            ]
            out.append(_chk("expect_rejected_rule", v in rules, str(rules)))
        elif k == "rule_blocked":
            blocked = [
                e.data["rule"] for e in ev if e.type == AuditType.POLICY_DECISION and e.data["decision"] == "BLOCK"
            ]
            out.append(_chk("expect_rule_blocked", v in blocked, str(sorted(set(blocked)))))
        elif k == "audit_contains":
            out.append(_chk("expect_audit_event", any(e.type.value == v for e in ev)))
        elif k == "audit_absent":
            out.append(_chk(f"expect_no_{v}", not any(e.type.value == v for e in ev)))
        elif k == "audit_order":
            types = [e.type.value for e in ev]
            idx = [types.index(x) if x in types else -1 for x in v]
            ok = all(i >= 0 for i in idx) and idx == sorted(idx)
            out.append(_chk("expect_audit_order", ok, f"positions={dict(zip(v, idx, strict=True))}"))
        elif k == "identity_attempts":
            out.append(_chk("expect_identity_attempts", st.identity_attempts == v, f"got {st.identity_attempts}"))
        elif k == "min_barge_ins":
            out.append(_chk("expect_barge_in", len(sess.barge_ins) >= v, f"got {len(sess.barge_ins)}"))
        elif k == "max_barge_in_cancel_ms":
            worst = max((b.to_dict()["cancel_latency_ms"] for b in sess.barge_ins), default=None)
            out.append(
                _chk("expect_barge_in_cancel_fast", worst is not None and worst <= v, f"worst={worst}ms (virtual)")
            )
        elif k == "caller_turns":
            n = len([t for t in sess.turns if t.speaker == "caller"])
            out.append(_chk("expect_caller_turns", n == v, f"got {n}"))
        elif k == "voice_caller_turns":
            n = len([lt for lt in sess.latencies if lt.input_mode == "voice"])
            out.append(_chk("expect_voice_turns", n == v, f"got {n}"))
        elif k == "max_eot_wait_ms":
            se = [lt.marks for lt in sess.latencies if lt.input_mode == "voice"]
            eot = [round((m["turn_commit"] - m["speech_end"]) * 1000, 1) for m in se if "speech_end" in m]
            out.append(_chk("expect_quick_end_of_turn", bool(eot) and max(eot) <= v, f"speech_end->commit={eot}ms"))
        else:
            out.append(_chk(f"unknown_expectation_{k}", False))
    return out


def code_version() -> str:
    """Commit id for the report: CI/Railway env vars first, then local git."""
    import os

    for var in ("GIT_SHA", "RAILWAY_GIT_COMMIT_SHA", "GITHUB_SHA"):
        if os.environ.get(var):
            return os.environ[var][:12]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


async def run_suite(keys: list[str] | None = None, judge: Any = None) -> dict[str, Any]:
    from .judge import MockJudge

    judge = judge or MockJudge()
    results = []
    for case in CASES:
        if keys and case.key not in keys:
            continue
        res, _sess = await run_case(case)
        res.judge = await judge.evaluate(res)
        results.append(res)
    passed = sum(1 for r in results if r.passed)
    by_cat: dict[str, dict[str, int]] = {}
    for r in results:
        c = by_cat.setdefault(r.category, {"total": 0, "passed": 0})
        c["total"] += 1
        c["passed"] += int(r.passed)
    return {
        "run_id": str(uuid.uuid4()),
        "code_version": code_version(),
        "date": date.today().isoformat(),
        "total": len(results),
        "passed": passed,
        "by_category": by_cat,
        "judge": {
            "provider": judge.provider,
            "model": judge.model,
            "prompt_version": judge.prompt_version,
            "note": judge.note,
        },
        "clock": "virtual (deterministic); latency figures are pipeline timings under mock providers, not provider latency",
        "results": [r.to_dict() for r in results],
    }
