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


def _drop_records(caplog):
    return [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "verifier.drop"
    ]


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
    drops = _drop_records(caplog)
    assert len(drops) == 2
    risk_drop = next(record for record in drops if record.kind == "risk")
    assert risk_drop.levelno == logging.WARNING
    assert risk_drop.reason == "excerpt 不命中"
    assert risk_drop.risk_type == "probation_exceed"
    assert risk_drop.excerpt == tampered
    observation_drop = next(
        record for record in drops if record.kind == "observation"
    )
    assert observation_drop.risk_type is None


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
    drops = _drop_records(caplog)
    assert len(drops) == 1
    assert drops[0].reason == "excerpt 不命中"
    assert drops[0].risk_type == "probation_exceed"
    assert drops[0].excerpt == tampered


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
    drops = _drop_records(caplog)
    assert len(drops) == 1
    assert drops[0].levelno == logging.WARNING
    assert drops[0].reason == "is_violation=false"
    assert drops[0].risk_type == "probation_exceed"
    assert drops[0].entry["explanation"] == "边界情形，经计算不构成违规"


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
    drops = _drop_records(caplog)
    assert len(drops) == 1
    assert drops[0].reason == "字段缺失"
    assert drops[0].risk_type == "probation_exceed"
    assert drops[0].entry["explanation"] == "缺少裁决字段"
