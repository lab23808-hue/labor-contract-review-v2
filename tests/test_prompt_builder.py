from pathlib import Path

from domain.extractor import extract_fields
from domain.prompt_builder import build_prompt

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"
SNAPSHOTS = Path(__file__).parent / "snapshots"

CHECKLIST = (
    "1. probation_exceed：试用期长度超过法定上限：期限<1年→1个月；满1年不足3年→2个月；满3年→6个月。「试工」「见习期」等变相叫法同样按试用期计。",
    "2. illegal_penalty：用人单位对劳动者罚款，无法律依据，一律报告。约定违约金的，仅两种情形合法：①专项培训费用+约定服务期；②竞业限制。其余情形（普通离职、提前解约、业绩不达标等）的违约金条款一律报告。",
    "3. disguised_waiver：条款试图排除法定义务或剥夺法定权利的，无论表述多「自愿」均报告。典型话术：「自愿放弃社保」「工伤概不负责」「伤亡与公司无关」「自愿加班不领加班费」「发生纠纷不得仲裁或诉讼」。",
    "4. wage_below_floor：约定工资低于当地最低工资标准的，报告。试用期工资低于约定工资80%的，报告。",
    "5. term_inconsistent：合同期限前后表述矛盾（与【已知信息】中的不一致提示对照判断）。",
    "6. social_security_missing：合同全文未见任何社会保险相关条款时报告。",
)


def _prompt_for(name: str) -> tuple[str, str]:
    text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    return text, build_prompt(text, extract_fields(text))


def test_prompt_matches_snapshot_for_synthetic_0001():
    _, prompt = _prompt_for("synthetic_0001")

    expected = (SNAPSHOTS / "prompt_0001.txt").read_text(encoding="utf-8")

    assert prompt == expected


def test_conflict_hint_and_checklist_are_embedded():
    _, prompt = _prompt_for("synthetic_0001")

    assert (
        "注意：原文另一处日期区间推算为 36 个月，存在不一致" in prompt
    )
    for item in CHECKLIST:
        assert item in prompt
