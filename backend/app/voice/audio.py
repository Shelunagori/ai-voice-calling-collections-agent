"""Small, dependency-free audio helpers (PCM16 mono).

`audioop` is deprecated/removed in modern Python, so G.711 mu-law and the simple
8k<->16k resampling needed for Twilio Media Streams are implemented here.
"""

from __future__ import annotations

import math
from array import array

_BIAS = 0x84
_CLIP = 32635


def _linear_to_ulaw(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample
    sample = min(sample, _CLIP) + _BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _ulaw_to_linear(u: int) -> int:
    u = ~u & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = ((mantissa << 3) + _BIAS) << exponent
    sample -= _BIAS
    return -sample if sign else sample


_ULAW_DECODE = [_ulaw_to_linear(i) for i in range(256)]
_ULAW_ENCODE = bytes(_linear_to_ulaw(i - 32768) for i in range(65536))


def pcm16_to_ulaw(pcm: bytes) -> bytes:
    a = array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    return bytes(_ULAW_ENCODE[s + 32768] for s in a)


def ulaw_to_pcm16(data: bytes) -> bytes:
    return array("h", (_ULAW_DECODE[b] for b in data)).tobytes()


def upsample_8k_to_16k(pcm: bytes) -> bytes:
    a = array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    out = array("h")
    for i, s in enumerate(a):
        nxt = a[i + 1] if i + 1 < len(a) else s
        out.append(s)
        out.append((s + nxt) // 2)
    return out.tobytes()


def downsample_16k_to_8k(pcm: bytes) -> bytes:
    a = array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    return array("h", ((a[i] + a[i + 1]) // 2 for i in range(0, len(a) - 1, 2))).tobytes()


def rms_dbfs(pcm: bytes) -> float:
    a = array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    if not a:
        return -120.0
    ms = sum(s * s for s in a) / len(a)
    if ms <= 0:
        return -120.0
    return 10 * math.log10(ms / (32768.0**2))


def duration_s(pcm: bytes, sample_rate: int) -> float:
    return len(pcm) / 2 / sample_rate


# ---------------------------------------------------------------- synthetic signals
def synth_tone(seconds: float, sample_rate: int, freq: float = 220.0, amp: float = 0.25) -> bytes:
    n = int(seconds * sample_rate)
    return array("h", (int(amp * 32767 * math.sin(2 * math.pi * freq * i / sample_rate)) for i in range(n))).tobytes()


def synth_speechlike(seconds: float, sample_rate: int, seed: int = 1, amp: float = 0.3) -> bytes:
    """Amplitude-modulated harmonic signal with syllable-rate envelope (~4 Hz).
    Deterministic; used to exercise VAD/turn detection without recorded speech."""
    n = int(seconds * sample_rate)
    f0 = 140 + (seed % 5) * 20
    out = array("h")
    for i in range(n):
        t = i / sample_rate
        env = 0.55 + 0.45 * math.sin(2 * math.pi * 4.0 * t + seed)
        v = sum(math.sin(2 * math.pi * f0 * k * t) / k for k in (1, 2, 3))
        out.append(int(max(-1.0, min(1.0, amp * env * v / 1.8)) * 32767))
    return out.tobytes()


def synth_noise(seconds: float, sample_rate: int, amp: float = 0.02, seed: int = 7) -> bytes:
    """Deterministic pseudo-random background noise (LCG)."""
    n = int(seconds * sample_rate)
    x = seed
    out = array("h")
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        out.append(int(((x / 0x7FFFFFFF) * 2 - 1) * amp * 32767))
    return out.tobytes()


def silence(seconds: float, sample_rate: int) -> bytes:
    return bytes(int(seconds * sample_rate) * 2)


def mix(a: bytes, b: bytes) -> bytes:
    x = array("h")
    x.frombytes(a)
    y = array("h")
    y.frombytes(b)
    n = max(len(x), len(y))
    return array(
        "h",
        (max(-32768, min(32767, (x[i] if i < len(x) else 0) + (y[i] if i < len(y) else 0))) for i in range(n)),
    ).tobytes()


def frames(pcm: bytes, sample_rate: int, frame_ms: int = 20) -> list[bytes]:
    size = int(sample_rate * frame_ms / 1000) * 2
    return [pcm[i : i + size] for i in range(0, len(pcm) - size + 1, size)]
