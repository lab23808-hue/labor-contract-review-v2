from pathlib import Path

from app.review import run_full_review
from domain.extractor import extract_fields
from infra.llm_client import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "contracts" / "synthetic_0001.txt"
PENALTY_FIXTURE = (
    Path(__file__).parent / "fixtures" / "realistic" / "C-003.txt"
)
OPEN_ENDED_FIXTURE = (
    Path(__file__).parent / "fixtures" / "realistic" / "C-007.txt"
)
NONCOMPETE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "realistic" / "C-008.txt"
)
WAGE_FIXTURE = (
    Path(__file__).parent / "corpus_gen" / "generated" / "G-15.txt"
)

ZERO_TOKENS = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "estimated": False,
}


def _meta(llm_failed, risk_count, sources):
    return {
        "llm_failed": llm_failed,
        "risk_count": risk_count,
        "sources": sources,
        "tokens": ZERO_TOKENS,
    }


def _inputs() -> tuple[str, dict]:
    text = FIXTURE.read_text(encoding="utf-8")
    return text, extract_fields(text)


def test_full_review_rules_win_on_synthetic_0001():
    text, extract = _inputs()
    llm_response = {
        "risks": [
            {
                "risk_type": "probation_exceed",
                "severity": "medium",
                "excerpt": "双方约定试用期为三个月，劳动合同期限为两年。",
                "explanation": "LLM 版",
                "is_violation": True,
            },
            {
                "risk_type": "term_inconsistent",
                "severity": "high",
                "excerpt": "合同期限自2025年02月14日起至2028年02月14日止。",
                "explanation": "LLM 版",
                "is_violation": True,
            },
        ],
        "other_observations": [
            {"excerpt": "第2条 试用期期限超过上限", "note": "标题异常"}
        ],
    }

    report = run_full_review(text, extract, FakeLLM(llm_response))

    assert [risk["risk_type"] for risk in report["risks"]] == [
        "probation_exceed",
        "term_inconsistent",
    ]
    assert [risk["source"] for risk in report["risks"]] == ["rules", "rules"]
    assert report["meta"] == _meta(
        False, 2, {"rules": 2, "llm": 0}
    )
    assert report["other_observations"] == [
        {
            "excerpt": "第2条 试用期期限超过上限",
            "note": "标题异常",
            "source": "llm",
        }
    ]


def test_full_review_llm_failed_keeps_rules():
    text, extract = _inputs()
    llm_output = {
        "risks": [],
        "other_observations": [],
        "llm_failed": True,
        "error": "bad json",
    }

    report = run_full_review(text, extract, FakeLLM(llm_output))

    assert [risk["risk_type"] for risk in report["risks"]] == [
        "probation_exceed",
        "term_inconsistent",
    ]
    assert [risk["source"] for risk in report["risks"]] == ["rules", "rules"]
    assert report["meta"] == _meta(
        True, 2, {"rules": 2, "llm": 0}
    )


def test_c003_penalty_rule_reports_two_rule_risks():
    text = PENALTY_FIXTURE.read_text(encoding="utf-8")

    report = run_full_review(
        text,
        extract_fields(text),
        FakeLLM({"risks": [], "other_observations": []}),
    )

    penalties = [
        risk
        for risk in report["risks"]
        if risk["risk_type"] == "illegal_penalty"
    ]

    assert len(penalties) == 2
    assert [risk["source"] for risk in penalties] == ["rules", "rules"]
    assert [risk["excerpt"] for risk in penalties] == [
        "乙方在合同期内提前离职的，应向甲方支付违约金人民币20000元",
        "乙方离职后未提前三十日书面通知甲方的，另行支付违约金5000元",
    ]
    assert report["meta"] == _meta(
        False, 2, {"rules": 2, "llm": 0}
    )


def test_c007_open_ended_denied_llm_risk_is_kept():
    text = OPEN_ENDED_FIXTURE.read_text(encoding="utf-8")
    llm_response = {
        "risks": [
            {
                "risk_type": "open_ended_denied",
                "severity": "medium",
                "excerpt": (
                    "本合同期满后如续签，仍为固定期限劳动合同，"
                    "乙方不得要求签订无固定期限劳动合同。"
                ),
                "explanation": "排除劳动者签订无固定期限合同的权利",
                "is_violation": True,
            }
        ],
        "other_observations": [],
    }

    report = run_full_review(
        text, extract_fields(text), FakeLLM(llm_response)
    )

    assert [risk["risk_type"] for risk in report["risks"]] == [
        "open_ended_denied"
    ]
    assert report["risks"][0]["source"] == "llm"
    assert report["meta"] == _meta(
        False, 1, {"rules": 0, "llm": 1}
    )


def test_c008_noncompete_rules_and_llm_are_merged():
    text = NONCOMPETE_FIXTURE.read_text(encoding="utf-8")
    llm_response = {
        "risks": [
            {
                "risk_type": "noncompete_exceed",
                "severity": "medium",
                "excerpt": "乙方离职后三年内不得入职与甲方有竞争关系的单位",
                "explanation": "竞业限制期限三年，超过两年上限",
                "is_violation": True,
            }
        ],
        "other_observations": [],
    }

    report = run_full_review(
        text, extract_fields(text), FakeLLM(llm_response)
    )

    assert [risk["risk_type"] for risk in report["risks"]] == [
        "noncompete_no_pay",
        "noncompete_exceed",
    ]
    assert [risk["source"] for risk in report["risks"]] == ["rules", "llm"]
    assert report["risks"][0]["excerpt"] == (
        "乙方离职后三年内不得入职与甲方有竞争关系的单位，"
        "竞业限制期间甲方无需支付任何补偿"
    )
    assert report["meta"] == _meta(
        False, 2, {"rules": 1, "llm": 1}
    )


def test_wage_rule_wins_over_llm_dual_source():
    text = WAGE_FIXTURE.read_text(encoding="utf-8")
    llm_response = {
        "risks": [
            {
                "risk_type": "wage_below_floor",
                "severity": "high",
                "excerpt": "试用期月工资为9000元",
                "explanation": "LLM 版",
                "is_violation": True,
            }
        ],
        "other_observations": [],
    }

    report = run_full_review(
        text, extract_fields(text), FakeLLM(llm_response)
    )

    wages = [
        risk
        for risk in report["risks"]
        if risk["risk_type"] == "wage_below_floor"
    ]
    assert len(wages) == 1
    assert wages[0]["source"] == "rules"
    assert report["meta"] == _meta(
        False, 1, {"rules": 1, "llm": 0}
    )
