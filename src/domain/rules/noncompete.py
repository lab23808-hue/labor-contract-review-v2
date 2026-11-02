"""竞业限制经济补偿缺失规则。

先取包含竞业限制关键词的最长子句作为竞业限制条款，只在该条款内做补偿
关键词与否定回看；条款外的经济补偿提及不计入。条款内每个补偿关键词命中
向前回看最多 4 个字符，若窗口内出现「无」「没有」「不」「放弃」「无需」
「免于」之一，该命中视为否定式提及。条款内所有命中均被否定或无命中时，
报一条 noncompete_no_pay。

已知边界：条款间引用（如「补偿标准见第X条」）不会跟进；如果标题本身含
「竞业限制补偿」且被选为条款，仍会被视为已约定补偿。切句边界沿用
penalty：句号、分号、换行、冒号，标题段独立。
"""

import re
from typing import Any

_CLAUSE_BOUNDARY = re.compile(r"[。；;：:\n]")
_NONCOMPETE_OCCURRENCE = re.compile(r"竞\s*业\s*限\s*制|竞\s*业\s*禁\s*止")
_WHITESPACE = re.compile(r"\s+")
_COMPENSATION_TERMS = ("经济补偿", "竞业限制补偿", "按月补偿")
_NEGATION_TERMS = ("无", "没有", "不", "放弃", "无需", "免于")
_NEGATION_LOOKBACK = 4

_EXPLANATION = (
    "竞业限制条款未约定经济补偿。依据《劳动合同法》第二十三条，"
    "竞业限制期限内用人单位须按月给付经济补偿；"
    "未约定补偿的条款损害劳动者权益。"
)


def judge_noncompete(cleaned_text: str) -> list[dict[str, Any]]:
    if not _NONCOMPETE_OCCURRENCE.search(cleaned_text):
        return []

    clause = _longest_clause(cleaned_text)
    if _has_compensation(clause):
        return []

    return [
        {
            "source": "rules",
            "risk_type": "noncompete_no_pay",
            "severity": "high",
            "excerpt": clause,
            "explanation": _EXPLANATION,
        }
    ]


def _has_compensation(normalized: str) -> bool:
    for term in _COMPENSATION_TERMS:
        start = 0
        while True:
            index = normalized.find(term, start)
            if index < 0:
                break
            window = normalized[
                max(0, index - _NEGATION_LOOKBACK):index
            ]
            if not any(
                negation in window for negation in _NEGATION_TERMS
            ):
                return True
            start = index + len(term)
    return False


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
