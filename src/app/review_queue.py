import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional

from app.review_engine import run_full_review
from domain.extractor import extract_fields
from domain.ports import ReviewTaskRepository

logger = logging.getLogger(__name__)

MAX_WORKERS = 16


@dataclass
class TaskRecord:
    contract_id: str
    filename: str
    status: str = "queued"
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class ReviewQueue:
    def __init__(
        self,
        client: Any,
        store: ReviewTaskRepository,
        *,
        workers: int,
        max_inflight: int,
        queue_limit: int,
        ttl_seconds: float,
        cleanup_interval: float,
    ) -> None:
        if queue_limit <= 0:
            raise ValueError("queue_limit must be positive")
        self.client = client
        self.store = store
        self.workers = min(max(1, workers), MAX_WORKERS)
        self.max_inflight = min(max(1, max_inflight), MAX_WORKERS)
        self.queue_limit = queue_limit
        self.ttl_seconds = ttl_seconds
        self.cleanup_interval = cleanup_interval
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

    def reserve(self, contract_id: str, filename: str = "") -> bool:
        if not self._admission.acquire(blocking=False):
            return False

        now = time.time()
        record = TaskRecord(
            contract_id=contract_id, filename=filename, created_at=now
        )
        with self._lock:
            self._tasks[contract_id] = record
        try:
            self.store.create(contract_id, filename, None, now)
        except Exception:
            self._admission.release()
            with self._lock:
                self._tasks.pop(contract_id, None)
            try:
                self.store.delete(contract_id)
            except Exception:
                logger.exception("task.cleanup_failed")
            return False
        return True

    def submit_reserved(self, contract_id: str, cleaned_text: str) -> bool:
        with self._lock:
            record = self._tasks.get(contract_id)
        if record is None:
            return False

        try:
            self.store.set_cleaned_text(contract_id, cleaned_text, time.time())
        except Exception:
            logger.exception("task.persist_failed")
            self.fail_reserved(contract_id, "清洗文本持久化失败")
            return False
        try:
            self._executor.submit(self._run, contract_id, cleaned_text)
        except Exception:
            self.release_reservation(contract_id)
            return False
        return True

    def submit(
        self,
        contract_id: str,
        cleaned_text: str,
        filename: str = "",
    ) -> Optional[str]:
        if not self.reserve(contract_id, filename):
            return None
        if not self.submit_reserved(contract_id, cleaned_text):
            return None
        return contract_id

    def release_reservation(self, contract_id: str) -> None:
        with self._lock:
            self._tasks.pop(contract_id, None)
        try:
            self.store.delete(contract_id)
        except Exception:
            logger.exception("task.cleanup_failed")
        self._admission.release()

    def cancel(self, contract_id: str) -> bool:
        with self._lock:
            record = self._tasks.pop(contract_id, None)
        if record is None:
            return False
        try:
            self.store.delete(contract_id)
        except Exception:
            logger.exception("task.cleanup_failed")
        if record.status == "queued":
            # processing 由 worker 的 finally 释放；done/failed 早已释放
            self._admission.release()
        return True

    def fail_reserved(self, contract_id: str, error: str) -> None:
        now = time.time()
        with self._lock:
            record = self._tasks.get(contract_id)
            if record is not None:
                record.status = "failed"
                record.error = error
                record.finished_at = now
        try:
            self.store.mark_failed(contract_id, error, now)
        except Exception:
            logger.exception("task.persist_failed")
        self._admission.release()

    def get(self, contract_id: str) -> Optional[TaskRecord]:
        with self._lock:
            return self._tasks.get(contract_id)

    def is_expired(self, contract_id: str) -> bool:
        return self.store.is_expired(contract_id)

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
                for contract_id in expired:
                    self._tasks.pop(contract_id, None)
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
        tasks = self.store.list_all()
        now = time.time()
        for task in tasks:
            record = TaskRecord(
                contract_id=task.contract_id,
                filename=task.filename,
                status=task.status,
                result=task.result,
                error=task.error,
                created_at=task.created_at,
                finished_at=(
                    task.updated_at
                    if task.status in ("done", "failed")
                    else None
                ),
            )
            with self._lock:
                self._tasks[record.contract_id] = record

        for task in tasks:
            if task.status not in ("queued", "processing"):
                continue
            if task.cleaned_text is None:
                self._mark_failed(
                    task.contract_id, "持久化记录缺少清洗文本"
                )
                continue
            if task.status == "processing":
                self.store.mark_queued(task.contract_id, now)
                with self._lock:
                    record = self._tasks.get(task.contract_id)
                    if record is not None:
                        record.status = "queued"
                logger.info(
                    "",
                    extra={
                        "event": "task.requeued",
                        "contract_id": task.contract_id,
                        "previous_status": "processing",
                    },
                )
            if not self._admission.acquire(blocking=False):
                logger.warning(
                    "",
                    extra={
                        "event": "task.recovery_deferred",
                        "contract_id": task.contract_id,
                    },
                )
                continue
            logger.info(
                "",
                extra={
                    "event": "task.recovered",
                    "contract_id": task.contract_id,
                },
            )
            self._executor.submit(
                self._run,
                task.contract_id,
                task.cleaned_text,
            )

    def _mark_failed(self, contract_id: str, error: str) -> None:
        now = time.time()
        with self._lock:
            record = self._tasks.get(contract_id)
            if record is not None:
                record.status = "failed"
                record.error = error
                record.finished_at = now
        self.store.mark_failed(contract_id, error, now)

    def _run(self, contract_id: str, cleaned_text: str) -> None:
        now = time.time()
        with self._lock:
            record = self._tasks.get(contract_id)
            if record is None:
                # 排队期间被 cancel，名额已在 cancel 中释放
                return
            record.status = "processing"
            record.started_at = now
        try:
            self.store.mark_processing(contract_id, now)
            report = run_full_review(
                cleaned_text,
                extract_fields(cleaned_text),
                llm_client=self.client,
                run_id=contract_id,
            )
            now = time.time()
            with self._lock:
                if self._tasks.get(contract_id) is not record:
                    return
                record.result = report
                record.status = "done"
                record.finished_at = now
            self.store.mark_done(contract_id, report, now)
        except Exception as exc:
            now = time.time()
            error = f"{type(exc).__name__}: {exc}"
            with self._lock:
                if self._tasks.get(contract_id) is not record:
                    return
                record.status = "failed"
                record.error = error
                record.finished_at = now
            self.store.mark_failed(contract_id, error, now)
        finally:
            self._admission.release()

    def _cleanup_loop(self) -> None:
        while not self._stop.wait(self.cleanup_interval):
            try:
                self.cleanup_expired()
            except Exception:
                logger.exception("task.cleanup_failed")
