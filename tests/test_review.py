from app.review_engine import run_llm_branch
from infra.llm_client import FakeLLM

CLEANED = "劳动合同\n第一条 试用期三个月。\n第二条 月工资为人民币8000元。"


def test_fake_llm_chain_keeps_valid_and_drops_fabricated():
    response = {
        "risks": [
            {
                "risk_type": "probation_exceed",
                "severity": "high",
                "excerpt": "试用期三个月",
                "explanation": "正文命中",
                "is_violation": True,
            },
            {
                "risk_type": "illegal_penalty",
                "severity": "medium",
                "excerpt": "原文没有这句",
                "explanation": "编造",
                "is_violation": True,
            },
        ],
        "other_observations": [
            {"excerpt": "月工资为人民币8000元", "note": "可保留"},
            {"excerpt": "编造观察", "note": "编造"},
        ],
    }

    result = run_llm_branch(CLEANED, {}, FakeLLM(response))

    assert [risk["risk_type"] for risk in result["risks"]] == ["probation_exceed"]
    assert [item["excerpt"] for item in result["other_observations"]] == [
        "月工资为人民币8000元"
    ]
