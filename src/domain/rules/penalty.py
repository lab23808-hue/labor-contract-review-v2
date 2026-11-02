"""劳动合同违约金条款确定性规则。

逐子句定位「违约金」，命中合法情形关键词的跳过，其余按条报告。

实现先定位「违约金」（允许字符间出现空白，以修复 OCR 断词），再向两侧
回溯到最近的句号/分号/换行/冒号，取该子句并移除空白作为 excerpt。冒号
同时作为边界，是为了不让条文标题中的「服务期」等词污染正文判断。

severity 固定为 medium：关键词启发式无法完全排除「措辞特殊但实为
合法服务期条款」的可能，按保护性偏向报告但降一档置信度。
"""

import re
from typing import Any

_CLAUSE_BOUNDARY = re.compile(r"[。；;：:\n]")
_PENALTY_OCCURRENCE = re.compile(r"违\s*约\s*金")
_WHITESPACE = re.compile(r"\s+")
_LEGAL_TERMS = re.compile(r"服务期|培训|竞业限制|竞业禁止|保密")

_EXPLANATION = (
    "本条约定由劳动者承担违约金。依据《劳动合同法》第二十五条，"
    "除服务期（第二十二条）和竞业限制（第二十三条）情形外，"
    "用人单位不得约定由劳动者承担违约金；若本条不属于上述两种情形，"
    "则该约定违法。"
)


def judge_penalty(cleaned_text: str) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    seen_clauses: list[tuple[int, int]] = []

    for occurrence in _PENALTY_OCCURRENCE.finditer(cleaned_text):
        start = _clause_start(cleaned_text, occurrence.start())
        boundary = _CLAUSE_BOUNDARY.search(cleaned_text, occurrence.end())
        end = boundary.start() if boundary else len(cleaned_text)

        if any(existing_start <= occurrence.start() < existing_end
               for existing_start, existing_end in seen_clauses):
            continue
        seen_clauses.append((start, end))

        clause = _WHITESPACE.sub("", cleaned_text[start:end])
        if not clause or _LEGAL_TERMS.search(clause):
            continue
        risks.append(
            {
                "source": "rules",
                "risk_type": "illegal_penalty",
                "severity": "medium",
                "excerpt": clause,
                "explanation": _EXPLANATION,
            }
        )
    return risks


def _clause_start(text: str, position: int) -> int:
    start = 0
    for boundary in _CLAUSE_BOUNDARY.finditer(text, 0, position):
        start = boundary.end()
    return start
