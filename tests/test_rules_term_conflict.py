from pathlib import Path

from domain.extractor import extract_fields
from domain.rules.term_conflict import judge_term_conflict

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"


def _extract(name: str) -> dict:
    text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    return extract_fields(text)


def test_conflict_reported_from_synthetic_0001():
    risk = judge_term_conflict(_extract("synthetic_0001"))

    assert risk == {
        "source": "rules",
        "risk_type": "term_inconsistent",
        "severity": "medium",
        "excerpt": "",
        "explanation": (
            "合同期限存在矛盾：声明为 24 个月（「合同期限为两年」），"
            "日期区间推算为 36 个月（「自2025年02月14日起至2028年02月14日止」）"
        ),
    }


def test_no_conflict_returns_none():
    assert judge_term_conflict(_extract("real_contract")) is None
    assert judge_term_conflict(_extract("synthetic_0002")) is None


def test_missing_term_returns_none():
    assert judge_term_conflict({}) is None
