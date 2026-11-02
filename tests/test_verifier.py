import logging

import pytest

from domain.verifier import verify_llm_output

CLEANED = "劳动合同\n第一条 试用期三个月。\n第二条 月工资为人民币8000元。"


def test_keeps_exact_excerpt():
    output = {
        "risks": [
            {
                "risk_type": "probation_exceed",
                "severity": "high",
                "excerpt": "试用期三个月",
                "explanation": "超过上限",
                "is_violation": True,
            }
        ],
        "other_observations": [],
    }

    result = verify_llm_output(output, CLEANED)

    assert result["risks"] == output["risks"]
    assert result["other_observations"] == []


def test_drops_tampered_excerpt_and_logs(caplog):
    tampered = "试用期二个月"
    output = {
        "risks": [
            {
                "risk_type": "probation_exceed",
                "severity": "high",
                "excerpt": tampered,
                "explanation": "编造",
                "is_violation": True,
            }
        ],
        "other_observations": [
            {"excerpt": "月工资为人民币8000元", "note": "可保留"},
            {"excerpt": "原文没有这句", "note": "编造"},
        ],
    }

    with caplog.at_level(logging.WARNING):
        result = verify_llm_output(output, CLEANED)

    assert result["risks"] == []
    assert [item["note"] for item in result["other_observations"]] == ["可保留"]
    assert "丢弃 LLM risk" in caplog.text
    assert tampered in caplog.text
    assert "丢弃 LLM observation" in caplog.text


@pytest.mark.parametrize(
    "excerpt", ["试用期间月工资为3200元", "试用期间月工资为　3200元"]
)
def test_keeps_excerpt_when_only_whitespace_differs(excerpt):
    cleaned = "劳动合同\n乙方月工资为6000元，试用期间月工资为\n3200元。"
    output = {
        "risks": [
            {
                "risk_type": "wage_below_floor",
                "severity": "medium",
                "excerpt": excerpt,
                "explanation": "低于 80%",
                "is_violation": True,
            }
        ],
        "other_observations": [],
    }

    result = verify_llm_output(output, cleaned)

    assert result["risks"] == output["risks"]


@pytest.mark.parametrize("tampered", ["试用期个月", "试用期三个月整"])
def test_drops_excerpt_with_deleted_or_extra_chars(tampered, caplog):
    output = {
        "risks": [
            {
                "risk_type": "probation_exceed",
                "severity": "high",
                "excerpt": tampered,
                "explanation": "编造",
                "is_violation": True,
            }
        ],
        "other_observations": [],
    }

    with caplog.at_level(logging.WARNING):
        result = verify_llm_output(output, CLEANED)

    assert result["risks"] == []
    assert tampered in caplog.text


def test_drops_risk_when_is_violation_is_false(caplog):
    entry = {
        "risk_type": "probation_exceed",
        "severity": "high",
        "excerpt": "试用期三个月",
        "explanation": "边界情形，经计算不构成违规",
        "is_violation": False,
    }
    output = {"risks": [entry], "other_observations": []}

    with caplog.at_level(logging.WARNING):
        result = verify_llm_output(output, CLEANED)

    assert result["risks"] == []
    assert "is_violation 不为 true" in caplog.text
    assert '"is_violation": false' in caplog.text
    assert "边界情形，经计算不构成违规" in caplog.text


def test_drops_risk_when_is_violation_is_missing(caplog):
    output = {
        "risks": [
            {
                "risk_type": "probation_exceed",
                "severity": "high",
                "excerpt": "试用期三个月",
                "explanation": "缺少裁决字段",
            }
        ],
        "other_observations": [],
    }

    with caplog.at_level(logging.WARNING):
        result = verify_llm_output(output, CLEANED)

    assert result["risks"] == []
    assert "is_violation 不为 true" in caplog.text
    assert "缺少裁决字段" in caplog.text
