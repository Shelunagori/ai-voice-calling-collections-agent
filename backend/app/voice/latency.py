"""Per-turn latency instrumentation and percentile aggregation.

All marks are monotonic timestamps captured at the moment the event is observed in
this process. Nothing here is estimated or hard-coded: if a mark was not observed
the stage is simply absent. Aggregates always carry the provider mode so mock
pipeline timings are never presented as real provider latency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# (stage name, start mark, end mark)
STAGES: list[tuple[str, str, str]] = [
    ("speech_end_to_final_transcript", "speech_end", "final_transcript"),
    ("final_transcript_to_turn_commit", "final_transcript", "turn_commit"),
    ("nlu", "nlu_start", "nlu_done"),
    ("policy_apply", "nlu_done", "policy_done"),
    ("response_first_token", "policy_done", "response_first_token"),
    ("response_ready", "policy_done", "response_ready"),
    ("tts_request_to_first_audio", "tts_request", "tts_first_audio"),
    ("turn_commit_to_first_audio", "turn_commit", "first_audio_sent"),
    ("speech_end_to_first_audio", "speech_end", "first_audio_sent"),
]
BARGE_IN_STAGES: list[tuple[str, str, str]] = [
    ("barge_in_detect_to_cancel", "barge_in_detected_at", "tts_cancel_requested_at"),
    ("barge_in_cancel_to_stopped", "tts_cancel_requested_at", "tts_stopped_at"),
    ("barge_in_total", "barge_in_detected_at", "tts_stopped_at"),
]
BUDGET_MS = 1500.0


@dataclass
class TurnLatency:
    turn_index: int
    input_mode: str  # "voice" | "text"
    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str, at: float) -> None:
        self.marks.setdefault(name, at)

    def stages_ms(self) -> dict[str, float]:
        out = {}
        for name, a, b in STAGES + BARGE_IN_STAGES:
            if a in self.marks and b in self.marks and self.marks[b] >= self.marks[a]:
                out[name] = round((self.marks[b] - self.marks[a]) * 1000, 1)
        # For typed turns the "speech end" equivalent is the moment text arrived.
        if "speech_end_to_first_audio" not in out and "turn_commit_to_first_audio" in out and self.input_mode == "text":
            out["input_to_first_audio"] = out["turn_commit_to_first_audio"]
        elif "speech_end_to_first_audio" in out:
            out["input_to_first_audio"] = out["speech_end_to_first_audio"]
        return out


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; None for empty input."""
    if not values:
        return None
    s = sorted(values)
    k = max(0, math.ceil(p / 100 * len(s)) - 1)
    return s[k]


def summarize(samples: dict[str, list[float]]) -> dict[str, dict[str, float | int | None]]:
    return {
        stage: {
            "count": len(v),
            "p50_ms": percentile(v, 50),
            "p95_ms": percentile(v, 95),
            "max_ms": max(v) if v else None,
        }
        for stage, v in sorted(samples.items())
    }
