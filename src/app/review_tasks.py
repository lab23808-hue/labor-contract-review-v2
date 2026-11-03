import math
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from app.review import run_full_review
from domain.cleaning import clean_text
from domain.extractor import extract_fields
from domain.ports import DocumentParser

MAX_WORKERS = 16
DEFAULT_WORKERS = 8
DEFAULT_MAX_INFLIGHT = 8
DEFAULT_RPS = 8.0
DEFAULT_QUEUE_LIMIT = 50
DEFAULT_RATE_LIMIT = 5
DEFAULT_RATE_WINDOW = 60.0
DEFAULT_MAX_FILE_SIZE = 2 * 1024 * 1024


@dataclass
class TaskRecord:
    task_id: str
    filename: str
    status: str = "queued"
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


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


class ReviewTaskManager:
    def __init__(
        self,
        client: Any,
        parsers: Mapping[str, DocumentParser],
        workers: int = DEFAULT_WORKERS,
        max_inflight: int = DEFAULT_MAX_INFLIGHT,
        queue_limit: int = DEFAULT_QUEUE_LIMIT,
        rate_limit: int = DEFAULT_RATE_LIMIT,
        rate_window: float = DEFAULT_RATE_WINDOW,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    ) -> None:
        if queue_limit <= 0:
            raise ValueError("queue_limit must be positive")
        self.client = client
        self.parsers = dict(parsers)
        self.workers = min(max(1, workers), MAX_WORKERS)
        self.max_inflight = min(max(1, max_inflight), MAX_WORKERS)
        self.queue_limit = queue_limit
        self.max_file_size = max_file_size
        self.allowed_extensions = {
            f".{name.lower()}" for name in self.parsers
        }
        self.rate_limiter = SlidingWindowRateLimiter(
            rate_limit, rate_window
        )
        self._executor = ThreadPoolExecutor(
            max_workers=self.workers,
            thread_name_prefix="review-task",
        )
        self._admission = threading.BoundedSemaphore(queue_limit)
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()

    def submit(self, filename: str, content: bytes) -> Optional[str]:
        if not self._admission.acquire(blocking=False):
            return None

        task_id = uuid.uuid4().hex
        record = TaskRecord(task_id=task_id, filename=filename)
        with self._lock:
            self._tasks[task_id] = record
        try:
            self._executor.submit(
                self._run, task_id, filename, content
            )
        except Exception as exc:
            with self._lock:
                record.status = "failed"
                record.error = f"{type(exc).__name__}: {exc}"
                record.finished_at = time.time()
            self._admission.release()
            return None
        return task_id

    def get(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            return self._tasks.get(task_id)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            tasks = list(self._tasks.values())
        return {
            "queue_depth": sum(
                1 for task in tasks if task.status == "queued"
            ),
            "in_flight": sum(
                1 for task in tasks if task.status == "processing"
            ),
            "processed": sum(
                1 for task in tasks if task.status == "done"
            ),
            "failed": sum(
                1 for task in tasks if task.status == "failed"
            ),
            "total": len(tasks),
            "queue_limit": self.queue_limit,
            "workers": self.workers,
            "max_inflight": self.max_inflight,
        }

    def close(self) -> None:
        self._executor.shutdown(wait=True)

    def _run(
        self, task_id: str, filename: str, content: bytes
    ) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.status = "processing"
            record.started_at = time.time()
        try:
            suffix = filename.rsplit(".", 1)[-1].lower()
            parser = self.parsers.get(suffix)
            if parser is None:
                raise ValueError(f"不支持的解析格式：{suffix}")
            parsed = parser.parse(filename, content)
            cleaned = clean_text(parsed)
            report = run_full_review(
                cleaned,
                extract_fields(cleaned),
                llm_client=self.client,
                run_id=task_id,
            )
            with self._lock:
                record.result = report
                record.status = "done"
                record.finished_at = time.time()
        except Exception as exc:
            with self._lock:
                record.status = "failed"
                record.error = f"{type(exc).__name__}: {exc}"
                record.finished_at = time.time()
        finally:
            self._admission.release()
