"""Semantic end-of-turn decision (separate from VAD).

Given how long the caller has been silent and what they have said so far, decide
whether the turn is complete. Heuristics:

* nothing transcribed yet  -> wait for the transcript, then treat as noise;
* filler-only / trailing conjunction ("and", "so", "えーと", "けど") -> the caller is
  thinking: wait longer;
* very short complete answers ("yes", "はい") -> respond quickly;
* otherwise a moderate default; a hard ceiling always ends the turn.

Thresholds are configuration, not magic: they are exposed in `TurnConfig` and
covered by tests. Production tuning would come from labelled call audio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from ..domain.nlu_rules import is_filler_only, normalise


class TurnDecision(StrEnum):
    COMPLETE = "COMPLETE"
    WAIT = "WAIT"
    NOISE = "NOISE"  # voice energy but no words: background speech, cough, line noise


@dataclass
class TurnConfig:
    quick_ms: int = 250
    default_ms: int = 600
    thinking_ms: int = 1500
    max_ms: int = 2500
    transcript_grace_ms: int = 1200  # how long to wait for STT after speech ends


_CONTINUATION_EN = re.compile(r"\b(and|but|so|because|or|um+|uh+|er+|like|then|if|i can|i could|maybe)[\s,.-]*$")
_CONTINUATION_JA = re.compile(r"(けど|けれど|けれども|ので|から|が|えーと|えっと|あの|あのー|それで|で|と|、)$")
_SHORT_EN = re.compile(r"^(yes|yeah|yep|no|nope|correct|right|okay|ok|sure|that's right|that's correct|speaking)[.!]?$")
_SHORT_JA = re.compile(r"^(はい|いいえ|ええ|そうです|違います|大丈夫です|お願いします)[。!！]?$")


class EndOfTurnDetector:
    def __init__(self, config: TurnConfig | None = None) -> None:
        self.cfg = config or TurnConfig()

    def required_silence_ms(self, text: str) -> int:
        t = normalise(text)
        if not t:
            return self.cfg.transcript_grace_ms
        if is_filler_only(t) or _CONTINUATION_EN.search(t) or _CONTINUATION_JA.search(t):
            return self.cfg.thinking_ms
        if _SHORT_EN.match(t) or _SHORT_JA.match(t):
            return self.cfg.quick_ms
        return self.cfg.default_ms

    def decide(self, text: str, silence_ms: float, transcript_final: bool) -> TurnDecision:
        t = normalise(text)
        if silence_ms >= self.cfg.max_ms:
            return TurnDecision.COMPLETE if t and not is_filler_only(t) else TurnDecision.NOISE
        if not t:
            return TurnDecision.NOISE if silence_ms >= self.cfg.transcript_grace_ms else TurnDecision.WAIT
        need = self.required_silence_ms(t)
        if not transcript_final:
            # Only a partial so far: give the provider time to deliver the final (it may
            # still grow, e.g. "no" -> "no, the 20th") before committing on the partial.
            need = max(need, self.cfg.transcript_grace_ms)
        if silence_ms >= need and not is_filler_only(t):
            return TurnDecision.COMPLETE
        # Filler-only ("um...") keeps waiting until the hard ceiling, then counts as noise.
        return TurnDecision.WAIT
