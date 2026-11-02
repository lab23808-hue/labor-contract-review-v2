import asyncio
from typing import Annotated, Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Query,
    Response,
    UploadFile,
)
from pydantic import BaseModel

from api.dependencies import get_contract_manager, get_parse_semaphore
from app.contract_manager import ContractManager, run_parse_task
from config import settings
from core.contract import Contract
from core.errors import ValidationError

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
    file: Annotated[UploadFile, File(...)],
    manager: Annotated[ContractManager, Depends(get_contract_manager)],
) -> Contract:
    content = await file.read()
    return manager.upload(file.filename or "", content)


@router.post("/contracts/batch", response_model=BatchUploadResponse)
async def upload_batch(
    files: Annotated[list[UploadFile], File(...)],
    background_tasks: BackgroundTasks,
    manager: Annotated[ContractManager, Depends(get_contract_manager)],
    semaphore: Annotated[asyncio.Semaphore, Depends(get_parse_semaphore)],
) -> BatchUploadResponse:
    if len(files) > settings.MAX_BATCH_FILES:
        raise ValidationError(
            f"批量上传文件数 {len(files)} 超过上限 {settings.MAX_BATCH_FILES}"
        )

    contents: list[tuple[str, bytes]] = []
    total_bytes = 0
    for upload in files:
        content = await upload.read()
        total_bytes += len(content)
        if total_bytes > settings.MAX_BATCH_BYTES:
            raise ValidationError(
                f"批量上传总大小超过上限 {settings.MAX_BATCH_BYTES} 字节"
            )
        contents.append((upload.filename or "", content))

    items: list[BatchItemResponse] = []
    succeeded = 0
    for item in manager.batch_upload(contents):
        detail = item.detail
        if item.status == 201:
            manager.start_parse(item.contract_id)
            background_tasks.add_task(
                run_parse_task, manager, item.contract_id, semaphore
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
    background_tasks.add_task(run_parse_task, manager, contract_id, semaphore)
    return contract


@router.delete("/contracts/{contract_id}", status_code=204)
def delete_contract(
    contract_id: str,
    manager: Annotated[ContractManager, Depends(get_contract_manager)],
) -> Response:
    manager.delete(contract_id)
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
