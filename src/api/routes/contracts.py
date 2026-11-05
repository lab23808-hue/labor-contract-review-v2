import asyncio
import uuid
from typing import Annotated, Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel

from api.dependencies import (
    get_contract_manager,
    get_parse_semaphore,
    get_review_manager,
    get_review_rate_limiter,
)
from app.contract_manager import ContractManager, run_parse_task
from app.review_tasks import ReviewTaskManager
from config import settings
from core.contract import Contract
from core.errors import ValidationError
from domain.ports import RateLimiter

router = APIRouter(tags=["contracts"])


class BatchItemResponse(BaseModel):
    filename: str
    contract_id: Optional[str]
    status: int
    detail: str


class BatchSummary(BaseModel):
    total: int
    succeeded: int
    failed: int


class BatchUploadResponse(BaseModel):
    items: list[BatchItemResponse]
    summary: BatchSummary


def _artifact_response(content: bytes, media_type: str, filename: str) -> Response:
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"artifact\"; filename*=UTF-8''{quote(filename)}"
        )
    }
    return Response(content=content, media_type=media_type, headers=headers)


@router.post("/contracts", response_model=Contract, status_code=201)
async def upload_contract(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(...)],
    manager: Annotated[ContractManager, Depends(get_contract_manager)],
    review_manager: Annotated[
        ReviewTaskManager, Depends(get_review_manager)
    ],
    rate_limiter: Annotated[
        RateLimiter, Depends(get_review_rate_limiter)
    ],
    semaphore: Annotated[asyncio.Semaphore, Depends(get_parse_semaphore)],
) -> Contract:
    content = await file.read()
    ip = request.client.host if request.client else "unknown"
    allowed, remaining, retry_after = rate_limiter.check(ip)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="上传过于频繁，请稍后再试",
            headers={
                "X-RateLimit-Remaining": "0",
                "Retry-After": str(retry_after),
            },
        )

    contract_id = uuid.uuid4().hex
    filename = file.filename or ""
    if not review_manager.reserve(contract_id, filename):
        raise HTTPException(
            status_code=503,
            detail="系统忙",
            headers={"X-RateLimit-Remaining": str(remaining)},
        )
    try:
        manager.upload(filename, content, contract_id=contract_id)
        contract = manager.start_parse(contract_id)
    except Exception:
        review_manager.release_reservation(contract_id)
        raise

    background_tasks.add_task(
        run_parse_task,
        manager,
        contract_id,
        semaphore,
        review_manager,
    )
    return contract


@router.post("/contracts/batch", response_model=BatchUploadResponse)
async def upload_batch(
    request: Request,
    response: Response,
    files: Annotated[list[UploadFile], File(...)],
    background_tasks: BackgroundTasks,
    manager: Annotated[ContractManager, Depends(get_contract_manager)],
    review_manager: Annotated[
        ReviewTaskManager, Depends(get_review_manager)
    ],
    rate_limiter: Annotated[
        RateLimiter, Depends(get_review_rate_limiter)
    ],
    semaphore: Annotated[asyncio.Semaphore, Depends(get_parse_semaphore)],
) -> BatchUploadResponse:
    if len(files) > settings.MAX_BATCH_FILES:
        raise ValidationError(
            f"批量上传文件数 {len(files)} 超过上限 {settings.MAX_BATCH_FILES}"
        )

    ip = request.client.host if request.client else "unknown"
    contents: list[tuple[str, bytes]] = []
    denied_items: list[BatchItemResponse] = []
    total_bytes = 0
    remaining = 0
    for upload in files:
        content = await upload.read()
        total_bytes += len(content)
        if total_bytes > settings.MAX_BATCH_BYTES:
            raise ValidationError(
                f"批量上传总大小超过上限 {settings.MAX_BATCH_BYTES} 字节"
            )
        allowed, remaining, _ = rate_limiter.check(ip)
        if not allowed:
            denied_items.append(
                BatchItemResponse(
                    filename=upload.filename or "",
                    contract_id=None,
                    status=429,
                    detail="上传过于频繁，请稍后再试",
                )
            )
            continue
        contents.append((upload.filename or "", content))
    response.headers["X-RateLimit-Remaining"] = str(remaining)

    items: list[BatchItemResponse] = []
    succeeded = 0
    for item in manager.batch_upload(contents):
        detail = item.detail
        if item.status == 201:
            contract_id = item.contract_id
            if not review_manager.reserve(contract_id, item.filename):
                manager.delete(contract_id)
                items.append(
                    BatchItemResponse(
                        filename=item.filename,
                        contract_id=None,
                        status=503,
                        detail="系统忙",
                    )
                )
                continue
            manager.start_parse(contract_id)
            background_tasks.add_task(
                run_parse_task,
                manager,
                contract_id,
                semaphore,
                review_manager,
            )
            detail = "uploaded, parse scheduled"
            succeeded += 1
        items.append(
            BatchItemResponse(
                filename=item.filename,
                contract_id=item.contract_id,
                status=item.status,
                detail=detail,
            )
        )

    items.extend(denied_items)
    return BatchUploadResponse(
        items=items,
        summary=BatchSummary(
            total=len(items),
            succeeded=succeeded,
            failed=len(items) - succeeded,
        ),
    )


@router.get("/contracts", response_model=list[Contract])
def list_contracts(
    manager: Annotated[ContractManager, Depends(get_contract_manager)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Contract]:
    return manager.list(limit=limit, offset=offset)


@router.get("/contracts/{contract_id}", response_model=Contract)
def get_contract(
    contract_id: str,
    manager: Annotated[ContractManager, Depends(get_contract_manager)],
) -> Contract:
    return manager.get(contract_id)


@router.post(
    "/contracts/{contract_id}/parse",
    response_model=Contract,
    status_code=202,
    responses={
        202: {
            "description": "解析已受理，请轮询 GET /contracts/{contract_id} 的 status"
        }
    },
)
def parse_contract(
    contract_id: str,
    background_tasks: BackgroundTasks,
    manager: Annotated[ContractManager, Depends(get_contract_manager)],
    semaphore: Annotated[asyncio.Semaphore, Depends(get_parse_semaphore)],
) -> Contract:
    contract = manager.start_parse(contract_id)
    background_tasks.add_task(
        run_parse_task, manager, contract_id, semaphore, None
    )
    return contract


@router.delete("/contracts/{contract_id}", status_code=204)
def delete_contract(
    contract_id: str,
    manager: Annotated[ContractManager, Depends(get_contract_manager)],
    review_manager: Annotated[
        ReviewTaskManager, Depends(get_review_manager)
    ],
) -> Response:
    manager.delete(contract_id)
    review_manager.cancel(contract_id)
    return Response(status_code=204)


@router.get("/contracts/{contract_id}/parsed")
def get_parsed_artifact(
    contract_id: str,
    manager: Annotated[ContractManager, Depends(get_contract_manager)],
) -> Response:
    filename, content = manager.parsed_artifact(contract_id)
    return _artifact_response(content, "text/markdown", filename)


@router.get("/contracts/{contract_id}/cleaned")
def get_cleaned_artifact(
    contract_id: str,
    manager: Annotated[ContractManager, Depends(get_contract_manager)],
) -> Response:
    filename, content = manager.cleaned_artifact(contract_id)
    return _artifact_response(content, "text/plain", filename)
