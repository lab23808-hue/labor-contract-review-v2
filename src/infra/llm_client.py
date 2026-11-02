import json
import logging
from typing import Any, Optional

from openai import OpenAI

from config.llm import LLMConfig, get_llm_config

logger = logging.getLogger(__name__)

_SYSTEM_MARKER = "[SYSTEM]\n"
_USER_SEPARATOR = "\n\n[USER]\n"
_THINKING_MODES = {"enabled", "disabled"}


class DeepSeekLLMClient:
    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self._config = config or get_llm_config()
        self._client = OpenAI(
            api_key=self._config.api_key,
            base_url=self._config.base_url,
        )

    def call_llm(self, prompt: str) -> dict[str, Any]:
        last_error = ""
        for attempt in (1, 2):
            try:
                return json.loads(self._request(prompt))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "LLM 返回无法解析为 JSON（第 %d 次）：%s", attempt, last_error
                )
        logger.error("LLM 连续两次返回非 JSON，标记 llm_failed：%s", last_error)
        return {
            "risks": [],
            "other_observations": [],
            "llm_failed": True,
            "error": last_error,
        }

    def _request(self, prompt: str) -> str:
        system, user = _split_prompt(prompt)
        response = self._client.chat.completions.create(
            model=self._config.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": _thinking_mode(self._config.thinking)}},
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("LLM 返回空内容")
        return content


class FakeLLM:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def call_llm(self, prompt: str) -> dict[str, Any]:
        return self._response


def _split_prompt(prompt: str) -> tuple[str, str]:
    rest = (
        prompt[len(_SYSTEM_MARKER) :]
        if prompt.startswith(_SYSTEM_MARKER)
        else prompt
    )
    system, separator, user = rest.partition(_USER_SEPARATOR)
    if not separator:
        return "", prompt
    return system.strip(), user


def _thinking_mode(value: str) -> str:
    mode = (value or "disabled").strip().lower()
    return mode if mode in _THINKING_MODES else "disabled"
