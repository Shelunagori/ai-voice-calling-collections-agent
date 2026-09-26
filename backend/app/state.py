"""Process-wide application state (created in the FastAPI lifespan)."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import Settings
from .domain.clock import Clock, SystemClock
from .persistence.repository import Repository
from .runtime import Providers

if TYPE_CHECKING:
    from .voice.session import VoiceSession


class RateLimiter:
    """Sliding-window limiter keyed by (bucket, client). In-process: fine for a single
    replica demo; a multi-replica deployment would move this to Redis."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, bucket: str, key: str, limit: int, window_s: float, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self._hits[(bucket, key)]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


@dataclass
class AppState:
    settings: Settings
    repo: Repository
    providers: Providers
    db_mode: str = "unknown"
    db_revision: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    sessions: dict[uuid.UUID, VoiceSession] = field(default_factory=dict)
    pending_calls: dict[str, dict[str, Any]] = field(default_factory=dict)  # session_id -> outbound call context
    limiter: RateLimiter = field(default_factory=RateLimiter)
    draining: bool = False
    policy_clock: Clock = field(default_factory=SystemClock)
    closing: set[Any] = field(default_factory=set)  # in-flight close_session tasks
    opening: int = 0  # sessions being set up (reserved capacity)
