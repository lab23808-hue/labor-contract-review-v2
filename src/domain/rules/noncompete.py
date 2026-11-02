"""竞业限制经济补偿缺失规则。

全文出现竞业限制/竞业禁止，且全文不含任何补偿关键词时，报一条
noncompete_no_pay。补偿判定是封闭的全文关键词检查：标题中的
「竞业限制补偿」也会被视为已约定补偿，这是该启发式的已知边界。

excerpt 取包含竞业限制关键词的最长子句，避免只取到「第六条 竞业限制」
这类独立标题。切句边界沿用 penalty：句号、分号、换行、冒号，标题段独立。
"""

import re
from typing import Any

_CLAUSE_BOUNDARY = re.compile(r"[。；;：:\n]")
_NONCOMPETE_OCCURRENCE = re.compile(r"竞\s*业\s*限\s*制|竞\s*业\s*禁\s*止")
_WHITESPACE = re.compile(r"\s+")
_COMPENSATION_TERMS = ("经济补偿", "竞业限制补偿", "按月补偿")

_EXPLANATION = (
    "竞业限制条款未约定经济补偿。依据《劳动合同法》第二十三条，"
    "竞业限制期限内用人单位须按月给付经济补偿；"
    "未约定补偿的条款损害劳动者权益。"
)


def judge_noncompete(cleaned_text: str) -> list[dict[str, Any]]:
    if not _NONCOMPETE_OCCURRENCE.search(cleaned_text):
        return []

    normalized = _WHITESPACE.sub("", cleaned_text)
    if any(term in normalized for term in _COMPENSATION_TERMS):
        return []

    return [
        {
            "source": "rules",
            "risk_type": "noncompete_no_pay",
            "severity": "high",
            "excerpt": _longest_clause(cleaned_text),
            "explanation": _EXPLANATION,
        }
    ]


def _longest_clause(text: str) -> str:
    longest = ""
    for occurrence in _NONCOMPETE_OCCURRENCE.finditer(text):
        start = _clause_start(text, occurrence.start())
        boundary = _CLAUSE_BOUNDARY.search(text, occurrence.end())
        end = boundary.start() if boundary else len(text)
        clause = _WHITESPACE.sub("", text[start:end])
        if len(clause) > len(longest):
            longest = clause
    return longest


def _clause_start(text: str, position: int) -> int:
    start = 0
    for boundary in _CLAUSE_BOUNDARY.finditer(text, 0, position):
        start = boundary.end()
    return start
