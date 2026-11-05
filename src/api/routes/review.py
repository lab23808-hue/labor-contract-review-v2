from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from api.dependencies import get_review_manager
from api.review_presenters import (
    error_context,
    failed_context,
    render_page,
    report_context,
    status_context,
)
from app.review_tasks import ReviewTaskManager

router = APIRouter(tags=["review"])

ManagerDep = Annotated[ReviewTaskManager, Depends(get_review_manager)]


@router.post("/upload", status_code=202)
async def upload(
    request: Request,
    manager: ManagerDep,
    file: Annotated[UploadFile, File(...)],
) -> JSONResponse:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in manager.allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型：{suffix or '(无扩展名)'}",
        )

    content = await file.read()
    if len(content) > manager.max_file_size:
        raise HTTPException(
            status_code=400,
            detail=f"文件超过上限 {manager.max_file_size} 字节",
        )

    ip = request.client.host if request.client else "unknown"
    allowed, remaining, retry_after = manager.rate_limiter.check(ip)
    headers = {"X-RateLimit-Remaining": str(remaining)}
    if not allowed:
        headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=429,
            detail="上传过于频繁，请稍后再试",
            headers=headers,
        )

    task_id = manager.submit(filename, content)
    if task_id is None:
        raise HTTPException(
            status_code=503,
            detail="系统忙",
            headers=headers,
        )
    return JSONResponse(
        status_code=202,
        content={"task_id": task_id, "status": "queued"},
        headers=headers,
    )


@router.get("/result/{task_id}")
def get_result(task_id: str, manager: ManagerDep) -> dict[str, Any]:
    record = manager.get(task_id)
    if record is None:
        if manager.is_expired(task_id):
            raise HTTPException(status_code=410, detail="任务已过期")
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "task_id": record.task_id,
        "status": record.status,
        "result": record.result,
        "error": record.error,
    }


@router.get("/stats")
def get_stats(manager: ManagerDep) -> dict[str, Any]:
    return manager.stats()


@router.get("/report/{task_id}", response_class=HTMLResponse)
def get_report(task_id: str, request: Request, manager: ManagerDep):
    record = manager.get(task_id)
    if record is None:
        if manager.is_expired(task_id):
            return render_page(
                request,
                error_context(
                    task_id,
                    "任务已过期",
                    "结果已按保留策略清理，无法再查看。",
                ),
                410,
            )
        return render_page(
            request,
            error_context(
                task_id,
                "任务不存在",
                "没有找到这个 task_id。",
            ),
            404,
        )
    if record.status in ("queued", "processing"):
        return render_page(request, status_context(record), 200)
    if record.status == "failed":
        return render_page(request, failed_context(record), 200)
    return render_page(request, report_context(record), 200)
