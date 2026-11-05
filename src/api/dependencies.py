from fastapi import Request

from app.contract_manager import ContractManager
from app.review_service import ReviewService
from app.upload_pipeline import UploadPipeline


def get_review_service(request: Request) -> ReviewService:
    return request.app.state.review_service


def get_upload_pipeline(request: Request) -> UploadPipeline:
    return request.app.state.upload_pipeline


def get_contract_manager(request: Request) -> ContractManager:
    return request.app.state.contract_manager
