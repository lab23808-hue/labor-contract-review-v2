"""试用期工资低于约定工资 80% 规则。

probation_salary 为抽取 JSON 的可选扩展字段；当前五字段抽取器尚未产出，
缺失时本规则返回 None。「低于当地最低工资标准」不在本规则范围内。
"""

from typing import Any, Optional


def judge_wage(extract_json: dict[str, Any]) -> Optional[dict[str, Any]]:
    salary = (extract_json.get("salary") or {}).get("value")
    probation_salary = (extract_json.get("probation_salary") or {}).get("value")
    if salary is None or probation_salary is None:
        return None

    floor = salary * 0.8
    if probation_salary >= floor:
        return None

    return {
        "source": "rules",
        "risk_type": "wage_below_floor",
        "severity": "medium",
        "excerpt": (extract_json.get("probation_salary") or {}).get("evidence")
        or "",
        "explanation": (
            f"试用期工资 {_fmt(probation_salary)} 低于约定工资 {_fmt(salary)}"
            f" 的 80% ({_fmt(floor)})"
        ),
    }


def _fmt(value: float) -> int | float:
    number = float(value)
    if number.is_integer():
        return int(number)
    return round(number, 2)
