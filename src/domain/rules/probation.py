"""试用期超限规则。

contract_term 带 conflict 时按从严偏向处理：对所有期限候选分别计算法定上限，
取其中最小的上限（等价于采用较短期限），任一候选超限即报告。
"""

from typing import Any, Optional


def judge_probation(
    extract_json: dict[str, Any],
) -> Optional[dict[str, Any]]:
    probation = extract_json.get("probation_months") or {}
    term = extract_json.get("contract_term") or {}

    months = probation.get("value")
    term_months = term.get("value")
    if months is None or term_months is None:
        return None

    candidates = [term_months]
    conflict = term.get("conflict")
    if conflict and conflict.get("alternative") is not None:
        candidates.append(conflict["alternative"])

    limit = min(_legal_limit(value) for value in candidates)
    if months <= limit:
        return None

    return {
        "source": "rules",
        "risk_type": "probation_exceed",
        "severity": "high",
        "excerpt": probation.get("evidence") or "",
        "explanation": (
            f"试用期约定 {_fmt(months)} 个月，"
            f"超过该合同期限对应的法定上限 {limit} 个月"
        ),
    }


def _legal_limit(term_months: float) -> int:
    if term_months < 12:
        return 1
    if term_months < 36:
        return 2
    return 6


def _fmt(value: float) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number
