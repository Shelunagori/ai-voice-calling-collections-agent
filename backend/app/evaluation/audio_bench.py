"""Audio regression harness.

  python -m app.evaluation.audio_bench signals            # deterministic VAD fixtures (always runnable)
  python -m app.evaluation.audio_bench generate           # TTS-generate speech WAVs (needs CARTESIA_API_KEY)
  python -m app.evaluation.audio_bench stt [--out FILE]   # run STT over speech WAVs (needs CARTESIA_API_KEY)

The STT benchmark reports character error rate and entity accuracy (amounts, dates,
intents) through the same deterministic parser the agent uses. When credentials or
WAV files are missing it reports `not_executed` instead of inventing numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import unicodedata
import wave
from datetime import date
from pathlib import Path
from typing import Any

from ..domain import nlu_rules
from ..domain.commands import Action
from ..domain.models import Language
from ..voice import audio
from ..voice.vad import EnergyVAD, VADEventType

ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "audio"
SR = 16000


def load_manifest() -> dict[str, Any]:
    return json.loads((ROOT / "manifest.json").read_text())


def render_signal(recipe: list[list[Any]]) -> bytes:
    pcm = b""
    bed: bytes | None = None
    for kind, secs in recipe:
        if kind == "silence":
            pcm += audio.silence(secs, SR)
        elif kind == "speech":
            pcm += audio.synth_speechlike(secs, SR)
        elif kind == "noise":
            pcm += audio.synth_noise(secs, SR, amp=0.01)
        elif kind == "noise_bed":
            bed = audio.synth_noise(secs + 10, SR, amp=0.01)
    if bed is not None:
        pcm = audio.mix(bed[: len(pcm)], pcm)
    return pcm


def run_signals() -> list[dict[str, Any]]:
    out = []
    for fx in load_manifest()["fixtures"]:
        if fx["kind"] != "synthetic_signal":
            continue
        v = EnergyVAD(SR)
        segs = 0
        for fr in audio.frames(render_signal(fx["recipe"]), SR):
            segs += sum(1 for e in v.feed(fr) if e.type == VADEventType.SPEECH_START)
        want = fx["expect"]["segments"]
        out.append(
            {"id": fx["id"], "category": fx["category"], "segments": segs, "expected": want, "passed": segs == want}
        )
    return out


def cer(ref: str, hyp: str) -> float:
    r = [c for c in unicodedata.normalize("NFKC", ref.lower()) if c.isalnum()]
    h = [c for c in unicodedata.normalize("NFKC", hyp.lower()) if c.isalnum()]
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hc in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc))
        prev = cur
    return prev[-1] / len(r)


def entity_check(fx: dict[str, Any], transcript: str) -> dict[str, bool]:
    lang = Language(fx["language"])
    interp = nlu_rules.interpret(transcript, lang, date(2026, 10, 1), expecting_dob="dob" in fx["entities"])
    res: dict[str, bool] = {}
    ent = fx["entities"]
    pay = interp.first(Action.PROPOSE_PAYMENT)
    if "amount" in ent:
        res["amount"] = bool(pay and pay.amount == ent["amount"])
    if "date_md" in ent:
        res["date"] = bool(pay and pay.date and [pay.date.month, pay.date.day] == ent["date_md"])
    if "dob" in ent:
        d = interp.first(Action.PROVIDE_DOB)
        res["dob"] = bool(d and d.dob and d.dob.isoformat() == ent["dob"])
    if "intent" in ent:
        res["intent"] = interp.has(Action(ent["intent"]))
    if "name" in ent:
        res["name"] = ent["name"] in transcript
    return res


def _read_wav(p: Path) -> bytes:
    with wave.open(str(p), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != SR:
            raise ValueError(f"{p.name}: expected 16 kHz mono PCM16")
        return w.readframes(w.getnframes())


def _write_wav(p: Path, pcm: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)


async def generate() -> dict[str, Any]:
    from ..config import get_settings
    from ..providers.cartesia import CartesiaTTS

    s = get_settings()
    if not s.cartesia_api_key or not s.cartesia_voice_id:
        return {"status": "not_executed", "reason": "CARTESIA_API_KEY and CARTESIA_VOICE_ID required"}
    tts = CartesiaTTS(
        s.cartesia_api_key, s.cartesia_tts_model, s.cartesia_version, s.cartesia_voice_id, s.cartesia_voice_id_ja, SR
    )
    made = []
    for fx in load_manifest()["fixtures"]:
        if fx["kind"] != "speech":
            continue
        pcm = b"".join([c async for c in tts.synthesize(fx["text"], fx["language"], context_id=f"fx-{fx['id']}")])
        pcm = audio.silence(0.3, SR) + pcm + audio.silence(0.8, SR)
        if fx.get("noise_amp"):
            pcm = audio.mix(audio.synth_noise(len(pcm) / 2 / SR, SR, amp=fx["noise_amp"]), pcm)
        _write_wav(ROOT / "generated" / f"{fx['id']}.wav", pcm)
        made.append(fx["id"])
    await tts.aclose()
    return {"status": "generated", "files": made, "note": "TTS-generated synthetic speech, not real caller audio"}


async def run_stt() -> dict[str, Any]:
    from ..config import get_settings
    from ..providers.cartesia import CartesiaSTT

    s = get_settings()
    if not s.cartesia_api_key:
        return {"status": "not_executed", "reason": "CARTESIA_API_KEY not set"}
    stt = CartesiaSTT(s.cartesia_api_key, s.cartesia_stt_model, s.cartesia_version)
    rows = []
    for fx in load_manifest()["fixtures"]:
        if fx["kind"] != "speech":
            continue
        wav = next(
            (p for p in (ROOT / "wav" / f"{fx['id']}.wav", ROOT / "generated" / f"{fx['id']}.wav") if p.exists()), None
        )
        if wav is None:
            rows.append({"id": fx["id"], "status": "missing_audio"})
            continue
        pcm = _read_wav(wav)
        stream = await stt.open_stream(fx["language"], SR)
        t0 = time.monotonic()
        for fr in audio.frames(pcm, SR):
            await stream.send_audio(fr)
        sent_at = time.monotonic()
        await stream.finalize()
        text, final_at = await _collect(stream)
        await stream.close()
        hyp = " ".join(text)
        rows.append(
            {
                "id": fx["id"],
                "category": fx["category"],
                "language": fx["language"],
                "source": wav.parent.name,
                "reference": fx["text"],
                "hypothesis": hyp,
                "cer": round(cer(fx["text"], hyp), 3),
                "entities": entity_check(fx, hyp),
                "upload_s": round(sent_at - t0, 3),
                "finalize_to_final_ms": round((final_at - sent_at) * 1000, 1) if final_at else None,
            }
        )
    return {"status": "executed", "provider": f"cartesia:{s.cartesia_stt_model}", "results": rows}


async def _collect(stream: Any) -> tuple[list[str], float | None]:
    from ..providers.base import STTEventType

    text: list[str] = []
    final_at: float | None = None

    async def run() -> None:
        nonlocal final_at
        async for ev in stream.events():
            if ev.type == STTEventType.FINAL and ev.text:
                text.append(ev.text)
                final_at = time.monotonic()
            if ev.type == STTEventType.CLOSED:
                return

    try:
        await asyncio.wait_for(run(), timeout=4)
    except TimeoutError:
        pass
    return text, final_at


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m app.evaluation.audio_bench")
    ap.add_argument("command", choices=["signals", "generate", "stt"])
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.command == "signals":
        res: Any = {"status": "executed", "results": run_signals()}
    elif a.command == "generate":
        res = asyncio.run(generate())
    else:
        res = asyncio.run(run_stt())
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    print(txt)
    if a.out:
        Path(a.out).write_text(txt)
    if res.get("status") == "executed" and a.command == "signals" and not all(r["passed"] for r in res["results"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
