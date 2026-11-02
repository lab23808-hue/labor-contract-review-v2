import re
from datetime import date, timedelta
from typing import Any, Callable, Optional

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
}
_CN_NUMBER_CHARS = "一二三四五六七八九十两"
_NUM = rf"(?:[{_CN_NUMBER_CHARS}]+|\d+(?:\.\d+)?)"

_FULLWIDTH = str.maketrans(
    {
        **{chr(0xFF10 + i): str(i) for i in range(10)},
        **{chr(0xFF21 + i): chr(ord("A") + i) for i in range(26)},
        **{chr(0xFF41 + i): chr(ord("a") + i) for i in range(26)},
        "：": ":",
        "，": ",",
        "（": "(",
        "）": ")",
        "％": "%",
        "／": "/",
    }
)

_PROBATION_EXPLICIT_MONTHS = re.compile(
    rf"试用期\s*(?:为|是|:)?\s*({_NUM})\s*个?月"
)
_PROBATION_EXPLICIT_DAYS = re.compile(rf"试用期\s*(?:为|是|:)?\s*({_NUM})\s*天")
_PROBATION_MONTHS = re.compile(rf"试用期\s*(?:不超过)?\s*({_NUM})\s*个?月")
_PROBATION_DAYS = re.compile(rf"试用期\s*(?:不超过)?\s*({_NUM})\s*天")

_TERM_EXPLICIT_YEARS = re.compile(rf"(?:合同)?期限\s*(?:为|是|:)?\s*({_NUM})\s*年")
_TERM_EXPLICIT_MONTHS = re.compile(
    rf"(?:合同)?期限\s*(?:为|是|:)?\s*({_NUM})\s*(?:个)?月"
)
_TERM_DATE_RANGE = re.compile(
    r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*起"
    r"\s*至\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*止"
)

_SALARY_LABEL = r"(?:月基本工资|月工资|基本工资|工资标准|月薪|薪资|工资)"
_SALARY_EXPLICIT = re.compile(
    rf"{_SALARY_LABEL}\s*(?:为|是|:)?\s*(?:人民币|RMB|￥|¥)?\s*"
    r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*元"
)
_SALARY = re.compile(
    rf"{_SALARY_LABEL}\s*(?:人民币|RMB|￥|¥)?\s*"
    r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*元"
)
_CN_AMOUNT_CHARS = "零壹贰叁肆伍陆柒捌玖拾佰仟万"
_CN_AMOUNT_DIGITS = {
    "零": 0,
    "壹": 1,
    "贰": 2,
    "叁": 3,
    "肆": 4,
    "伍": 5,
    "陆": 6,
    "柒": 7,
    "捌": 8,
    "玖": 9,
    **{digit: value for digit, value in _CN_DIGITS.items() if digit != "两"},
    "两": 2,
}
_CN_AMOUNT_UNITS = {"拾": 10, "佰": 100, "仟": 1000}
_SALARY_CN_EXPLICIT = re.compile(
    rf"{_SALARY_LABEL}\s*(?:为|是|:)?\s*(?:人民币|RMB|￥|¥)?\s*"
    rf"([{_CN_AMOUNT_CHARS}]+)\s*元(?:整)?"
)
_SALARY_CN = re.compile(
    rf"(?:{_SALARY_LABEL}|人民币|RMB)\s*(?:为|是|:)?\s*"
    rf"([{_CN_AMOUNT_CHARS}]+)\s*元(?:整)?"
)

_PENALTY_KEYWORDS = ("违约金", "罚款", "扣罚", "罚金")
_SOCIAL_KEYWORDS = ("社会保险", "社保", "五险")

_NOT_FOUND = {"value": None, "method": "not_found", "evidence": None}


def extract_fields(text: str) -> dict[str, dict[str, Any]]:
    normalized = text.translate(_FULLWIDTH)
    return {
        "contract_term": _contract_term(text, normalized),
        "probation_months": _pick(
            text,
            normalized,
            (
                _explicit_probation_months,
                _explicit_probation_days,
                _probation_months,
                _probation_days,
            ),
        ),
        "salary": _pick(
            text,
            normalized,
            (_explicit_salary, _explicit_salary_cn, _salary, _salary_cn),
        ),
        "has_penalty_clause": _keyword_field(text, normalized, _PENALTY_KEYWORDS),
        "has_social_security": _keyword_field(text, normalized, _SOCIAL_KEYWORDS),
    }


def _pick(
    text: str,
    normalized: str,
    extractors: tuple[Callable[[str, str], Optional[dict[str, Any]]], ...],
) -> dict[str, Any]:
    for extractor in extractors:
        result = extractor(text, normalized)
        if result is not None:
            return result
    return dict(_NOT_FOUND)


def _contract_term(text: str, normalized: str) -> dict[str, Any]:
    declared = _explicit_term_years(text, normalized) or _explicit_term_months(
        text, normalized
    )
    inferred = _inferred_term_dates(text, normalized)

    if declared is None and inferred is None:
        return {**_NOT_FOUND, "conflict": None}
    if declared is None:
        return {**inferred, "conflict": None}
    if inferred is None or inferred["value"] == declared["value"]:
        return {**declared, "conflict": None}
    return {
        **declared,
        "conflict": {
            "alternative": inferred["value"],
            "alternative_method": inferred["method"],
            "alternative_evidence": inferred["evidence"],
        },
    }


def _hit(text: str, match: re.Match[str], value: Any, method: str) -> dict[str, Any]:
    return {
        "value": value,
        "method": method,
        "evidence": text[match.start() : match.end()],
    }


def _explicit_term_years(text: str, normalized: str) -> Optional[dict[str, Any]]:
    match = _TERM_EXPLICIT_YEARS.search(normalized)
    if match is None:
        return None
    return _hit(text, match, _tidy(_num(match.group(1)) * 12), "explicit")


def _explicit_term_months(text: str, normalized: str) -> Optional[dict[str, Any]]:
    match = _TERM_EXPLICIT_MONTHS.search(normalized)
    if match is None:
        return None
    return _hit(text, match, _tidy(_num(match.group(1))), "explicit")


def _inferred_term_dates(text: str, normalized: str) -> Optional[dict[str, Any]]:
    match = _TERM_DATE_RANGE.search(normalized)
    if match is None:
        return None
    start_year, start_month, start_day = (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
    )
    end_year, end_month, end_day = (
        int(match.group(4)),
        int(match.group(5)),
        int(match.group(6)),
    )
    adjusted_end = date(end_year, end_month, end_day) + timedelta(days=1)
    months = (adjusted_end.year - start_year) * 12 + (
        adjusted_end.month - start_month
    )
    if adjusted_end.day < start_day:
        months -= 1
    return _hit(text, match, months, "inferred")


def _explicit_probation_months(
    text: str, normalized: str
) -> Optional[dict[str, Any]]:
    match = _PROBATION_EXPLICIT_MONTHS.search(normalized)
    if match is None:
        return None
    return _hit(text, match, _tidy(_num(match.group(1))), "explicit")


def _explicit_probation_days(text: str, normalized: str) -> Optional[dict[str, Any]]:
    match = _PROBATION_EXPLICIT_DAYS.search(normalized)
    if match is None:
        return None
    return _hit(text, match, _tidy(_num(match.group(1)) / 30), "explicit")


def _probation_months(text: str, normalized: str) -> Optional[dict[str, Any]]:
    match = _PROBATION_MONTHS.search(normalized)
    if match is None:
        return None
    return _hit(text, match, _tidy(_num(match.group(1))), "inferred")


def _probation_days(text: str, normalized: str) -> Optional[dict[str, Any]]:
    match = _PROBATION_DAYS.search(normalized)
    if match is None:
        return None
    return _hit(text, match, _tidy(_num(match.group(1)) / 30), "inferred")


def _explicit_salary(text: str, normalized: str) -> Optional[dict[str, Any]]:
    match = _SALARY_EXPLICIT.search(normalized)
    if match is None:
        return None
    return _hit(text, match, _tidy(float(match.group(1).replace(",", ""))), "explicit")


def _explicit_salary_cn(text: str, normalized: str) -> Optional[dict[str, Any]]:
    match = _SALARY_CN_EXPLICIT.search(normalized)
    if match is None:
        return None
    return _hit(text, match, _tidy(_cn_amount(match.group(1))), "explicit")


def _salary(text: str, normalized: str) -> Optional[dict[str, Any]]:
    match = _SALARY.search(normalized)
    if match is None:
        return None
    return _hit(text, match, _tidy(float(match.group(1).replace(",", ""))), "inferred")


def _salary_cn(text: str, normalized: str) -> Optional[dict[str, Any]]:
    match = _SALARY_CN.search(normalized)
    if match is None:
        return None
    return _hit(text, match, _tidy(_cn_amount(match.group(1))), "inferred")


def _keyword_field(
    text: str, normalized: str, keywords: tuple[str, ...]
) -> dict[str, Any]:
    best_index = -1
    best_keyword = ""
    for keyword in keywords:
        index = normalized.find(keyword)
        if index != -1 and (best_index == -1 or index < best_index):
            best_index = index
            best_keyword = keyword
    if best_index == -1:
        return dict(_NOT_FOUND)
    return {
        "value": True,
        "method": "keyword",
        "evidence": text[best_index : best_index + len(best_keyword)],
    }


def _num(token: str) -> float:
    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        return float(token)
    return float(_cn_number(token))


def _cn_number(token: str) -> int:
    if token in _CN_DIGITS:
        return _CN_DIGITS[token]
    if "十" in token:
        left, _, right = token.partition("十")
        tens = _CN_DIGITS[left] if left else 1
        ones = _CN_DIGITS[right] if right else 0
        return tens * 10 + ones
    raise ValueError(f"无法识别中文数字：{token}")


def _cn_amount(token: str) -> int:
    total = 0
    section = 0
    number = 0
    for char in token:
        if char in _CN_AMOUNT_DIGITS:
            number = _CN_AMOUNT_DIGITS[char]
        elif char in _CN_AMOUNT_UNITS:
            section += (number or 1) * _CN_AMOUNT_UNITS[char]
            number = 0
        elif char == "万":
            section = (section + number) * 10000
            total += section
            section = 0
            number = 0
        else:
            raise ValueError(f"无法识别金额：{token}")
    return total + section + number


def _tidy(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value
