from typing import Any

SYSTEM_MARKER = "[SYSTEM]"
USER_MARKER = "[USER]"

_SYSTEM_PROMPT = (
    "你是劳动合同合规审查员。逐条检查下列风险项，只基于提供的合同原文判断，"
    "不引入外部知识，不引用任何法条条号。"
)

_CHECKLIST = (
    "1. probation_exceed：试用期长度超过法定上限：期限<1年→1个月；满1年不足3年→2个月；满3年→6个月。「试工」「见习期」等变相叫法同样按试用期计。经计算未超过对应上限的，不得将该条目列入 risks。",
    "2. illegal_penalty：用人单位对劳动者罚款，无法律依据，一律报告。约定违约金的，仅两种情形合法：①专项培训费用+约定服务期；②竞业限制。其余情形（普通离职、提前解约、业绩不达标等）的违约金条款一律报告。",
    "3. disguised_waiver：条款试图排除法定义务或剥夺法定权利的，无论表述多「自愿」均报告。典型话术：「自愿放弃社保」「工伤概不负责」「伤亡与公司无关」「自愿加班不领加班费」「发生纠纷不得仲裁或诉讼」。",
    "4. wage_below_floor：约定工资低于当地最低工资标准的，报告。试用期工资低于约定工资80%的，报告。",
    "5. term_inconsistent：合同期限前后表述矛盾（与【已知信息】中的不一致提示对照判断）。",
    "6. social_security_missing：合同全文未见任何社会保险相关条款时报告。",
)

_OUTPUT_FORMAT = (
    '只输出 JSON，结构为 {"risks": [...], "other_observations": [...]}。'
    'risks 每条 {"risk_type": "上述清单中的名称", "severity": "high/medium/low", '
    '"excerpt": "<逐字来自原文>", "explanation": "一句话说明", '
    '"is_violation": true|false}。'
    "is_violation 为 true 表示经核对确认为违规；false 表示存疑、边界情形或"
    "经计算不构成违规——为 false 时同样写入 risks 数组，由下游处理。"
    '清单外的可疑条款放 other_observations，每条 {"excerpt", "note"}，不得放入 risks。'
    "没有风险则 risks 为空数组。"
)


def build_prompt(cleaned_text: str, extract_json: dict[str, Any]) -> str:
    user = (
        f"【已知信息】\n{_known_info(extract_json)}\n\n"
        f"【检查清单】\n" + "\n".join(_CHECKLIST) + "\n\n"
        f"【合同原文】\n{cleaned_text}\n\n"
        f"【输出格式】\n{_OUTPUT_FORMAT}"
    )
    return f"{SYSTEM_MARKER}\n{_SYSTEM_PROMPT}\n\n{USER_MARKER}\n{user}"


def _known_info(extract_json: dict[str, Any]) -> str:
    term = extract_json.get("contract_term", {})
    lines = [f"合同期限：{_value(term, '个月')}"]
    conflict = term.get("conflict")
    if conflict:
        lines.append(
            f"注意：原文另一处日期区间推算为 {conflict['alternative']} 个月，存在不一致"
        )
    lines.append(f"试用期：{_value(extract_json.get('probation_months', {}), '个月')}")
    lines.append(f"工资：{_value(extract_json.get('salary', {}), '元/月')}")
    lines.append(f"违约金条款：{_flag(extract_json.get('has_penalty_clause', {}))}")
    lines.append(f"社会保险条款：{_flag(extract_json.get('has_social_security', {}))}")
    lines.append("以上以此为准，不要自行从原文重新数数")
    return "\n".join(lines)


def _value(field: dict[str, Any], unit: str) -> str:
    value = field.get("value")
    if value is None:
        return "未发现"
    return f"{value} {unit}"


def _flag(field: dict[str, Any]) -> str:
    value = field.get("value")
    if value is None:
        return "未发现"
    return "是" if value else "否"
