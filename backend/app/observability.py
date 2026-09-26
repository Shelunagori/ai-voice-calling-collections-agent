"""Structured JSON logging, correlation IDs and a tiny Prometheus-format metrics registry."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import threading
import time
from collections import defaultdict
from typing import Any

correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="-")

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization|x-api-key|api[_-]?key|token|auth_token|password)([\"'=:\s]+)([^\s\"',}]+)"),
    re.compile(r"sk_[A-Za-z0-9]{8,}"),
]
_STD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {"message", "asctime"}


def redact(text: str) -> str:
    for p in _SECRET_PATTERNS:
        text = p.sub(
            lambda m: (m.group(1) + m.group(2) + "[REDACTED]") if m.lastindex and m.lastindex >= 3 else "[REDACTED]",
            text,
        )
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "correlation_id": correlation_id.get(),
            "session_id": session_id_var.get(),
        }
        for k, v in record.__dict__.items():
            if k not in _STD and not k.startswith("_"):
                out[k] = redact(str(v)) if isinstance(v, str) else v
        if record.exc_info:
            out["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(out, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [h]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "websockets", "alembic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class Metrics:
    """Thread-safe counters/gauges/histograms rendered in Prometheus text format."""

    BUCKETS = (50, 100, 250, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self.gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self.hist: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)

    @staticmethod
    def _key(name: str, labels: dict[str, str] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
        return name, tuple(sorted((labels or {}).items()))

    def inc(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        with self._lock:
            self.counters[self._key(name, labels)] += value

    def set(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self.gauges[self._key(name, labels)] = value

    def observe(self, name: str, value_ms: float, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            h = self.hist[self._key(name, labels)]
            h.append(value_ms)
            if len(h) > 5000:
                del h[:1000]

    def render(self) -> str:
        lines: list[str] = []

        def lab(pairs: tuple[tuple[str, str], ...], extra: str = "") -> str:
            items = [f'{k}="{v}"' for k, v in pairs] + ([extra] if extra else [])
            return "{" + ",".join(items) + "}" if items else ""

        with self._lock:
            for (n, p), v in sorted(self.counters.items()):
                lines.append(f"{n}_total{lab(p)} {v}")
            for (n, p), v in sorted(self.gauges.items()):
                lines.append(f"{n}{lab(p)} {v}")
            for (n, p), vals in sorted(self.hist.items()):
                for b in self.BUCKETS:
                    lines.append(f"{n}_bucket{lab(p, f'le="{b}"')} {sum(1 for x in vals if x <= b)}")
                lines.append(f"{n}_bucket{lab(p, 'le="+Inf"')} {len(vals)}")
                lines.append(f"{n}_sum{lab(p)} {sum(vals)}")
                lines.append(f"{n}_count{lab(p)} {len(vals)}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
