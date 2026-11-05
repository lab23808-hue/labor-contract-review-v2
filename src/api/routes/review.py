from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from api.dependencies import get_review_service
from api.review_presenters import (
    error_context,
    failed_context,
    render_page,
    report_context,
    status_context,
)
from app.review_service import ReviewService

router = APIRouter(tags=["review"])

ServiceDep = Annotated[ReviewService, Depends(get_review_service)]


@router.get("/result/{contract_id}")
def get_result(contract_id: str, manager: ServiceDep) -> dict[str, Any]:
    record = manager.get(contract_id)
    if record is None:
        if manager.is_expired(contract_id):
            raise HTTPException(status_code=410, detail="合同审查已过期")
        raise HTTPException(status_code=404, detail="合同审查不存在")
    return {
        "contract_id": record.task_id,
        "status": record.status,
        "result": record.result,
        "error": record.error,
    }


@router.get("/stats")
def get_stats(manager: ServiceDep) -> dict[str, Any]:
    return manager.stats()


@router.get("/report/{contract_id}", response_class=HTMLResponse)
def get_report(
    contract_id: str, request: Request, manager: ServiceDep
):
    record = manager.get(contract_id)
    if record is None:
        if manager.is_expired(contract_id):
            return render_page(
                request,
                error_context(
                    contract_id,
                    "任务已过期",
                    "结果已按保留策略清理，无法再查看。",
                ),
                410,
            )
        return render_page(
            request,
            error_context(
                contract_id,
                "任务不存在",
                "没有找到这个合同审查记录。",
            ),
            404,
        )
    if record.status in ("queued", "processing"):
        return render_page(request, status_context(record), 200)
    if record.status == "failed":
        return render_page(request, failed_context(record), 200)
    return render_page(request, report_context(record), 200)
