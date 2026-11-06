import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from typing import Optional, Sequence

from app.contract_manager import ContractManager
from app.parsing_service import ParsingService
from app.review_service import ReviewService
from core.contract import Contract
from core.errors import (
    CorruptedFileError,
    QueueFullError,
    RateLimitExceeded,
    ValidationError,
)
from domain.ports import DuplicateContractError, RateLimiter


@dataclass(frozen=True)
class IngestResult:
    contract: Contract
    rate_limit_remaining: int


@dataclass(frozen=True)
class BatchItem:
    filename: str
    contract_id: Optional[str]
    outcome: str
    detail: str


@dataclass(frozen=True)
class BatchIngestResult:
    items: list[BatchItem]
    rate_limit_remaining: int


class UploadPipeline:
    def __init__(
        self,
        contract_manager: ContractManager,
        parsing_service: ParsingService,
        review_service: ReviewService,
        rate_limiter: RateLimiter,
        parse_semaphore: asyncio.Semaphore,
        *,
        max_upload_bytes: int,
        max_batch_files: int,
        max_batch_bytes: int,
    ) -> None:
        self._contracts = contract_manager
        self._parsing = parsing_service
        self._review = review_service
        self._rate_limiter = rate_limiter
        self._parse_semaphore = parse_semaphore
        self._max_upload_bytes = max_upload_bytes
        self._max_batch_files = max_batch_files
        self._max_batch_bytes = max_batch_bytes

    @property
    def max_upload_bytes(self) -> int:
        return self._max_upload_bytes

    @property
    def max_batch_bytes(self) -> int:
        return self._max_batch_bytes

    def ingest(
        self, filename: str, content: bytes, client_ip: str
    ) -> IngestResult:
        remaining = self._check_rate(client_ip)
        return IngestResult(
            contract=self._create(filename, content),
            rate_limit_remaining=remaining,
        )

    def ingest_batch(
        self,
        files: Sequence[tuple[str, Optional[bytes]]],
        client_ip: str,
    ) -> BatchIngestResult:
        if len(files) > self._max_batch_files:
            raise ValidationError(
                f"批量上传文件数 {len(files)} 超过上限 "
                f"{self._max_batch_files}"
            )

        items: list[BatchItem] = []
        total_bytes = 0
        remaining = 0
        for filename, content in files:
            if content is None:
                items.append(
                    BatchItem(
                        filename,
                        None,
                        "invalid",
                        f"文件大小超过上限 {self._max_upload_bytes} 字节",
                    )
                )
                continue
            total_bytes += len(content)
            if total_bytes > self._max_batch_bytes:
                raise ValidationError(
                    f"批量上传总大小超过上限 "
                    f"{self._max_batch_bytes} 字节"
                )
            allowed, remaining, _ = self._rate_limiter.check(client_ip)
            if not allowed:
                items.append(
                    BatchItem(
                        filename,
                        None,
                        "rate_limited",
                        "上传过于频繁，请稍后再试",
                    )
                )
                continue
            items.append(self._ingest_one(filename, content))
        return BatchIngestResult(
            items=items, rate_limit_remaining=remaining
        )

    def start_parse(self, contract_id: str) -> Contract:
        return self._parsing.start(contract_id)

    def delete(self, contract_id: str) -> None:
        self._contracts.delete(contract_id)
        self._review.cancel(contract_id)

    async def process(
        self, contract_id: str, enqueue_review: bool = True
    ) -> None:
        async with self._parse_semaphore:
            try:
                contract = await asyncio.to_thread(
                    self._parsing.execute, contract_id
                )
            except Exception as exc:
                if enqueue_review:
                    self._review.fail_reserved(
                        contract_id, f"{type(exc).__name__}: {exc}"
                    )
                raise

        if not enqueue_review:
            return
        if contract.status != "parsed":
            self._review.fail_reserved(
                contract_id, contract.error or "解析失败"
            )
            return
        try:
            _, cleaned_bytes = self._contracts.cleaned_artifact(
                contract_id
            )
            cleaned_text = cleaned_bytes.decode("utf-8")
        except Exception as exc:
            self._review.fail_reserved(
                contract_id, f"{type(exc).__name__}: {exc}"
            )
            return
        self._review.submit_reserved(contract_id, cleaned_text)

    def _create(self, filename: str, content: bytes) -> Contract:
        contract_id = uuid.uuid4().hex
        if not self._review.reserve(contract_id, filename):
            raise QueueFullError()
        try:
            self._contracts.upload(
                filename, content, contract_id=contract_id
            )
            return self._parsing.start(contract_id)
        except Exception:
            self._review.release_reservation(contract_id)
            raise

    def _check_rate(self, client_ip: str) -> int:
        allowed, remaining, retry_after = self._rate_limiter.check(
            client_ip
        )
        if not allowed:
            raise RateLimitExceeded(retry_after)
        return remaining

    def _ingest_one(self, filename: str, content: bytes) -> BatchItem:
        try:
            contract = self._create(filename, content)
        except DuplicateContractError as exc:
            existing = self._contracts.find_by_sha256(
                hashlib.sha256(content).hexdigest()
            )
            existing_id = (
                existing.contract_id if existing is not None else None
            )
            return BatchItem(
                filename, existing_id, "duplicate", str(exc)
            )
        except CorruptedFileError as exc:
            return BatchItem(filename, None, "corrupted", str(exc))
        except ValidationError as exc:
            return BatchItem(filename, None, "invalid", str(exc))
        except QueueFullError:
            return BatchItem(filename, None, "queue_full", "系统忙")
        return BatchItem(
            filename,
            contract.contract_id,
            "created",
            "uploaded, parse scheduled",
        )
