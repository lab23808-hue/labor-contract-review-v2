"""规则结果与 LLM 结果的确定性合流。

仲裁规则：
1. 按 risk_type 分组：规则命中该 type → 规则版胜出，该 type 的全部 LLM 版
   逐条丢弃并记录日志；
2. 规则沉默该 type → 该 type 的 LLM 条目全部保留并标记 source=llm，
   条目之间不互相折叠；
3. 规则单源 → 保留；
4. severity 随被保留的一方，不做二次仲裁；
5. llm_failed → 规则结果照常落报告，meta 标记；
6. other_observations 原样透传（source=llm），不参与 risks 去重；
7. 排序：high→medium→low，同级按 risk_type 字典序。
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def merge_review(
    rules_risks: list[dict[str, Any]], llm_output: dict[str, Any]
) -> dict[str, Any]:
    llm_failed = bool(llm_output.get("llm_failed"))

    rules_by_type: dict[str, dict[str, Any]] = {}
    for risk in rules_risks:
        rules_by_type[risk["risk_type"]] = _with_source(risk, "rules")

    llm_by_type: dict[str, list[dict[str, Any]]] = {}
    for risk in llm_output.get("risks") or []:
        risk_type = risk.get("risk_type")
        if not risk_type:
            logger.warning("丢弃缺少 risk_type 的 LLM 风险：%r", risk)
            continue
        llm_by_type.setdefault(risk_type, []).append(risk)

    final_risks = list(rules_by_type.values())
    for risk_type, llm_risks in llm_by_type.items():
        if risk_type in rules_by_type:
            for risk in llm_risks:
                logger.warning(
                    "同 risk_type 双源命中，丢弃 LLM 版：%s",
                    json.dumps(risk, ensure_ascii=False),
                )
            continue
        final_risks.extend(
            _with_source(risk, "llm") for risk in llm_risks
        )

    if llm_failed:
        logger.warning("LLM 分支失败，报告仅保留规则结果")

    final_risks = sorted(
        final_risks,
        key=lambda risk: (
            _SEVERITY_ORDER.get(risk.get("severity"), 3),
            risk.get("risk_type", ""),
        ),
    )

    observations = []
    for item in llm_output.get("other_observations") or []:
        observation = dict(item)
        observation["source"] = "llm"
        observations.append(observation)

    sources = {"rules": 0, "llm": 0}
    for risk in final_risks:
        sources[risk["source"]] += 1

    return {
        "risks": final_risks,
        "other_observations": observations,
        "meta": {
            "llm_failed": llm_failed,
            "risk_count": len(final_risks),
            "sources": sources,
        },
    }


def _with_source(risk: dict[str, Any], source: str) -> dict[str, Any]:
    merged = dict(risk)
    merged["source"] = source
    return merged
