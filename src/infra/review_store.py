import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from core.review_task import ReviewTask

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_tasks (
    -- 领域名为 contract_id；列名保留 task_id 以兼容旧库
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

    def create(
        self,
        contract_id: str,
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
                (contract_id, filename, cleaned_text, now, now),
            )
            self._conn.commit()

    def set_cleaned_text(
        self, contract_id: str, cleaned_text: str, now: float
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE review_tasks SET status='queued', "
                "cleaned_text=?, updated_at=? WHERE task_id=?",
                (cleaned_text, now, contract_id),
            )
            self._conn.commit()

    def mark_processing(self, contract_id: str, now: float) -> None:
        self._update_status(contract_id, "processing", now)

    def mark_queued(self, contract_id: str, now: float) -> None:
        self._update_status(contract_id, "queued", now)

    def mark_done(
        self, contract_id: str, result: dict[str, Any], now: float
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE review_tasks SET status='done', "
                "result_json=?, error=NULL, cleaned_text=NULL, "
                "updated_at=? WHERE task_id=?",
                (
                    json.dumps(result, ensure_ascii=False),
                    now,
                    contract_id,
                ),
            )
            self._conn.commit()

    def mark_failed(
        self, contract_id: str, error: str, now: float
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE review_tasks SET status='failed', "
                "result_json=NULL, error=?, cleaned_text=NULL, "
                "updated_at=? WHERE task_id=?",
                (error, now, contract_id),
            )
            self._conn.commit()

    def delete(self, contract_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM review_tasks WHERE task_id=?",
                (contract_id,),
            )
            self._conn.commit()

    def get(self, contract_id: str) -> Optional[ReviewTask]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM review_tasks WHERE task_id=?",
                (contract_id,),
            ).fetchone()
        return _row_to_task(row) if row is not None else None

    def list_all(self) -> list[ReviewTask]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM review_tasks ORDER BY created_at"
            ).fetchall()
        return [_row_to_task(row) for row in rows]

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

    def is_expired(self, contract_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM expired_task_ids WHERE task_id=?",
                (contract_id,),
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
        self, contract_id: str, status: str, now: float
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE review_tasks SET status=?, updated_at=? "
                "WHERE task_id=?",
                (status, now, contract_id),
            )
            self._conn.commit()


def _row_to_task(row: sqlite3.Row) -> ReviewTask:
    return ReviewTask(
        contract_id=row["task_id"],
        filename=row["filename"],
        status=row["status"],
        result=(
            json.loads(row["result_json"])
            if row["result_json"]
            else None
        ),
        error=row["error"],
        cleaned_text=row["cleaned_text"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
