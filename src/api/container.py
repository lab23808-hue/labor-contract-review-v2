import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from app.contract_manager import ContractManager
from app.review_tasks import ReviewTaskManager
from config import settings
from domain.ports import (
    ContractRepository,
    DocumentParser,
    DocumentValidator,
    RateLimiter,
)
from infra.docx_parser import DocxParser
from infra.llm_client import DeepSeekLLMClient
from infra.mineru_parser import MinerUParser
from infra.pdf_validator import PdfValidator
from infra.rate_limiter import RequestThrottle, SlidingWindowRateLimiter
from infra.sqlite_repository import SQLiteContractRepository
from infra.txt_parser import TxtParser


def build_parsers() -> dict[str, DocumentParser]:
    mineru = MinerUParser()
    return {
        "txt": TxtParser(),
        "docx": DocxParser(),
        "pdf": mineru,
        "jpg": mineru,
        "png": mineru,
    }


@dataclass(frozen=True)
class AppContainer:
    contract_repository: ContractRepository
    parsers: Mapping[str, DocumentParser]
    validator: DocumentValidator
    contract_manager: ContractManager
    review_manager: ReviewTaskManager
    review_rate_limiter: RateLimiter
    parse_semaphore: asyncio.Semaphore


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
    parsers = parsers if parsers is not None else build_parsers()
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

    review_manager = ReviewTaskManager(
        review_client,
        workers=workers,
        max_inflight=max_inflight,
        queue_limit=queue_limit,
        store_path=store_path,
        ttl_seconds=ttl_seconds,
        cleanup_interval=cleanup_interval,
    )
    return AppContainer(
        contract_repository=contract_repository,
        parsers=parsers,
        validator=validator,
        contract_manager=ContractManager(
            contract_repository, parsers, validator
        ),
        review_manager=review_manager,
        review_rate_limiter=SlidingWindowRateLimiter(
            rate_limit, rate_window
        ),
        parse_semaphore=asyncio.Semaphore(1),
    )
