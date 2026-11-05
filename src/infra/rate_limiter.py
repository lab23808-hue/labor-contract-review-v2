import math
import threading
import time
from collections import deque
from typing import Callable


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int, int]:
        now = time.monotonic()
        with self._lock:
            events = self._events.setdefault(key, deque())
            while (
                events
                and events[0] <= now - self.window_seconds
            ):
                events.popleft()
            if len(events) >= self.limit:
                retry_after = max(
                    1,
                    math.ceil(
                        self.window_seconds - (now - events[0])
                    ),
                )
                return False, 0, retry_after
            events.append(now)
            return True, self.limit - len(events), 0


class TokenBucket:
    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = float(rate)
        self.capacity = float(
            capacity if capacity is not None else max(1.0, rate)
        )
        self._tokens = self.capacity
        self._updated = clock()
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(
                    self.capacity,
                    self._tokens + (now - self._updated) * self.rate,
                )
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) / self.rate
            self._sleep(wait)


class RequestThrottle:
    def __init__(self, rate: float, max_inflight: int) -> None:
        if max_inflight <= 0:
            raise ValueError("max_inflight must be positive")
        self.bucket = TokenBucket(rate)
        self._semaphore = threading.BoundedSemaphore(max_inflight)

    def acquire(self) -> None:
        self._semaphore.acquire()
        self.bucket.acquire()

    def release(self) -> None:
        self._semaphore.release()
