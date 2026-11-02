from pathlib import Path

from domain.extractor import extract_fields
from domain.rules.probation import judge_probation

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"


def _extract(name: str) -> dict:
    text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    return extract_fields(text)


def test_probation_exceeded_with_conflict_uses_stricter_limit():
    risk = judge_probation(_extract("synthetic_0001"))

    assert risk == {
        "source": "rules",
        "risk_type": "probation_exceed",
        "severity": "high",
        "excerpt": "试用期为三个月",
        "explanation": "试用期约定 3 个月，超过该合同期限对应的法定上限 2 个月",
    }


def test_probation_within_limit_on_real_contract():
    assert judge_probation(_extract("real_contract")) is None


def test_probation_missing_returns_none():
    assert judge_probation(_extract("synthetic_0002")) is None


def test_probation_exactly_at_limit_not_reported():
    for term_months, probation_months in ((12, 2), (24, 2), (36, 6)):
        extract = {
            "contract_term": {"value": term_months},
            "probation_months": {"value": probation_months},
        }
        assert judge_probation(extract) is None


def test_probation_conflict_reports_when_any_candidate_exceeds():
    extract = {
        "contract_term": {"value": 6, "conflict": {"alternative": 12}},
        "probation_months": {"value": 3},
    }

    risk = judge_probation(extract)

    assert risk is not None
    assert risk["explanation"].endswith("法定上限 1 个月")
