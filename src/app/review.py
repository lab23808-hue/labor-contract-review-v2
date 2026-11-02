import copy
import logging
from pathlib import Path
from typing import Any, Optional, Protocol

from app.trace import TraceRecorder
from domain.merge.merger import merge_review
from domain.prompt_builder import build_prompt
from domain.rules.noncompete import judge_noncompete
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
    raw_out: Optional[dict[str, Any]] = None,
    dropped_out: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    client = llm_client if llm_client is not None else _default_client()
    prompt = build_prompt(cleaned_text, extract_json)
    raw = client.call_llm(prompt)
    if raw_out is not None:
        raw_out.update(copy.deepcopy(raw))
    if raw.get("llm_failed"):
        logger.warning("LLM 调用失败，放行下游：%s", raw.get("error"))
        return raw
    return verify_llm_output(raw, cleaned_text, dropped=dropped_out)


def run_full_review(
    cleaned_text: str,
    extract_json: dict[str, Any],
    llm_client: Optional[LLMClient] = None,
    trace_dir: Optional[Path] = None,
) -> dict[str, Any]:
    recorder = TraceRecorder(trace_dir) if trace_dir is not None else None

    rule_outputs = {
        "probation": _as_list(judge_probation(extract_json)),
        "term_conflict": _as_list(judge_term_conflict(extract_json)),
        "social_security": _as_list(judge_social_security(cleaned_text)),
        "wage": _as_list(judge_wage(extract_json)),
        "penalty": judge_penalty(cleaned_text),
        "noncompete": judge_noncompete(cleaned_text),
    }
    rules_risks = [
        risk for risks in rule_outputs.values() for risk in risks
    ]

    if recorder is not None:
        recorder.write_json("03_extraction.json", extract_json)
        recorder.write_json("04_rules.json", rule_outputs)
        counts = {
            name: len(risks) for name, risks in rule_outputs.items()
        }
        recorder.log(f"rules {counts}")

    raw_out = {} if recorder is not None else None
    dropped = [] if recorder is not None else None
    llm_output = run_llm_branch(
        cleaned_text,
        extract_json,
        llm_client,
        raw_out=raw_out,
        dropped_out=dropped,
    )

    if recorder is not None:
        recorder.write_json("05_llm_raw.json", raw_out)
        verified_payload = dict(llm_output)
        verified_payload["dropped"] = dropped
        recorder.write_json("06_llm_verified.json", verified_payload)
        recorder.log(
            "llm_raw risks=%d observations=%d failed=%s"
            % (
                len(raw_out.get("risks") or []),
                len(raw_out.get("other_observations") or []),
                bool(raw_out.get("llm_failed")),
            )
        )
        recorder.log(
            "llm_verified risks=%d observations=%d dropped=%d"
            % (
                len(llm_output.get("risks") or []),
                len(llm_output.get("other_observations") or []),
                len(dropped),
            )
        )

    arbitration = [] if recorder is not None else None
    report = merge_review(rules_risks, llm_output, trace_out=arbitration)

    if recorder is not None:
        recorder.write_json(
            "07_merger.json",
            {"report": report, "arbitration": arbitration},
        )
        recorder.write_json("08_final_report.json", report)
        recorder.log(
            "merger risks=%d sources=%s arbitration_events=%d"
            % (
                report["meta"]["risk_count"],
                report["meta"]["sources"],
                len(arbitration),
            )
        )
        recorder.log(
            "final risks=%d observations=%d"
            % (len(report["risks"]), len(report["other_observations"]))
        )

    return report


def _as_list(risk: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    return [] if risk is None else [risk]


def _default_client() -> LLMClient:
    from infra.llm_client import DeepSeekLLMClient

    return DeepSeekLLMClient()
