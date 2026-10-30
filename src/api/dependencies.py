from typing import Annotated

from fastapi import Depends, Request

from app.contract_manager import ContractManager
from domain.ports import ContractRepository


def get_contract_repository(request: Request) -> ContractRepository:
    return request.app.state.contract_repository


def get_contract_manager(
    repository: Annotated[ContractRepository, Depends(get_contract_repository)],
) -> ContractManager:
    return ContractManager(repository)
