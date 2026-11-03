from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi import APIRouter, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

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

router = APIRouter(tags=["review"])


def build_parsers() -> dict[str, DocumentParser]:
    return {
        "txt": TxtParser(),
        "docx": DocxParser(),
        "pdf": MinerUParser(),
    }


@router.post("/upload", status_code=202)
async def upload(
    request: Request,
    file: UploadFile = File(...),
) -> JSONResponse:
    manager = request.app.state.review_manager
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in manager.allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型：{suffix or '(无扩展名)'}",
        )

    content = await file.read()
    if len(content) > manager.max_file_size:
        raise HTTPException(
            status_code=400,
            detail=f"文件超过上限 {manager.max_file_size} 字节",
        )

    ip = request.client.host if request.client else "unknown"
    allowed, remaining, retry_after = manager.rate_limiter.check(ip)
    headers = {"X-RateLimit-Remaining": str(remaining)}
    if not allowed:
        headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=429,
            detail="上传过于频繁，请稍后再试",
            headers=headers,
        )

    task_id = manager.submit(filename, content)
    if task_id is None:
        raise HTTPException(
            status_code=503,
            detail="系统忙",
            headers=headers,
        )
    return JSONResponse(
        status_code=202,
        content={"task_id": task_id, "status": "queued"},
        headers=headers,
    )


@router.get("/result/{task_id}")
def get_result(task_id: str, request: Request) -> dict[str, Any]:
    manager = request.app.state.review_manager
    record = manager.get(task_id)
    if record is None:
        if manager.is_expired(task_id):
            raise HTTPException(status_code=410, detail="任务已过期")
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "task_id": record.task_id,
        "status": record.status,
        "result": record.result,
        "error": record.error,
    }


@router.get("/stats")
def get_stats(request: Request) -> dict[str, Any]:
    return request.app.state.review_manager.stats()


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
    app.include_router(router)
    return app
