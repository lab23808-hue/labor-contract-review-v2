from typing import Annotated, Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Query,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel

from api.dependencies import get_contract_manager, get_upload_pipeline
from app.contract_manager import ContractManager
from app.upload_pipeline import UploadPipeline
from core.contract import Contract
from core.errors import ValidationError

router = APIRouter(tags=["contracts"])

_BATCH_STATUS = {
    "created": 201,
    "duplicate": 409,
    "invalid": 422,
    "corrupted": 400,
    "rate_limited": 429,
    "queue_full": 503,
}


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
    pipeline: Annotated[
        UploadPipeline, Depends(get_upload_pipeline)
    ],
) -> Contract:
    limit = pipeline.max_upload_bytes
    size = getattr(file, "size", None)
    if size is not None and size > limit:
        raise ValidationError(
            f"文件大小 {size} 字节超过上限 {limit} 字节"
        )
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise ValidationError(f"文件大小超过上限 {limit} 字节")
    ip = request.client.host if request.client else "unknown"
    result = pipeline.ingest(file.filename or "", content, ip)
    response.headers["X-RateLimit-Remaining"] = str(
        result.rate_limit_remaining
    )
    background_tasks.add_task(
        pipeline.process, result.contract.contract_id
    )
    return result.contract


@router.post("/contracts/batch", response_model=BatchUploadResponse)
async def upload_batch(
    request: Request,
    response: Response,
    files: Annotated[list[UploadFile], File(...)],
    background_tasks: BackgroundTasks,
    pipeline: Annotated[
        UploadPipeline, Depends(get_upload_pipeline)
    ],
) -> BatchUploadResponse:
    ip = request.client.host if request.client else "unknown"
    limit_file = pipeline.max_upload_bytes
    limit_total = pipeline.max_batch_bytes
    payload: list[tuple[str, Optional[bytes]]] = []
    total_bytes = 0
    for upload in files:
        filename = upload.filename or ""
        size = getattr(upload, "size", None)
        if size is not None:
            if size > limit_file:
                payload.append((filename, None))
                continue
            if total_bytes + size > limit_total:
                raise ValidationError(
                    f"批量上传总大小超过上限 {limit_total} 字节"
                )
            payload.append((filename, await upload.read(size)))
            total_bytes += size
            continue

        cap = min(limit_file, limit_total - total_bytes)
        content = await upload.read(cap + 1)
        if len(content) > limit_file:
            payload.append((filename, None))
            continue
        if total_bytes + len(content) > limit_total:
            raise ValidationError(
                f"批量上传总大小超过上限 {limit_total} 字节"
            )
        payload.append((filename, content))
        total_bytes += len(content)

    result = pipeline.ingest_batch(payload, ip)
    response.headers["X-RateLimit-Remaining"] = str(
        result.rate_limit_remaining
    )

    items: list[BatchItemResponse] = []
    succeeded = 0
    for item in result.items:
        if item.outcome == "created" and item.contract_id is not None:
            background_tasks.add_task(
                pipeline.process, item.contract_id
            )
            succeeded += 1
        items.append(
            BatchItemResponse(
                filename=item.filename,
                contract_id=item.contract_id,
                status=_BATCH_STATUS[item.outcome],
                detail=item.detail,
            )
        )

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
    pipeline: Annotated[
        UploadPipeline, Depends(get_upload_pipeline)
    ],
) -> Contract:
    contract = pipeline.start_parse(contract_id)
    background_tasks.add_task(pipeline.process, contract_id, False)
    return contract


@router.delete("/contracts/{contract_id}", status_code=204)
def delete_contract(
    contract_id: str,
    pipeline: Annotated[
        UploadPipeline, Depends(get_upload_pipeline)
    ],
) -> Response:
    pipeline.delete(contract_id)
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
