import logging

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
            }
        ],
        "other_observations": [],
    }

    result = verify_llm_output(output, CLEANED)

    assert result["risks"] == output["risks"]
    assert result["other_observations"] == []


def test_drops_tampered_excerpt_and_logs(caplog):
    tampered = "试用期三个 月"
    output = {
        "risks": [
            {
                "risk_type": "probation_exceed",
                "severity": "high",
                "excerpt": tampered,
                "explanation": "编造",
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
