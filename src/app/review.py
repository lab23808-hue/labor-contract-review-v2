import logging
from typing import Any, Optional, Protocol

from domain.prompt_builder import build_prompt
from domain.verifier import verify_llm_output

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    def call_llm(self, prompt: str) -> dict[str, Any]: ...


def run_llm_branch(
    cleaned_text: str,
    extract_json: dict[str, Any],
    llm_client: Optional[LLMClient] = None,
) -> dict[str, Any]:
    client = llm_client if llm_client is not None else _default_client()
    prompt = build_prompt(cleaned_text, extract_json)
    raw = client.call_llm(prompt)
    if raw.get("llm_failed"):
        logger.warning("LLM 调用失败，放行下游：%s", raw.get("error"))
        return raw
    return verify_llm_output(raw, cleaned_text)


def _default_client() -> LLMClient:
    from infra.llm_client import DeepSeekLLMClient

    return DeepSeekLLMClient()
