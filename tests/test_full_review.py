from pathlib import Path

from app.review import run_full_review
from domain.extractor import extract_fields
from infra.llm_client import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "contracts" / "synthetic_0001.txt"


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
    assert report["meta"] == {
        "llm_failed": False,
        "risk_count": 2,
        "sources": {"rules": 2, "llm": 0},
    }
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
    assert report["meta"] == {
        "llm_failed": True,
        "risk_count": 2,
        "sources": {"rules": 2, "llm": 0},
    }
