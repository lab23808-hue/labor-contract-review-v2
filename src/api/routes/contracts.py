import asyncio
from typing import Annotated
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

from api.dependencies import get_contract_manager, get_parse_semaphore
from app.contract_manager import ContractManager, run_parse_task
from core.contract import Contract

router = APIRouter(tags=["contracts"])


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
