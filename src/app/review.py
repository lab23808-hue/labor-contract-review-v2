import logging
from typing import Any, Optional, Protocol

from domain.merge.merger import merge_review
from domain.prompt_builder import build_prompt
from domain.rules.penalty import judge_penalty
from domain.rules.probation import judge_probation
from domain.rules.social_security import judge_social_security
from domain.rules.term_conflict import judge_term_conflict
from domain.rules.wage import judge_wage
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


def run_full_review(
    cleaned_text: str,
    extract_json: dict[str, Any],
    llm_client: Optional[LLMClient] = None,
) -> dict[str, Any]:
    rules_risks = [
        risk
        for risk in (
            judge_probation(extract_json),
            judge_term_conflict(extract_json),
            judge_social_security(cleaned_text),
            judge_wage(extract_json),
        )
        if risk is not None
    ]
    rules_risks.extend(judge_penalty(cleaned_text))
    llm_output = run_llm_branch(cleaned_text, extract_json, llm_client)
    return merge_review(rules_risks, llm_output)


def _default_client() -> LLMClient:
    from infra.llm_client import DeepSeekLLMClient

    return DeepSeekLLMClient()
