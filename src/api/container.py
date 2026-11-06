import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from app.contract_manager import ContractManager
from app.parsing_service import ParsingService
from app.review_queue import ReviewQueue
from app.review_service import ReviewService
from app.upload_pipeline import UploadPipeline
from config import settings
from domain.ports import (
    ContractRepository,
    DocumentParser,
    DocumentValidator,
)
from infra.llm_client import DeepSeekLLMClient
from infra.parsers import build_parsers, registry_mismatch
from infra.pdf_validator import PdfValidator
from infra.rate_limiter import RequestThrottle, SlidingWindowRateLimiter
from infra.review_store import ReviewTaskStore
from infra.sqlite_repository import SQLiteContractRepository
from infra.upload_validator import FileUploadValidator, UploadPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppContainer:
    contract_repository: ContractRepository
    parsers: Mapping[str, DocumentParser]
    validator: DocumentValidator
    contract_manager: ContractManager
    review_queue: ReviewQueue
    review_service: ReviewService
    upload_pipeline: UploadPipeline


def build_container(
    repository: Optional[ContractRepository] = None,
    parsers: Optional[Mapping[str, DocumentParser]] = None,
    validator: Optional[DocumentValidator] = None,
    review_client: Any = None,
    review_store_path: Optional[Path | str] = None,
    review_workers: Optional[int] = None,
    review_max_inflight: Optional[int] = None,
    review_rps: Optional[float] = None,
    review_queue_limit: Optional[int] = None,
    review_rate_limit: Optional[int] = None,
    review_rate_window: Optional[float] = None,
    review_ttl_seconds: Optional[float] = None,
    review_cleanup_interval: Optional[float] = None,
) -> AppContainer:
    # 全项目唯一允许直接 import infra 的地方：组装根。
    contract_repository = (
        repository if repository is not None else SQLiteContractRepository()
    )
    if parsers is None:
        parsers = build_parsers()
        missing, unused = registry_mismatch(
            parsers, settings.ALLOWED_EXTENSIONS
        )
        if missing:
            logger.warning(
                "",
                extra={
                    "event": "config.allowed_without_parser",
                    "missing": missing,
                },
            )
        if unused:
            logger.warning(
                "",
                extra={
                    "event": "config.parser_not_allowed",
                    "unused": unused,
                },
            )
    validator = validator if validator is not None else PdfValidator()

    workers = (
        review_workers
        if review_workers is not None
        else settings.REVIEW_WORKERS
    )
    max_inflight = (
        review_max_inflight
        if review_max_inflight is not None
        else settings.REVIEW_MAX_INFLIGHT
    )
    rps = review_rps if review_rps is not None else settings.REVIEW_RPS
    queue_limit = (
        review_queue_limit
        if review_queue_limit is not None
        else settings.REVIEW_QUEUE_LIMIT
    )
    rate_limit = (
        review_rate_limit
        if review_rate_limit is not None
        else settings.REVIEW_RATE_LIMIT
    )
    rate_window = (
        review_rate_window
        if review_rate_window is not None
        else settings.REVIEW_RATE_WINDOW
    )
    ttl_seconds = (
        review_ttl_seconds
        if review_ttl_seconds is not None
        else settings.REVIEW_TTL_SECONDS
    )
    cleanup_interval = (
        review_cleanup_interval
        if review_cleanup_interval is not None
        else settings.REVIEW_CLEANUP_INTERVAL
    )
    store_path = (
        review_store_path
        if review_store_path is not None
        else settings.REVIEW_STORE_PATH
    )

    if review_client is None:
        review_client = DeepSeekLLMClient(
            throttle=RequestThrottle(
                rps, min(max(1, max_inflight), 16)
            )
        )

    review_queue = ReviewQueue(
        review_client,
        ReviewTaskStore(store_path),
        workers=workers,
        max_inflight=max_inflight,
        queue_limit=queue_limit,
        ttl_seconds=ttl_seconds,
        cleanup_interval=cleanup_interval,
    )
    review_service = ReviewService(review_queue)
    contract_manager = ContractManager(
        contract_repository,
        parsers,
        FileUploadValidator(
            policy=UploadPolicy(
                allowed_extensions=settings.ALLOWED_EXTENSIONS,
                max_upload_bytes=settings.MAX_UPLOAD_BYTES,
            ),
            document_validator=validator,
        ),
    )
    upload_pipeline = UploadPipeline(
        contract_manager,
        ParsingService(contract_repository, parsers),
        review_service,
        SlidingWindowRateLimiter(rate_limit, rate_window),
        asyncio.Semaphore(1),
        max_upload_bytes=settings.MAX_UPLOAD_BYTES,
        max_batch_files=settings.MAX_BATCH_FILES,
        max_batch_bytes=settings.MAX_BATCH_BYTES,
    )
    return AppContainer(
        contract_repository=contract_repository,
        parsers=parsers,
        validator=validator,
        contract_manager=contract_manager,
        review_queue=review_queue,
        review_service=review_service,
        upload_pipeline=upload_pipeline,
    )
