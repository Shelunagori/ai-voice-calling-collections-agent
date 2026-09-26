"""Energy-based voice activity detection with an adaptive noise floor.

This is deliberately separate from end-of-turn detection: VAD answers "is there
voice energy right now?"; `turn_detection` answers "has the caller finished their
turn?". Time is audio time (derived from sample counts) so behaviour is identical
in tests and production, independent of network jitter.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from .audio import rms_dbfs


class VADEventType(StrEnum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


@dataclass(frozen=True)
class VADEvent:
    type: VADEventType
    audio_time: float  # seconds of audio since stream start at which the event applies


@dataclass
class VADConfig:
    frame_ms: int = 20
    start_ms: int = 100  # voiced time required to declare speech start (rejects clicks)
    hang_ms: int = 240  # unvoiced time required to declare speech end
    abs_threshold_db: float = -45.0
    snr_db: float = 12.0  # required margin above the adaptive noise floor
    floor_window_ms: int = 1000  # noise floor = minimum frame level over this window
    floor_ceiling_db: float = -35.0  # the floor never rises above this (long speech)
    calibration_ms: int = 200  # no speech is declared during the first frames


class EnergyVAD:
    def __init__(self, sample_rate: int, config: VADConfig | None = None) -> None:
        self.sr = sample_rate
        self.cfg = config or VADConfig()
        self.noise_floor = -60.0
        self.speaking = False
        self.audio_time = 0.0
        self._voiced_run = 0.0
        self._unvoiced_run = 0.0
        self._run_start = 0.0
        self.last_voiced_time = 0.0
        self.speech_started_at = 0.0
        self.last_level_db = -120.0
        self._levels: deque[float] = deque(maxlen=max(1, self.cfg.floor_window_ms // self.cfg.frame_ms))

    def is_voiced(self, db: float) -> bool:
        return db > max(self.cfg.abs_threshold_db, self.noise_floor + self.cfg.snr_db)

    def feed(self, frame: bytes) -> list[VADEvent]:
        """Feed one frame (any length) and return zero or more events."""
        dur = len(frame) / 2 / self.sr
        db = rms_dbfs(frame)
        self.last_level_db = db
        t0 = self.audio_time
        self.audio_time += dur
        events: list[VADEvent] = []
        # Minimum-statistics noise floor: stationary background noise sets the floor,
        # speech (which has troughs between syllables) does not.
        self._levels.append(db)
        self.noise_floor = min(max(min(self._levels), -90.0), self.cfg.floor_ceiling_db)
        calibrating = self.audio_time * 1000 <= self.cfg.calibration_ms
        voiced = self.is_voiced(db) and not calibrating
        if voiced:
            if self._voiced_run == 0:
                self._run_start = t0
            self._voiced_run += dur
            self._unvoiced_run = 0.0
            self.last_voiced_time = self.audio_time
            if not self.speaking and self._voiced_run * 1000 >= self.cfg.start_ms:
                self.speaking = True
                self.speech_started_at = self._run_start
                events.append(VADEvent(VADEventType.SPEECH_START, self._run_start))
        else:
            self._unvoiced_run += dur
            if not self.speaking:
                self._voiced_run = 0.0
            if self.speaking and self._unvoiced_run * 1000 >= self.cfg.hang_ms:
                self.speaking = False
                self._voiced_run = 0.0
                events.append(VADEvent(VADEventType.SPEECH_END, self.last_voiced_time))
        return events

    @property
    def current_speech_ms(self) -> float:
        """Voiced duration of the current segment (excludes the trailing hang time)."""
        return (self.last_voiced_time - self.speech_started_at) * 1000 if self.speaking else 0.0

    @property
    def silence_ms(self) -> float:
        return 0.0 if self.speaking else (self.audio_time - self.last_voiced_time) * 1000
