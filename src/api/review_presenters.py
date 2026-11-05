from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(
    env=Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
    )
)

DISCLAIMER = "自动审查结果仅供参考，不构成法律意见。"

RISK_TYPE_NAMES = {
    "probation_exceed": "试用期超限",
    "illegal_penalty": "违法违约金",
    "disguised_waiver": "变相免责或放弃权利",
    "wage_below_floor": "试用期工资低于下限",
    "term_inconsistent": "合同期限前后矛盾",
    "social_security_missing": "社会保险条款缺失",
    "open_ended_denied": "无固定期限权利被排除",
    "noncompete_no_pay": "竞业限制未约定补偿",
    "noncompete_exceed": "竞业限制超过两年",
}

SEVERITY_LABELS = {"high": "高", "medium": "中", "low": "低"}
SEVERITY_CLASSES = {"high": "high", "medium": "medium", "low": "low"}
SOURCE_LABELS = {
    "rules": ("规则命中", "rules"),
    "llm": ("LLM 判定", "llm"),
}

CHECK_ITEMS = [
    "试用期期限是否超过法定上限（含「试工」「见习」等变相叫法）",
    "是否存在违法违约金或罚款条款",
    "是否存在变相免责、放弃法定权利的话术",
    "试用期工资是否低于转正工资的 80%",
    "合同期限前后表述是否矛盾",
    "是否缺失社会保险条款",
    "是否排除劳动者签订无固定期限劳动合同的权利",
    "竞业限制期限是否超过两年",
    "竞业限制是否约定经济补偿",
]


def render_page(
    request: Request, context: dict[str, Any], status_code: int
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="report.html",
        context=context,
        status_code=status_code,
    )


def _base_context(
    *,
    mode: str,
    title: str,
    task_id: str,
    filename: str,
    status_label: str,
    message: str = "",
    error: str = "",
    refresh_seconds: Optional[int] = None,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "title": title,
        "task_id": task_id,
        "filename": filename,
        "status_label": status_label,
        "message": message,
        "error": error,
        "refresh_seconds": refresh_seconds,
        "disclaimer": DISCLAIMER,
        "risks": [],
        "observations": [],
        "checks": [],
        "risk_count": 0,
        "overall": "",
        "overall_class": "none",
        "review_time": "",
        "tokens": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def error_context(
    task_id: str, title: str, message: str
) -> dict[str, Any]:
    return _base_context(
        mode="error",
        title=title,
        task_id=task_id,
        filename="",
        status_label=title,
        message=message,
    )


def status_context(record) -> dict[str, Any]:
    labels = {"queued": "排队中", "processing": "审查中"}
    messages = {
        "queued": "任务已受理，正在等待处理。",
        "processing": "正在审查合同，请稍候。",
    }
    return _base_context(
        mode="status",
        title="劳动合同审查",
        task_id=record.task_id,
        filename=record.filename,
        status_label=labels.get(record.status, "处理中"),
        message=messages.get(record.status, "正在处理。"),
        refresh_seconds=30,
    )


def failed_context(record) -> dict[str, Any]:
    return _base_context(
        mode="error",
        title="劳动合同审查",
        task_id=record.task_id,
        filename=record.filename,
        status_label="审查失败",
        message="审查过程中出现错误，未能完成本次审查。",
        error=record.error or "未知错误",
    )


def report_context(record) -> dict[str, Any]:
    result = record.result or {}
    risks = []
    for risk in result.get("risks") or []:
        risk_type = risk.get("risk_type") or ""
        severity = risk.get("severity") or "low"
        source_label, source_class = SOURCE_LABELS.get(
            risk.get("source") or "", ("未知来源", "other")
        )
        risks.append(
            {
                "type_name": RISK_TYPE_NAMES.get(risk_type, "其他风险"),
                "severity_label": SEVERITY_LABELS.get(severity, "未知"),
                "severity_class": SEVERITY_CLASSES.get(severity, "low"),
                "source_label": source_label,
                "source_class": source_class,
                "excerpt": risk.get("excerpt") or "",
                "explanation": risk.get("explanation") or "",
            }
        )
    observations = [
        {
            "excerpt": item.get("excerpt") or "",
            "note": item.get("note") or "",
        }
        for item in result.get("other_observations") or []
    ]
    raw_tokens = (result.get("meta") or {}).get("tokens") or {}
    tokens = {
        "prompt_tokens": int(raw_tokens.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(
            raw_tokens.get("completion_tokens", 0) or 0
        ),
        "total_tokens": int(raw_tokens.get("total_tokens", 0) or 0),
    }
    risk_count = len(risks)
    has_high = any(
        risk["severity_class"] == "high" for risk in risks
    )
    overall = (
        "存在高风险"
        if has_high
        else ("存在风险" if risks else "未检出风险")
    )
    overall_class = (
        "high" if has_high else ("medium" if risks else "none")
    )
    context = _base_context(
        mode="report",
        title="劳动合同审查报告",
        task_id=record.task_id,
        filename=record.filename,
        status_label="审查完成",
    )
    context.update(
        {
            "risks": risks,
            "observations": observations,
            "checks": CHECK_ITEMS,
            "risk_count": risk_count,
            "overall": overall,
            "overall_class": overall_class,
            "review_time": _format_time(
                record.finished_at or record.created_at
            ),
            "tokens": tokens,
        }
    )
    return context


def _format_time(timestamp: Optional[float]) -> str:
    if not timestamp:
        return "—"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
