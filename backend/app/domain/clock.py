"""Clock abstraction so policy (calling hours, date windows) and latency are testable."""

from __future__ import annotations

import asyncio
import heapq
import time
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware UTC wall clock."""

    def monotonic(self) -> float:
        """Monotonic seconds, for latency measurement."""

    def today_in(self, tz: str) -> date: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def today_in(self, tz: str) -> date:
        return self.now().astimezone(ZoneInfo(tz)).date()


class FakeClock:
    """Deterministic clock for tests/evaluation. `advance()` moves both clocks."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 10, 1, 1, 0, tzinfo=UTC)  # 10:00 JST
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def today_in(self, tz: str) -> date:
        return self._now.astimezone(ZoneInfo(tz)).date()

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._mono += seconds

    def set(self, when: datetime) -> None:
        self._now = when


class VirtualClock(FakeClock):
    """FakeClock with a cooperative `sleep()` driven by `advance()`.

    Lets tests and the evaluation harness run the real-time runtime (pacing, silence
    timers, barge-in) deterministically and much faster than wall time.
    """

    def __init__(self, start: datetime | None = None) -> None:
        super().__init__(start)
        self._sleepers: list[tuple[float, int, asyncio.Future[None]]] = []
        self._seq = 0

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._seq += 1
        heapq.heappush(self._sleepers, (self._mono + seconds, self._seq, fut))
        await fut

    @staticmethod
    async def _flush(n: int = 8) -> None:
        for _ in range(n):
            await asyncio.sleep(0)

    async def advance_async(self, seconds: float) -> None:
        target = self._mono + seconds
        await self._flush()
        while self._sleepers and self._sleepers[0][0] <= target:
            deadline, _, fut = heapq.heappop(self._sleepers)
            if deadline > self._mono:
                self.advance(deadline - self._mono)
            if not fut.done():
                fut.set_result(None)
            await self._flush()
        if target > self._mono:
            self.advance(target - self._mono)
        await self._flush()

    @property
    def pending_sleepers(self) -> int:
        return sum(1 for *_, f in self._sleepers if not f.done())
