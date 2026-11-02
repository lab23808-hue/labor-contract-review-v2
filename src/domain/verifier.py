import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")


def verify_llm_output(
    llm_output: dict[str, Any],
    cleaned_text: str,
    dropped: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    normalized_text = _squash_whitespace(cleaned_text)
    verified = dict(llm_output)
    verified["risks"] = _filter_items(
        llm_output.get("risks"),
        normalized_text,
        "risk",
        require_violation=True,
        dropped=dropped,
    )
    verified["other_observations"] = _filter_items(
        llm_output.get("other_observations"),
        normalized_text,
        "observation",
        require_violation=False,
        dropped=dropped,
    )
    return verified


def _filter_items(
    items: Any,
    normalized_text: str,
    label: str,
    *,
    require_violation: bool,
    dropped: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        if items is not None:
            logger.warning("LLM %s 字段不是数组，已清空：%r", label, items)
        return []

    kept: list[dict[str, Any]] = []
    for item in items:
        excerpt = item.get("excerpt") if isinstance(item, dict) else None
        if not _excerpt_matches(excerpt, normalized_text):
            _record_drop(dropped, label, item, "excerpt 不命中")
            logger.warning(
                "丢弃 LLM %s：excerpt 不在原文中：%r", label, excerpt
            )
            continue
        if require_violation:
            if not isinstance(item, dict) or "is_violation" not in item:
                _record_drop(dropped, label, item, "字段缺失")
                logger.warning(
                    "丢弃 LLM %s：is_violation 不为 true：%s",
                    label,
                    json.dumps(item, ensure_ascii=False),
                )
                continue
            if item.get("is_violation") is not True:
                _record_drop(dropped, label, item, "is_violation=false")
                logger.warning(
                    "丢弃 LLM %s：is_violation 不为 true：%s",
                    label,
                    json.dumps(item, ensure_ascii=False),
                )
                continue
        kept.append(item)
    return kept


def _record_drop(
    dropped: Optional[list[dict[str, Any]]],
    label: str,
    item: Any,
    reason: str,
) -> None:
    if dropped is not None:
        dropped.append({"kind": label, "entry": item, "reason": reason})


def _excerpt_matches(excerpt: Any, normalized_text: str) -> bool:
    if not isinstance(excerpt, str) or not excerpt:
        return False
    normalized_excerpt = _squash_whitespace(excerpt)
    return bool(normalized_excerpt) and normalized_excerpt in normalized_text


def _squash_whitespace(text: str) -> str:
    return _WHITESPACE.sub("", text)
