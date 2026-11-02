"""试用期工资低于转正工资 80% 规则。

从清洗后的合同文本提取试用期工资与转正/月工资，两者齐备且
试用期工资 < 转正工资 × 80% 时报告。缺任一数字不报，恰好 80% 不报。
支持阿拉伯数字与汉字金额，匹配前移除全部空白（OCR 归一化）。
"""

import re
from typing import Any, Optional

_WHITESPACE = re.compile(r"\s+")
_CN_CHARS = "零一二三四五六七八九两十百千万壹贰叁肆伍陆柒捌玖拾佰仟"
_AMOUNT = rf"(?:\d[\d,]*(?:\.\d+)?|[{_CN_CHARS}]+)"
_CURRENCY = r"(?:人民币|RMB|￥|¥)?"

_PROBATION = re.compile(
    rf"试用期(?:期间|间|内)?(?:的)?(?:月工资|工资)"
    rf"\s*(?:为|是|:)?\s*{_CURRENCY}\s*({_AMOUNT})\s*元(?:整)?"
)
_REGULAR = re.compile(
    rf"(转正(?:后)?(?:的)?(?:月)?工资|月工资|月薪)"
    rf"\s*(?:为|是|:)?\s*{_CURRENCY}\s*({_AMOUNT})\s*元(?:整)?"
)

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "壹": 1,
    "贰": 2,
    "叁": 3,
    "肆": 4,
    "伍": 5,
    "陆": 6,
    "柒": 7,
    "捌": 8,
    "玖": 9,
}
_CN_UNITS = {
    "十": 10,
    "拾": 10,
    "百": 100,
    "佰": 100,
    "千": 1000,
    "仟": 1000,
}


def judge_wage(cleaned_text: str) -> Optional[dict[str, Any]]:
    normalized = _WHITESPACE.sub("", cleaned_text)
    probation_match = _PROBATION.search(normalized)
    if probation_match is None:
        return None

    regular_match = _find_regular(normalized, probation_match.span())
    if regular_match is None:
        return None

    probation = _parse_amount(probation_match.group(1))
    regular = _parse_amount(regular_match.group(2))
    if probation >= regular * 0.8:
        return None

    return {
        "source": "rules",
        "risk_type": "wage_below_floor",
        "severity": "medium",
        "excerpt": probation_match.group(0),
        "explanation": (
            f"试用期工资 {_fmt(probation)} 低于转正工资 {_fmt(regular)} 的 80%"
            f"（{_fmt(probation)}/{_fmt(regular)}="
            f"{probation / regular:.0%} < 80%）"
        ),
    }


def _find_regular(normalized: str, probation_span: tuple[int, int]):
    candidates = list(_REGULAR.finditer(normalized))
    explicit = [
        match
        for match in candidates
        if match.group(1).startswith("转正")
    ]
    for match in explicit + candidates:
        if not _overlaps(match.span(), probation_span):
            return match
    return None


def _overlaps(
    first: tuple[int, int], second: tuple[int, int]
) -> bool:
    return first[0] < second[1] and second[0] < first[1]


def _parse_amount(token: str) -> float:
    if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", token):
        return float(token.replace(",", ""))
    return float(_cn_amount(token))


def _cn_amount(token: str) -> int:
    total = 0
    section = 0
    number = 0
    for char in token:
        if char in _CN_DIGITS:
            number = _CN_DIGITS[char]
        elif char in _CN_UNITS:
            section += (number or 1) * _CN_UNITS[char]
            number = 0
        elif char == "万":
            section = (section + number) * 10000
            total += section
            section = 0
            number = 0
        else:
            raise ValueError(f"无法识别金额：{token}")
    return total + section + number


def _fmt(value: float) -> int | float:
    number = float(value)
    if number.is_integer():
        return int(number)
    return round(number, 2)
