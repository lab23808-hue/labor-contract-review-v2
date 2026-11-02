from domain.rules.wage import judge_wage


def test_below_80_percent_reported():
    text = (
        "劳动合同\n"
        "乙方月工资为人民币10000元，试用期期间月工资为7000元。\n"
    )

    assert judge_wage(text) == {
        "source": "rules",
        "risk_type": "wage_below_floor",
        "severity": "medium",
        "excerpt": "试用期期间月工资为7000元",
        "explanation": (
            "试用期工资 7000 低于转正工资 10000 的 80%"
            "（7000/10000=70% < 80%）"
        ),
    }


def test_exactly_80_percent_not_reported():
    text = "劳动合同\n月工资为10000元，试用期工资8000元。\n"

    assert judge_wage(text) is None


def test_above_80_percent_not_reported():
    text = "劳动合同\n月工资为10000元，试用期工资9000元。\n"

    assert judge_wage(text) is None


def test_missing_regular_salary_returns_none():
    assert judge_wage("劳动合同\n试用期工资6000元。\n") is None
    assert (
        judge_wage("劳动合同\n试用期期间月工资为7000元。\n") is None
    )


def test_missing_probation_salary_returns_none():
    assert judge_wage("劳动合同\n月工资为8000元。\n") is None


def test_chinese_numerals_are_extracted():
    text = "劳动合同\n转正后工资一万二千元，试用期工资九千元。\n"

    risk = judge_wage(text)

    assert risk is not None
    assert risk["excerpt"] == "试用期工资九千元"
    assert "9000/12000=75%" in risk["explanation"]


def test_ocr_whitespace_is_normalized():
    text = (
        "劳动合同\n"
        "试用期期间月工资为\n7000元，月工资为10000元。\n"
    )

    risk = judge_wage(text)

    assert risk is not None
    assert risk["excerpt"] == "试用期期间月工资为7000元"
    assert "7000/10000=70%" in risk["explanation"]
