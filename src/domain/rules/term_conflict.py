"""合同期限前后矛盾规则。"""

from typing import Any, Optional


def judge_term_conflict(
    extract_json: dict[str, Any],
) -> Optional[dict[str, Any]]:
    term = extract_json.get("contract_term") or {}
    conflict = term.get("conflict")
    if not conflict:
        return None

    declared = term.get("value")
    alternative = conflict.get("alternative")
    declared_evidence = term.get("evidence") or ""
    alternative_evidence = conflict.get("alternative_evidence") or ""

    return {
        "source": "rules",
        "risk_type": "term_inconsistent",
        "severity": "medium",
        "excerpt": "",
        "explanation": (
            f"合同期限存在矛盾：声明为 {_fmt(declared)} 个月"
            f"（「{declared_evidence}」），"
            f"日期区间推算为 {_fmt(alternative)} 个月"
            f"（「{alternative_evidence}」）"
        ),
    }


def _fmt(value: float) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number
