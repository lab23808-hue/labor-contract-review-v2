from domain.rules.noncompete import judge_noncompete

EXPLANATION = (
    "竞业限制条款未约定经济补偿。依据《劳动合同法》第二十三条，"
    "竞业限制期限内用人单位须按月给付经济补偿；"
    "未约定补偿的条款损害劳动者权益。"
)


def test_noncompete_with_compensation_is_skipped():
    text = "劳动合同\n双方约定竞业限制，竞业限制期间甲方按月支付经济补偿。\n"

    assert judge_noncompete(text) == []


def test_noncompete_without_compensation_reports_one():
    text = "劳动合同\n双方约定竞业限制，乙方离职后二年内不得入职竞争单位。\n"

    assert judge_noncompete(text) == [
        {
            "source": "rules",
            "risk_type": "noncompete_no_pay",
            "severity": "high",
            "excerpt": "双方约定竞业限制，乙方离职后二年内不得入职竞争单位",
            "explanation": EXPLANATION,
        }
    ]


def test_no_noncompete_returns_empty():
    assert judge_noncompete("劳动合同\n乙方月工资为8000元。\n") == []


def test_no_compensation_variant_reports_and_skips_title_clause():
    text = (
        "劳动合同\n"
        "第六条 竞业限制\n"
        "乙方离职后三年内不得入职与甲方有竞争关系的单位，"
        "竞业限制期间甲方无需支付任何补偿。\n"
    )

    risks = judge_noncompete(text)

    assert len(risks) == 1
    assert risks[0]["excerpt"] == (
        "乙方离职后三年内不得入职与甲方有竞争关系的单位，"
        "竞业限制期间甲方无需支付任何补偿"
    )


def test_title_keyword_suppresses_report_current_behavior():
    # 条款作用域收窄后，标题段若自身含「竞业限制补偿」，它就是唯一的
    # 竞业限制条款，补偿关键词仍在条款内，因此仍被判为已约定补偿。
    # 这是标题型条款的已知边界，不额外加启发式。
    text = "劳动合同\n竞业限制补偿：乙方放弃补偿，甲方无需支付。\n"

    assert judge_noncompete(text) == []


def test_compensation_outside_noncompete_clause_does_not_count():
    text = (
        "劳动合同\n"
        "第十条 竞业限制：乙方离职后两年内不得入职与甲方有竞争关系的单位，"
        "竞业限制期限内无经济补偿。"
        "第十六条 经济补偿：合同解除或终止时，经济补偿按工作年限计算。\n"
    )

    risks = judge_noncompete(text)

    assert len(risks) == 1
    assert risks[0]["risk_type"] == "noncompete_no_pay"
    assert "竞业限制期限内无经济补偿" in risks[0]["excerpt"]
    assert "按工作年限计算" not in risks[0]["excerpt"]


def test_compensation_inside_noncompete_clause_is_not_reported():
    text = (
        "劳动合同\n"
        "第十条 竞业限制：乙方离职后两年内不得入职与甲方有竞争关系的单位，"
        "竞业限制期限内甲方按月支付经济补偿。\n"
    )

    assert judge_noncompete(text) == []


def test_negated_compensation_reports():
    text = "劳动合同\n双方约定竞业限制，无经济补偿。\n"

    risks = judge_noncompete(text)

    assert len(risks) == 1
    assert risks[0]["risk_type"] == "noncompete_no_pay"


def test_no_need_to_pay_compensation_reports():
    text = (
        "劳动合同\n"
        "双方约定竞业限制，竞业限制期间甲方无需支付经济补偿。\n"
    )

    risks = judge_noncompete(text)

    assert len(risks) == 1
    assert risks[0]["risk_type"] == "noncompete_no_pay"


def test_monthly_compensation_is_positive():
    text = (
        "劳动合同\n"
        "双方约定竞业限制，竞业限制期间甲方按月支付经济补偿。\n"
    )

    assert judge_noncompete(text) == []


def test_positive_mention_after_negative_keeps_compensation():
    text = (
        "劳动合同\n"
        "双方约定竞业限制，无经济补偿，甲方按月支付经济补偿。\n"
    )

    assert judge_noncompete(text) == []


def test_distant_negation_does_not_suppress():
    text = "劳动合同\n双方约定竞业限制，无任何形式的额外经济补偿。\n"

    assert judge_noncompete(text) == []
