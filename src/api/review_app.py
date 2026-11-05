from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi import FastAPI

from api.routes import review
from app.review_tasks import (
    DEFAULT_CLEANUP_INTERVAL,
    DEFAULT_MAX_FILE_SIZE,
    DEFAULT_MAX_INFLIGHT,
    DEFAULT_QUEUE_LIMIT,
    DEFAULT_RATE_LIMIT,
    DEFAULT_RATE_WINDOW,
    DEFAULT_RPS,
    DEFAULT_STORE_PATH,
    DEFAULT_TTL_SECONDS,
    DEFAULT_WORKERS,
    ReviewTaskManager,
)
from domain.ports import DocumentParser
from infra.docx_parser import DocxParser
from infra.mineru_parser import MinerUParser
from infra.txt_parser import TxtParser


def build_parsers() -> dict[str, DocumentParser]:
    return {
        "txt": TxtParser(),
        "docx": DocxParser(),
        "pdf": MinerUParser(),
    }


def create_review_app(
    client: Optional[Any] = None,
    parsers: Optional[Mapping[str, DocumentParser]] = None,
    workers: int = DEFAULT_WORKERS,
    max_inflight: int = DEFAULT_MAX_INFLIGHT,
    rps: float = DEFAULT_RPS,
    queue_limit: int = DEFAULT_QUEUE_LIMIT,
    rate_limit: int = DEFAULT_RATE_LIMIT,
    rate_window: float = DEFAULT_RATE_WINDOW,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    store_path: Path | str = DEFAULT_STORE_PATH,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    cleanup_interval: float = DEFAULT_CLEANUP_INTERVAL,
) -> FastAPI:
    if client is None:
        from infra.llm_client import DeepSeekLLMClient
        from infra.rate_limiter import RequestThrottle

        client = DeepSeekLLMClient(
            throttle=RequestThrottle(
                rps, min(max(1, max_inflight), 16)
            )
        )

    manager = ReviewTaskManager(
        client,
        parsers if parsers is not None else build_parsers(),
        workers=workers,
        max_inflight=max_inflight,
        queue_limit=queue_limit,
        rate_limit=rate_limit,
        rate_window=rate_window,
        max_file_size=max_file_size,
        store_path=store_path,
        ttl_seconds=ttl_seconds,
        cleanup_interval=cleanup_interval,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.review_manager.close()

    app = FastAPI(
        title="Labor Contract Review - Upload API",
        lifespan=lifespan,
    )
    app.state.review_manager = manager
    app.include_router(review.router)
    return app
