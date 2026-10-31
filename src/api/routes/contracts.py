from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile

from api.dependencies import get_contract_manager
from app.contract_manager import ContractManager
from core.contract import Contract

router = APIRouter(tags=["contracts"])


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


@router.post("/contracts/{contract_id}/parse", response_model=Contract)
def parse_contract(
    contract_id: str,
    manager: Annotated[ContractManager, Depends(get_contract_manager)],
) -> Contract:
    return manager.parse(contract_id)
