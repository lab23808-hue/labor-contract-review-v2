import json
from pathlib import Path

import pytest

from app.review import run_llm_branch
from config.llm import get_llm_config
from domain.extractor import extract_fields
from infra.llm_client import DeepSeekLLMClient

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"
SNAPSHOTS = Path(__file__).parent / "snapshots" / "stability_0001"


@pytest.mark.slow
def test_synthetic_0001_is_stable_over_five_runs():
    try:
        get_llm_config()
    except RuntimeError:
        pytest.skip("未配置 DEEPSEEK_API_KEY")

    text = (FIXTURES / "synthetic_0001.txt").read_text(encoding="utf-8")
    extract_json = extract_fields(text)
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)

    outputs = []
    client = DeepSeekLLMClient()
    for index in range(1, 6):
        result = run_llm_branch(text, extract_json, client)
        outputs.append(result)
        (SNAPSHOTS / f"run_{index}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    assert not any(result.get("llm_failed") for result in outputs)

    baseline = {
        risk["risk_type"]: risk["excerpt"] for risk in outputs[0]["risks"]
    }
    for result in outputs[1:]:
        current = {
            risk["risk_type"]: risk["excerpt"] for risk in result["risks"]
        }
        assert set(current) == set(baseline)
        for risk_type, excerpt in baseline.items():
            assert current[risk_type] == excerpt
