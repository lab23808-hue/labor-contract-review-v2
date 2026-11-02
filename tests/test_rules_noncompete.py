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
    # 封闭的全文关键词判定会被标题中的「竞业限制补偿」骗过；
    # 此处如实记录该已知边界，不额外加启发式。
    text = "劳动合同\n竞业限制补偿：乙方放弃补偿，甲方无需支付。\n"

    assert judge_noncompete(text) == []
