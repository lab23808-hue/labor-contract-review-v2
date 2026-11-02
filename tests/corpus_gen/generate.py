"""Generate the M12 coverage corpus from one legal contract skeleton.

Each G-xx builder starts from the same section list and injects exactly one
defect (or none for the clean baseline). Article numbers are assigned after
injection, so deleting a whole section does not leave a numbering gap.

Usage:
    python tests/corpus_gen/generate.py          # write generated/G-xx.txt
    python tests/corpus_gen/generate.py --check  # verify files are current
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = (ROOT / "template.txt").read_text(encoding="utf-8")
OUT_DIR = ROOT / "generated"

_CN_NUMERALS = "一二三四五六七八九十"

_BASE_TERM = (
    "本合同为固定期限劳动合同，自2025年3月1日起至2027年2月28日止，"
    "其中试用期为二个月（自2025年3月1日起至2025年4月30日止）。"
)
_BASE_PAY = (
    "乙方月工资为人民币10000元，试用期期间月工资为10000元。"
    "甲方于每月10日前以货币形式足额支付。"
)
_BASE_SOCIAL = (
    "甲方依法为乙方办理社会保险登记，缴纳养老、医疗、失业、工伤、"
    "生育保险费，乙方承担部分由甲方代扣代缴。"
)


def _base_sections():
    return [
        ("合同期限", _BASE_TERM),
        ("工作内容与地点", "乙方担任行政专员岗位，工作地点为北京市朝阳区。"),
        (
            "工作时间",
            "甲方安排乙方执行标准工时制度，每日工作不超过8小时，"
            "每周不超过40小时。",
        ),
        ("劳动报酬", _BASE_PAY),
        ("社会保险", _BASE_SOCIAL),
        (
            "合同解除与终止",
            "双方解除、终止本合同依照《中华人民共和国劳动合同法》"
            "及国家有关规定执行。",
        ),
        (
            "争议处理",
            "因履行本合同发生争议的，可依法向劳动争议仲裁委员会申请仲裁。",
        ),
    ]


def _replace(sections, title, body):
    return [
        (section_title, body if section_title == title else section_body)
        for section_title, section_body in sections
    ]


def _remove(sections, title):
    return [
        (section_title, section_body)
        for section_title, section_body in sections
        if section_title != title
    ]


def _add(sections, title, body):
    return sections + [(title, body)]


def _reorder(sections, titles):
    by_title = {title: body for title, body in sections}
    return [(title, by_title[title]) for title in titles]


def _article(index, title, body):
    numeral = (
        _CN_NUMERALS[index - 1] if 1 <= index <= len(_CN_NUMERALS) else str(index)
    )
    return f"第{numeral}条 {title}\n{body}"


def _render(index, sections):
    body = "\n\n".join(
        _article(position, title, text)
        for position, (title, text) in enumerate(sections, start=1)
    )
    return (
        TEMPLATE.replace(
            "{{EMPLOYER}}", f"生成语料科技有限公司G-{index:02d}"
        )
        .replace("{{EMPLOYEE}}", f"语料劳动者G-{index:02d}")
        .replace("{{SIGN_DATE}}", "2025年2月20日")
        .replace("{{SECTIONS}}", body)
    )


def _clean():
    return _base_sections()


def _probation_exceed():
    sections = _base_sections()
    return _replace(
        sections,
        "合同期限",
        "本合同为固定期限劳动合同，自2025年3月1日起至2026年2月28日止，"
        "其中试用期为四个月（自2025年3月1日起至2025年6月30日止）。",
    )


def _social_missing_one():
    return _replace(
        _base_sections(),
        "社会保险",
        "甲方依法为乙方办理社会保险登记，缴纳养老、医疗、失业、工伤保险费，"
        "乙方承担部分由甲方代扣代缴。",
    )


def _illegal_penalty():
    return _add(
        _base_sections(),
        "违约责任",
        "乙方在合同期内提前离职的，应向甲方支付违约金人民币20000元。",
    )


def _noncompete_cross_clause():
    sections = _add(
        _base_sections(),
        "竞业限制",
        "乙方离职后两年内不得入职与甲方有竞争关系的单位，"
        "竞业限制期限内无经济补偿。",
    )
    return _add(
        sections,
        "经济补偿",
        "合同解除或终止时，经济补偿按工作年限计算。",
    )


def _term_inconsistent():
    return _replace(
        _base_sections(),
        "合同期限",
        "本合同为固定期限劳动合同，合同期限为一年。"
        "本合同自2025年5月1日起至2028年4月30日止，"
        "其中试用期为一个月（自2025年5月1日起至2025年5月31日止）。",
    )


def _wage_below_floor():
    return _replace(
        _base_sections(),
        "劳动报酬",
        "乙方月工资为人民币10000元，试用期期间月工资为7000元。"
        "甲方于每月10日前以货币形式足额支付。",
    )


def _delete_social_security():
    return _remove(_base_sections(), "社会保险")


def _delete_remuneration():
    return _remove(_base_sections(), "劳动报酬")


def _delete_contract_term():
    return _remove(_base_sections(), "合同期限")


def _probation_exceed_rewrite():
    sections = _replace(
        _base_sections(),
        "合同期限",
        "本合同为固定期限劳动合同，期限一年，"
        "自2025年3月1日起至2026年2月28日止。"
        "试用期为4个月，自2025年3月1日起至2025年6月30日止。",
    )
    return _reorder(
        sections,
        [
            "合同期限",
            "社会保险",
            "劳动报酬",
            "工作内容与地点",
            "工作时间",
            "合同解除与终止",
            "争议处理",
        ],
    )


def _illegal_penalty_rewrite():
    return _add(
        _base_sections(),
        "违约责任",
        "如乙方提前解除本合同，须向甲方支付违约金人民币20000元。",
    )


def _term_inconsistent_rewrite():
    sections = _replace(
        _base_sections(),
        "合同期限",
        "本合同自2025年5月1日起至2028年4月30日止。"
        "双方确认本合同期限为一年，"
        "其中试用期为一个月（自2025年5月1日起至2025年5月31日止）。",
    )
    return _reorder(
        sections,
        [
            "工作内容与地点",
            "合同期限",
            "工作时间",
            "劳动报酬",
            "社会保险",
            "合同解除与终止",
            "争议处理",
        ],
    )


def _noncompete_cross_clause_rewrite():
    sections = _add(
        _base_sections(),
        "竞业限制",
        "乙方离职后两年内不得从事与甲方有竞争关系的业务，"
        "竞业限制期间不支付经济补偿。",
    )
    return _add(
        sections,
        "经济补偿",
        "劳动合同解除或终止时，甲方依法支付经济补偿。",
    )


def _wage_below_floor_rewrite():
    return _replace(
        _base_sections(),
        "劳动报酬",
        "乙方月工资为人民币12000元，试用期月工资为9000元。"
        "甲方于每月10日前以货币形式足额支付。",
    )


def _delete_social_security_rewrite():
    sections = _remove(_base_sections(), "社会保险")
    return _reorder(
        sections,
        [
            "工作内容与地点",
            "合同期限",
            "劳动报酬",
            "工作时间",
            "合同解除与终止",
            "争议处理",
        ],
    )


_BUILDERS = [
    ("G-01.txt", _clean),
    ("G-02.txt", _probation_exceed),
    ("G-03.txt", _social_missing_one),
    ("G-04.txt", _illegal_penalty),
    ("G-05.txt", _noncompete_cross_clause),
    ("G-06.txt", _term_inconsistent),
    ("G-07.txt", _wage_below_floor),
    ("G-08.txt", _delete_social_security),
    ("G-09.txt", _delete_remuneration),
    ("G-10.txt", _delete_contract_term),
    ("G-11.txt", _probation_exceed_rewrite),
    ("G-12.txt", _illegal_penalty_rewrite),
    ("G-13.txt", _term_inconsistent_rewrite),
    ("G-14.txt", _noncompete_cross_clause_rewrite),
    ("G-15.txt", _wage_below_floor_rewrite),
    ("G-16.txt", _delete_social_security_rewrite),
]


def build_contracts():
    return {
        name: _render(index, builder())
        for index, (name, builder) in enumerate(_BUILDERS, start=1)
    }


def main(argv):
    contracts = build_contracts()
    expected_path = ROOT / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    if set(contracts) != set(expected):
        raise SystemExit(
            "expected.json 与生成文件集合不一致："
            f"{sorted(set(contracts) ^ set(expected))}"
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if "--check" in argv:
        mismatches = [
            name
            for name, text in contracts.items()
            if not (OUT_DIR / name).is_file()
            or (OUT_DIR / name).read_text(encoding="utf-8") != text
        ]
        if mismatches:
            raise SystemExit(f"语料未同步：{mismatches}")
        print(f"corpus up to date: {len(contracts)} contracts")
        return 0

    for name, text in contracts.items():
        (OUT_DIR / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(contracts)} contracts to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
