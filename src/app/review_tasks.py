import json
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.review import run_full_review
from config import settings
from domain.extractor import extract_fields

logger = logging.getLogger(__name__)

MAX_WORKERS = 16
DEFAULT_WORKERS = settings.REVIEW_WORKERS
DEFAULT_MAX_INFLIGHT = settings.REVIEW_MAX_INFLIGHT
DEFAULT_RPS = settings.REVIEW_RPS
DEFAULT_QUEUE_LIMIT = settings.REVIEW_QUEUE_LIMIT
DEFAULT_TTL_SECONDS = settings.REVIEW_TTL_SECONDS
DEFAULT_CLEANUP_INTERVAL = settings.REVIEW_CLEANUP_INTERVAL
DEFAULT_STORE_PATH = Path(settings.REVIEW_STORE_PATH)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_tasks (
    task_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    cleaned_text TEXT,
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
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        # 旧库用 content BLOB 存原始字节，新库改存清洗文本；
        # CREATE TABLE IF NOT EXISTS 不会改已有表，需要显式补列。
        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(review_tasks)"
            )
        }
        if "cleaned_text" not in columns:
            self._conn.execute(
                "ALTER TABLE review_tasks ADD COLUMN cleaned_text TEXT"
            )
        if "content" in columns:
            try:
                self._conn.execute(
                    "ALTER TABLE review_tasks DROP COLUMN content"
                )
            except sqlite3.OperationalError:
                logger.warning(
                    "", extra={"event": "task.migration_keep_content"}
                )

    def create_task(
        self,
        task_id: str,
        filename: str,
        cleaned_text: Optional[str],
        now: float,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO review_tasks "
                "(task_id, filename, status, result_json, error, "
                "cleaned_text, created_at, updated_at) "
                "VALUES (?, ?, 'queued', NULL, NULL, ?, ?, ?)",
                (task_id, filename, cleaned_text, now, now),
            )
            self._conn.commit()

    def set_cleaned_text(
        self, task_id: str, cleaned_text: str, now: float
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE review_tasks SET status='queued', "
                "cleaned_text=?, updated_at=? WHERE task_id=?",
                (cleaned_text, now, task_id),
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
                "result_json=?, error=NULL, cleaned_text=NULL, "
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
                "result_json=NULL, error=?, cleaned_text=NULL, "
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
        workers: int = DEFAULT_WORKERS,
        max_inflight: int = DEFAULT_MAX_INFLIGHT,
        queue_limit: int = DEFAULT_QUEUE_LIMIT,
        store_path: Path | str = DEFAULT_STORE_PATH,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        cleanup_interval: float = DEFAULT_CLEANUP_INTERVAL,
    ) -> None:
        if queue_limit <= 0:
            raise ValueError("queue_limit must be positive")
        self.client = client
        self.workers = min(max(1, workers), MAX_WORKERS)
        self.max_inflight = min(max(1, max_inflight), MAX_WORKERS)
        self.queue_limit = queue_limit
        self.ttl_seconds = ttl_seconds
        self.cleanup_interval = cleanup_interval
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

    def reserve(self, task_id: str, filename: str = "") -> bool:
        if not self._admission.acquire(blocking=False):
            return False

        now = time.time()
        record = TaskRecord(
            task_id=task_id, filename=filename, created_at=now
        )
        with self._lock:
            self._tasks[task_id] = record
        try:
            self.store.create_task(task_id, filename, None, now)
        except Exception:
            self._admission.release()
            with self._lock:
                self._tasks.pop(task_id, None)
            try:
                self.store.delete_task(task_id)
            except Exception:
                logger.exception("task.cleanup_failed")
            return False
        return True

    def submit_reserved(self, task_id: str, cleaned_text: str) -> bool:
        with self._lock:
            record = self._tasks.get(task_id)
        if record is None:
            return False

        try:
            self.store.set_cleaned_text(task_id, cleaned_text, time.time())
        except Exception:
            logger.exception("task.persist_failed")
            self.fail_reserved(task_id, "清洗文本持久化失败")
            return False
        try:
            self._executor.submit(self._run, task_id, cleaned_text)
        except Exception:
            self.release_reservation(task_id)
            return False
        return True

    def submit(
        self,
        task_id: str,
        cleaned_text: str,
        filename: str = "",
    ) -> Optional[str]:
        if not self.reserve(task_id, filename):
            return None
        if not self.submit_reserved(task_id, cleaned_text):
            return None
        return task_id

    def release_reservation(self, task_id: str) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)
        try:
            self.store.delete_task(task_id)
        except Exception:
            logger.exception("task.cleanup_failed")
        self._admission.release()

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            record = self._tasks.pop(task_id, None)
        if record is None:
            return False
        try:
            self.store.delete_task(task_id)
        except Exception:
            logger.exception("task.cleanup_failed")
        if record.status == "queued":
            # processing 由 worker 的 finally 释放；done/failed 早已释放
            self._admission.release()
        return True

    def fail_reserved(self, task_id: str, error: str) -> None:
        now = time.time()
        with self._lock:
            record = self._tasks.get(task_id)
            if record is not None:
                record.status = "failed"
                record.error = error
                record.finished_at = now
        try:
            self.store.mark_failed(task_id, error, now)
        except Exception:
            logger.exception("task.persist_failed")
        self._admission.release()

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
                for task_id in expired:
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
            if row["cleaned_text"] is None:
                self._mark_failed(
                    row["task_id"], "持久化记录缺少清洗文本"
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
                row["cleaned_text"],
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

    def _run(self, task_id: str, cleaned_text: str) -> None:
        now = time.time()
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                # 排队期间被 cancel，名额已在 cancel 中释放
                return
            record.status = "processing"
            record.started_at = now
        try:
            self.store.mark_processing(task_id, now)
            report = run_full_review(
                cleaned_text,
                extract_fields(cleaned_text),
                llm_client=self.client,
                run_id=task_id,
            )
            now = time.time()
            with self._lock:
                if self._tasks.get(task_id) is not record:
                    return
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
                if self._tasks.get(task_id) is not record:
                    return
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
