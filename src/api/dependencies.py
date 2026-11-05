import asyncio

from fastapi import Request

from app.contract_manager import ContractManager
from app.review_service import ReviewService
from domain.ports import RateLimiter


def get_review_service(request: Request) -> ReviewService:
    return request.app.state.review_service


def get_review_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.review_rate_limiter


def get_parse_semaphore(request: Request) -> asyncio.Semaphore:
    return request.app.state.parse_semaphore


def get_contract_manager(request: Request) -> ContractManager:
    return request.app.state.contract_manager
