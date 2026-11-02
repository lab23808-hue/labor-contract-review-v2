"""规则结果与 LLM 结果的确定性合流。

仲裁规则：
1. 按 risk_type 分组：规则命中该 type → 该 type 的全部规则版保留（多条不
   折叠），该 type 的全部 LLM 版逐条丢弃并记录日志；
2. 规则沉默该 type → 该 type 的 LLM 条目全部保留并标记 source=llm，
   条目之间不互相折叠；
3. 规则单源 → 全部保留；
4. severity 随被保留的一方，不做二次仲裁；
5. llm_failed → 规则结果照常落报告，meta 标记；
6. other_observations 原样透传（source=llm），不参与 risks 去重；
7. 排序：high→medium→low，同级按 risk_type 字典序。
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def merge_review(
    rules_risks: list[dict[str, Any]],
    llm_output: dict[str, Any],
    trace_out: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    llm_failed = bool(llm_output.get("llm_failed"))

    rule_types = {risk["risk_type"] for risk in rules_risks}

    llm_by_type: dict[str, list[dict[str, Any]]] = {}
    for risk in llm_output.get("risks") or []:
        risk_type = risk.get("risk_type")
        if not risk_type:
            logger.warning(
                "", extra={"event": "merger.drop_missing_type", "entry": risk}
            )
            if trace_out is not None:
                trace_out.append(
                    {"action": "drop_llm_missing_type", "entry": risk}
                )
            continue
        llm_by_type.setdefault(risk_type, []).append(risk)

    final_risks = []
    for risk in rules_risks:
        final_risks.append(_with_source(risk, "rules"))
        logger.debug(
            "",
            extra={
                "event": "merger.keep_rule",
                "risk_type": risk["risk_type"],
                "excerpt": risk.get("excerpt"),
            },
        )
        if trace_out is not None:
            trace_out.append(
                {
                    "action": "keep_rule",
                    "risk_type": risk["risk_type"],
                    "entry": risk,
                }
            )

    for risk_type, llm_risks in llm_by_type.items():
        if risk_type in rule_types:
            for risk in llm_risks:
                logger.info(
                    "",
                    extra={
                        "event": "merger.arbitrate",
                        "risk_type": risk_type,
                        "winner": "rules",
                        "loser": "llm",
                        "excerpt": risk.get("excerpt"),
                    },
                )
                if trace_out is not None:
                    trace_out.append(
                        {
                            "action": "drop_llm_rule_hit",
                            "risk_type": risk_type,
                            "entry": risk,
                        }
                    )
            continue
        for risk in llm_risks:
            final_risks.append(_with_source(risk, "llm"))
            logger.info(
                "",
                extra={
                    "event": "merger.arbitrate",
                    "risk_type": risk_type,
                    "winner": "llm",
                    "excerpt": risk.get("excerpt"),
                },
            )
            if trace_out is not None:
                trace_out.append(
                    {
                        "action": "keep_llm",
                        "risk_type": risk_type,
                        "entry": risk,
                    }
                )

    if llm_failed:
        logger.warning("", extra={"event": "merger.llm_failed"})

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
