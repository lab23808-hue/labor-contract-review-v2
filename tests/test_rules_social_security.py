from pathlib import Path

from domain.rules.social_security import judge_social_security

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"

KEYWORDS = (
    "社会保险",
    "社保",
    "五险",
    "养老保险",
    "医疗保险",
    "失业保险",
    "工伤保险",
    "生育保险",
    "住房公积金",
)


def test_missing_social_security_reported():
    text = "劳动合同\n甲方：某公司\n乙方：张三\n月工资：8000元"

    risk = judge_social_security(text)

    assert risk == {
        "source": "rules",
        "risk_type": "social_security_missing",
        "severity": "high",
        "excerpt": "",
        "explanation": "合同全文未见任何社会保险相关条款",
    }


def test_present_social_security_not_reported():
    for name in ("real_contract", "synthetic_0001", "synthetic_0002"):
        text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
        assert judge_social_security(text) is None


def test_any_keyword_suppresses_the_rule():
    for keyword in KEYWORDS:
        assert judge_social_security(f"劳动合同\n{keyword}相关约定。") is None
