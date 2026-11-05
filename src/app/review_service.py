from dataclasses import dataclass
from typing import Any, Optional

from app.review_queue import ReviewQueue, TaskRecord


@dataclass(frozen=True)
class ReviewView:
    contract_id: str
    filename: str
    status: str
    result: Optional[dict[str, Any]]
    error: Optional[str]
    created_at: float
    finished_at: Optional[float]


class ReviewService:
    """路由与后台任务访问审查队列的唯一入口。"""

    def __init__(self, queue: ReviewQueue) -> None:
        self._queue = queue

    def reserve(self, contract_id: str, filename: str = "") -> bool:
        return self._queue.reserve(contract_id, filename)

    def submit_reserved(
        self, contract_id: str, cleaned_text: str
    ) -> bool:
        return self._queue.submit_reserved(contract_id, cleaned_text)

    def release_reservation(self, contract_id: str) -> None:
        self._queue.release_reservation(contract_id)

    def fail_reserved(self, contract_id: str, error: str) -> None:
        self._queue.fail_reserved(contract_id, error)

    def cancel(self, contract_id: str) -> bool:
        return self._queue.cancel(contract_id)

    def get(self, contract_id: str) -> Optional[ReviewView]:
        record = self._queue.get(contract_id)
        if record is None:
            return None
        return _to_view(record)

    def is_expired(self, contract_id: str) -> bool:
        return self._queue.is_expired(contract_id)

    def stats(self) -> dict[str, Any]:
        return self._queue.stats()

    def cleanup_expired(self) -> int:
        return self._queue.cleanup_expired()


def _to_view(record: TaskRecord) -> ReviewView:
    return ReviewView(
        contract_id=record.contract_id,
        filename=record.filename,
        status=record.status,
        result=record.result,
        error=record.error,
        created_at=record.created_at,
        finished_at=record.finished_at,
    )
