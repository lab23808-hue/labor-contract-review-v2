from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from api.dependencies import get_upload_service
from app.upload_service import UploadService
from core.contract import Contract

router = APIRouter(tags=["contracts"])


@router.post("/contracts", response_model=Contract, status_code=201)
async def upload_contract(
    file: Annotated[UploadFile, File(...)],
    service: Annotated[UploadService, Depends(get_upload_service)],
) -> Contract:
    content = await file.read()
    return service.upload(file.filename or "", content)


@router.get("/contracts/{contract_id}", response_model=Contract)
def get_contract(
    contract_id: str,
    service: Annotated[UploadService, Depends(get_upload_service)],
) -> Contract:
    return service.get(contract_id)
