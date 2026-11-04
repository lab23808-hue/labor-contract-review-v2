import json
import logging
import math
import sqlite3
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from app.review import run_full_review
from domain.cleaning import clean_text
from domain.extractor import extract_fields
from domain.ports import DocumentParser

logger = logging.getLogger(__name__)

MAX_WORKERS = 16
DEFAULT_WORKERS = 8
DEFAULT_MAX_INFLIGHT = 8
DEFAULT_RPS = 8.0
DEFAULT_QUEUE_LIMIT = 50
DEFAULT_RATE_LIMIT = 5
DEFAULT_RATE_WINDOW = 60.0
DEFAULT_MAX_FILE_SIZE = 2 * 1024 * 1024
DEFAULT_TTL_SECONDS = 24 * 3600
DEFAULT_CLEANUP_INTERVAL = 600.0
DEFAULT_STORE_PATH = Path("data") / "review_tasks.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_tasks (
    task_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    content BLOB,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS expired_task_ids (
    task_id TEXT PRIMARY KEY,
    expired_at REAL NOT NULL
);
"""


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


class ReviewTaskStore:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def create_task(
        self,
        task_id: str,
        filename: str,
        content: bytes,
        now: float,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO review_tasks "
                "(task_id, filename, status, result_json, error, "
                "content, created_at, updated_at) "
                "VALUES (?, ?, 'queued', NULL, NULL, ?, ?, ?)",
                (task_id, filename, content, now, now),
            )
            self._conn.commit()

    def mark_processing(self, task_id: str, now: float) -> None:
        self._update_status(task_id, "processing", now)

    def mark_queued(self, task_id: str, now: float) -> None:
        self._update_status(task_id, "queued", now)

    def mark_done(
        self, task_id: str, result_json: str, now: float
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE review_tasks SET status='done', "
                "result_json=?, error=NULL, content=NULL, "
                "updated_at=? WHERE task_id=?",
                (result_json, now, task_id),
            )
            self._conn.commit()

    def mark_failed(
        self, task_id: str, error: str, now: float
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE review_tasks SET status='failed', "
                "result_json=NULL, error=?, content=NULL, "
                "updated_at=? WHERE task_id=?",
                (error, now, task_id),
            )
            self._conn.commit()

    def delete_task(self, task_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM review_tasks WHERE task_id=?",
                (task_id,),
            )
            self._conn.commit()

    def get(self, task_id: str):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM review_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()

    def list_all(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM review_tasks ORDER BY created_at"
            ).fetchall()

    def delete_expired(
        self, cutoff: float, now: float
    ) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT task_id FROM review_tasks "
                "WHERE status IN ('done', 'failed') AND updated_at < ?",
                (cutoff,),
            ).fetchall()
            task_ids = [row["task_id"] for row in rows]
            if not task_ids:
                return []
            placeholders = ",".join("?" for _ in task_ids)
            self._conn.executemany(
                "INSERT OR REPLACE INTO expired_task_ids "
                "(task_id, expired_at) VALUES (?, ?)",
                [(task_id, now) for task_id in task_ids],
            )
            self._conn.execute(
                f"DELETE FROM review_tasks WHERE task_id IN ({placeholders})",
                task_ids,
            )
            self._conn.commit()
            return task_ids

    def is_expired(self, task_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM expired_task_ids WHERE task_id=?",
                (task_id,),
            ).fetchone()
            return row is not None

    def expired_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM expired_task_ids"
            ).fetchone()
            return int(row["count"])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _update_status(
        self, task_id: str, status: str, now: float
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE review_tasks SET status=?, updated_at=? "
                "WHERE task_id=?",
                (status, now, task_id),
            )
            self._conn.commit()


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
        store_path: Path | str = DEFAULT_STORE_PATH,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        cleanup_interval: float = DEFAULT_CLEANUP_INTERVAL,
    ) -> None:
        if queue_limit <= 0:
            raise ValueError("queue_limit must be positive")
        self.client = client
        self.parsers = dict(parsers)
        self.workers = min(max(1, workers), MAX_WORKERS)
        self.max_inflight = min(max(1, max_inflight), MAX_WORKERS)
        self.queue_limit = queue_limit
        self.max_file_size = max_file_size
        self.ttl_seconds = ttl_seconds
        self.cleanup_interval = cleanup_interval
        self.allowed_extensions = {
            f".{name.lower()}" for name in self.parsers
        }
        self.rate_limiter = SlidingWindowRateLimiter(
            rate_limit, rate_window
        )
        self.store = ReviewTaskStore(store_path)
        self._executor = ThreadPoolExecutor(
            max_workers=self.workers,
            thread_name_prefix="review-task",
        )
        self._admission = threading.BoundedSemaphore(queue_limit)
        # 并发约定（多线程读写，勿引入锁，遵守以下三条即可）：
        #   - 读取一律走 .get()，禁止 `in` + `[]` 两步式访问
        #   - 遍历前必须 list() 快照
        #   - record 对象一经取出，读其字段恒安全（引用计数保证存活）；
        #     但若记录可能已被 TTL 清理，回写字段前需确认该场景可接受
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._recover()
        self._stop = threading.Event()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="review-task-ttl",
            daemon=True,
        )
        self._cleanup_thread.start()

    def submit(self, filename: str, content: bytes) -> Optional[str]:
        if not self._admission.acquire(blocking=False):
            return None

        task_id = uuid.uuid4().hex
        now = time.time()
        try:
            record = TaskRecord(
                task_id=task_id, filename=filename, created_at=now
            )
            self.store.create_task(task_id, filename, content, now)
            with self._lock:
                self._tasks[task_id] = record
            self._executor.submit(
                self._run, task_id, filename, content
            )
        except Exception:
            self._admission.release()
            with self._lock:
                self._tasks.pop(task_id, None)
            try:
                self.store.delete_task(task_id)
            except Exception:
                logger.exception("task.cleanup_failed")
            return None
        return task_id

    def get(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            return self._tasks.get(task_id)

    def is_expired(self, task_id: str) -> bool:
        return self.store.is_expired(task_id)

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
            "store": self.store.path,
            "ttl_seconds": self.ttl_seconds,
            "expired": self.store.expired_count(),
        }

    def cleanup_expired(self) -> int:
        now = time.time()
        cutoff = now - self.ttl_seconds
        expired = set(self.store.delete_expired(cutoff, now))
        if expired:
            with self._lock:
                for task_id, _ in list(self._tasks.items()):
                    if task_id in expired:
                        self._tasks.pop(task_id, None)
            logger.info(
                "",
                extra={
                    "event": "task.expired",
                    "count": len(expired),
                },
            )
        return len(expired)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5)
        self._executor.shutdown(wait=True)
        self.store.close()

    def _recover(self) -> None:
        rows = self.store.list_all()
        now = time.time()
        for row in rows:
            result = (
                json.loads(row["result_json"])
                if row["result_json"]
                else None
            )
            record = TaskRecord(
                task_id=row["task_id"],
                filename=row["filename"],
                status=row["status"],
                result=result,
                error=row["error"],
                created_at=row["created_at"],
                finished_at=(
                    row["updated_at"]
                    if row["status"] in ("done", "failed")
                    else None
                ),
            )
            with self._lock:
                self._tasks[record.task_id] = record

        for row in rows:
            if row["status"] not in ("queued", "processing"):
                continue
            if row["content"] is None:
                self._mark_failed(
                    row["task_id"], "持久化记录缺少原始文件内容"
                )
                continue
            if row["status"] == "processing":
                self.store.mark_queued(row["task_id"], now)
                with self._lock:
                    record = self._tasks.get(row["task_id"])
                    if record is not None:
                        record.status = "queued"
                logger.info(
                    "",
                    extra={
                        "event": "task.requeued",
                        "task_id": row["task_id"],
                        "previous_status": "processing",
                    },
                )
            if not self._admission.acquire(blocking=False):
                logger.warning(
                    "",
                    extra={
                        "event": "task.recovery_deferred",
                        "task_id": row["task_id"],
                    },
                )
                continue
            logger.info(
                "",
                extra={
                    "event": "task.recovered",
                    "task_id": row["task_id"],
                },
            )
            self._executor.submit(
                self._run,
                row["task_id"],
                row["filename"],
                row["content"],
            )

    def _mark_failed(self, task_id: str, error: str) -> None:
        now = time.time()
        with self._lock:
            record = self._tasks.get(task_id)
            if record is not None:
                record.status = "failed"
                record.error = error
                record.finished_at = now
        self.store.mark_failed(task_id, error, now)

    def _run(
        self, task_id: str, filename: str, content: bytes
    ) -> None:
        now = time.time()
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                self._admission.release()
                return
            record.status = "processing"
            record.started_at = now
        try:
            self.store.mark_processing(task_id, now)
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
            now = time.time()
            with self._lock:
                record.result = report
                record.status = "done"
                record.finished_at = now
            self.store.mark_done(
                task_id,
                json.dumps(report, ensure_ascii=False),
                now,
            )
        except Exception as exc:
            now = time.time()
            error = f"{type(exc).__name__}: {exc}"
            with self._lock:
                record.status = "failed"
                record.error = error
                record.finished_at = now
            self.store.mark_failed(task_id, error, now)
        finally:
            self._admission.release()

    def _cleanup_loop(self) -> None:
        while not self._stop.wait(self.cleanup_interval):
            try:
                self.cleanup_expired()
            except Exception:
                logger.exception("task.cleanup_failed")
