import logging
from typing import Any

logger = logging.getLogger(__name__)


def verify_llm_output(
    llm_output: dict[str, Any], cleaned_text: str
) -> dict[str, Any]:
    verified = dict(llm_output)
    verified["risks"] = _filter_items(
        llm_output.get("risks"), cleaned_text, "risk"
    )
    verified["other_observations"] = _filter_items(
        llm_output.get("other_observations"), cleaned_text, "observation"
    )
    return verified


def _filter_items(
    items: Any, cleaned_text: str, label: str
) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        if items is not None:
            logger.warning("LLM %s 字段不是数组，已清空：%r", label, items)
        return []

    kept: list[dict[str, Any]] = []
    for item in items:
        excerpt = item.get("excerpt") if isinstance(item, dict) else None
        if isinstance(excerpt, str) and excerpt and excerpt in cleaned_text:
            kept.append(item)
        else:
            logger.warning(
                "丢弃 LLM %s：excerpt 不在原文中：%r", label, excerpt
            )
    return kept
