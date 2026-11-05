import asyncio
from typing import Annotated

from fastapi import Depends, Request

from app.contract_manager import ContractManager
from app.review_tasks import ReviewTaskManager
from domain.ports import ContractRepository


def get_contract_repository(request: Request) -> ContractRepository:
    return request.app.state.contract_repository


def get_review_manager(request: Request) -> ReviewTaskManager:
    return request.app.state.review_manager


def get_parse_semaphore(request: Request) -> asyncio.Semaphore:
    return request.app.state.parse_semaphore


def get_contract_manager(
    request: Request,
    repository: Annotated[ContractRepository, Depends(get_contract_repository)],
) -> ContractManager:
    return ContractManager(
        repository, request.app.state.parsers, request.app.state.validator
    )
