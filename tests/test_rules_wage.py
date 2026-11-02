from domain.rules.wage import judge_wage


def _extract(
    probation_salary: float, salary: float = 8000
) -> dict:
    return {
        "salary": {"value": salary},
        "probation_salary": {
            "value": probation_salary,
            "evidence": "试用期工资6000元",
        },
    }


def test_below_80_percent_reported():
    risk = judge_wage(_extract(6000))

    assert risk == {
        "source": "rules",
        "risk_type": "wage_below_floor",
        "severity": "medium",
        "excerpt": "试用期工资6000元",
        "explanation": "试用期工资 6000 低于约定工资 8000 的 80% (6400)",
    }


def test_exactly_80_percent_not_reported():
    assert judge_wage(_extract(6400)) is None


def test_above_80_percent_not_reported():
    assert judge_wage(_extract(7000)) is None


def test_missing_fields_return_none():
    assert judge_wage({"salary": {"value": 8000}}) is None
    assert judge_wage({"probation_salary": {"value": 6000}}) is None
    assert judge_wage({}) is None
