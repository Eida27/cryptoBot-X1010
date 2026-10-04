import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    def now_ms(self) -> int: ...
    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FakeClock:
    def __init__(self, at_ms: int = 0) -> None:
        self.at_ms = at_ms

    def now_ms(self) -> int:
        return self.at_ms

    def advance(self, milliseconds: int) -> None:
        self.at_ms += milliseconds

    async def sleep(self, seconds: float) -> None:
        self.advance(int(seconds * 1000))
        await asyncio.sleep(0)
