from domain.rules.penalty import judge_penalty

EXPLANATION = (
    "本条约定由劳动者承担违约金。依据《劳动合同法》第二十五条，"
    "除服务期（第二十二条）和竞业限制（第二十三条）情形外，"
    "用人单位不得约定由劳动者承担违约金；若本条不属于上述两种情形，"
    "则该约定违法。"
)


def test_no_penalty_returns_empty():
    assert judge_penalty("劳动合同\n乙方月工资为8000元。\n") == []


def test_plain_penalty_reports_one():
    text = "劳动合同\n乙方提前离职的，应向甲方支付违约金人民币20000元。\n"

    assert judge_penalty(text) == [
        {
            "source": "rules",
            "risk_type": "illegal_penalty",
            "severity": "medium",
            "excerpt": "乙方提前离职的，应向甲方支付违约金人民币20000元",
            "explanation": EXPLANATION,
        }
    ]


def test_service_period_penalty_is_skipped():
    text = (
        "劳动合同\n"
        "甲方为乙方提供专项培训费用并约定服务期，"
        "乙方违反服务期约定的，应支付违约金5000元。\n"
    )

    assert judge_penalty(text) == []


def test_non_compete_penalty_is_skipped():
    text = (
        "劳动合同\n"
        "双方约定竞业限制，乙方违反竞业限制义务的，应支付违约金5000元。\n"
    )

    assert judge_penalty(text) == []


def test_only_illegal_clause_is_reported():
    text = (
        "劳动合同\n"
        "甲方为乙方提供专项培训费用并约定服务期，"
        "乙方违反服务期约定的，应支付违约金5000元；"
        "乙方提前离职的，应向甲方支付违约金20000元。\n"
    )

    risks = judge_penalty(text)

    assert len(risks) == 1
    assert risks[0]["excerpt"] == "乙方提前离职的，应向甲方支付违约金20000元"


def test_ocr_broken_clause_is_normalized_and_reported():
    text = "劳动合同\n乙方提前离职的，应向甲方支付违约\n金人民币20000元。\n"

    risks = judge_penalty(text)

    assert len(risks) == 1
    assert risks[0]["excerpt"] == (
        "乙方提前离职的，应向甲方支付违约金人民币20000元"
    )


def test_each_illegal_clause_reports_separately():
    text = (
        "劳动合同\n"
        "乙方提前离职的，应向甲方支付违约金20000元。"
        "乙方未提前三十日书面通知的，应另行支付违约金5000元。\n"
    )

    risks = judge_penalty(text)

    assert [risk["excerpt"] for risk in risks] == [
        "乙方提前离职的，应向甲方支付违约金20000元",
        "乙方未提前三十日书面通知的，应另行支付违约金5000元",
    ]
