from pathlib import Path

import pytest

from domain.extractor import extract_fields

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"

NOT_FOUND = {"value": None, "method": "not_found", "evidence": None}
NOT_FOUND_TERM = {**NOT_FOUND, "conflict": None}

CASES = [
    (
        "real_contract",
        {
            "contract_term": {
                "value": 48,
                "method": "inferred",
                "evidence": "自2026年1月5日起至2030年1月4日止",
                "conflict": None,
            },
            "probation_months": {
                "value": 3,
                "method": "explicit",
                "evidence": "试用期三个月",
            },
            "salary": {
                "value": 20000,
                "method": "explicit",
                "evidence": "月基本工资为人民币20000元",
            },
            "has_penalty_clause": NOT_FOUND,
            "has_social_security": {
                "value": True,
                "method": "keyword",
                "evidence": "社会保险",
            },
        },
    ),
    (
        "synthetic_0001",
        {
            "contract_term": {
                "value": 24,
                "method": "explicit",
                "evidence": "合同期限为两年",
                "conflict": {
                    "alternative": 36,
                    "alternative_method": "inferred",
                    "alternative_evidence": "自2025年02月14日起至2028年02月14日止",
                },
            },
            "probation_months": {
                "value": 3,
                "method": "explicit",
                "evidence": "试用期为三个月",
            },
            "salary": {
                "value": 9600,
                "method": "explicit",
                "evidence": "月工资为人民币9600元",
            },
            "has_penalty_clause": NOT_FOUND,
            "has_social_security": {
                "value": True,
                "method": "keyword",
                "evidence": "社会保险",
            },
        },
    ),
    (
        "synthetic_0002",
        {
            "contract_term": {
                "value": 36,
                "method": "inferred",
                "evidence": "自2024年04月24日起至2027年04月24日止",
                "conflict": None,
            },
            "probation_months": NOT_FOUND,
            "salary": {
                "value": 9600,
                "method": "explicit",
                "evidence": "月工资为人民币9600元",
            },
            "has_penalty_clause": {
                "value": True,
                "method": "keyword",
                "evidence": "罚款",
            },
            "has_social_security": {
                "value": True,
                "method": "keyword",
                "evidence": "社会保险",
            },
        },
    ),
]


@pytest.mark.parametrize("name,expected", CASES)
def test_extracts_expected_fields_from_fixture(name, expected):
    text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")

    result = extract_fields(text)

    assert result == expected
    for field in result.values():
        if field["evidence"] is not None:
            assert field["evidence"] in text


def test_probation_in_days_converts_to_months():
    result = extract_fields("劳动合同\n试用期为90天。\n")

    assert result["probation_months"] == {
        "value": 3,
        "method": "explicit",
        "evidence": "试用期为90天",
    }


def test_missing_fields_are_not_found():
    result = extract_fields("劳动合同\n甲方：某公司\n乙方：张三\n")

    assert result["contract_term"] == NOT_FOUND_TERM
    for name in (
        "probation_months",
        "salary",
        "has_penalty_clause",
        "has_social_security",
    ):
        assert result[name] == NOT_FOUND


def test_contract_term_conflict_is_explicit():
    text = (
        "劳动合同\n"
        "本合同期限为两年。\n"
        "双方约定合同期限自2024年04月24日起至2027年04月24日止。\n"
    )

    term = extract_fields(text)["contract_term"]

    assert term["value"] == 24
    assert term["method"] == "explicit"
    assert term["evidence"] == "合同期限为两年"
    assert term["conflict"] == {
        "alternative": 36,
        "alternative_method": "inferred",
        "alternative_evidence": "自2024年04月24日起至2027年04月24日止",
    }
    assert term["evidence"] in text
    assert term["conflict"]["alternative_evidence"] in text


def test_real_contract_has_no_term_conflict():
    text = (FIXTURES / "real_contract.txt").read_text(encoding="utf-8")

    term = extract_fields(text)["contract_term"]

    assert term["value"] == 48
    assert term["method"] == "inferred"
    assert term["conflict"] is None


def test_chinese_numerals_are_converted():
    result = extract_fields("劳动合同\n试用期为十二个月。\n")

    assert result["probation_months"]["value"] == 12
    assert result["probation_months"]["method"] == "explicit"
    assert result["probation_months"]["evidence"] == "试用期为十二个月"


def test_mixed_case_amount_prefers_arabic():
    text = "劳动合同\n月工资为人民币8000元（大写：捌仟元整）。\n"

    salary = extract_fields(text)["salary"]

    assert salary == {
        "value": 8000,
        "method": "explicit",
        "evidence": "月工资为人民币8000元",
    }


def test_uppercase_amount_is_converted():
    text = "劳动合同\n月工资为人民币捌仟元整。\n"

    salary = extract_fields(text)["salary"]

    assert salary == {
        "value": 8000,
        "method": "explicit",
        "evidence": "月工资为人民币捌仟元整",
    }


def test_fullwidth_digits_are_normalized_but_evidence_is_verbatim():
    text = "劳动合同\n月工资为人民币８０００元。\n"

    salary = extract_fields(text)["salary"]

    assert salary == {
        "value": 8000,
        "method": "explicit",
        "evidence": "月工资为人民币８０００元",
    }
    assert salary["evidence"] in text
