import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")


def verify_llm_output(
    llm_output: dict[str, Any], cleaned_text: str
) -> dict[str, Any]:
    normalized_text = _squash_whitespace(cleaned_text)
    verified = dict(llm_output)
    verified["risks"] = _filter_items(
        llm_output.get("risks"), normalized_text, "risk"
    )
    verified["other_observations"] = _filter_items(
        llm_output.get("other_observations"), normalized_text, "observation"
    )
    return verified


def _filter_items(
    items: Any, normalized_text: str, label: str
) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        if items is not None:
            logger.warning("LLM %s 字段不是数组，已清空：%r", label, items)
        return []

    kept: list[dict[str, Any]] = []
    for item in items:
        excerpt = item.get("excerpt") if isinstance(item, dict) else None
        if isinstance(excerpt, str) and excerpt:
            normalized_excerpt = _squash_whitespace(excerpt)
            if normalized_excerpt and normalized_excerpt in normalized_text:
                kept.append(item)
                continue
        logger.warning(
            "丢弃 LLM %s：excerpt 不在原文中：%r", label, excerpt
        )
    return kept


def _squash_whitespace(text: str) -> str:
    return _WHITESPACE.sub("", text)
