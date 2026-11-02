"""社会保险条款缺失规则。"""

from typing import Any, Optional

_KEYWORDS = (
    "社会保险",
    "社保",
    "五险",
    "养老保险",
    "医疗保险",
    "失业保险",
    "工伤保险",
    "生育保险",
    "住房公积金",
)


def judge_social_security(cleaned_text: str) -> Optional[dict[str, Any]]:
    if any(keyword in cleaned_text for keyword in _KEYWORDS):
        return None

    return {
        "source": "rules",
        "risk_type": "social_security_missing",
        "severity": "high",
        "excerpt": "",
        "explanation": "合同全文未见任何社会保险相关条款",
    }
