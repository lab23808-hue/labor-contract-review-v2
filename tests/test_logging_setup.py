import json
import logging
from pathlib import Path

import pytest

from app.review import run_full_review
from config.llm import LLMConfig
from domain.cleaning import clean_text
from domain.extractor import extract_fields
from infra.docx_parser import DocxParser
from infra.llm_client import DeepSeekLLMClient, FakeLLM
from infra.txt_parser import TxtParser
from logging_setup import reset_logging, setup_logging

FIXTURES = Path(__file__).parent / "fixtures" / "realistic"
BASELINE = Path(__file__).parent / "snapshots" / "mlog_baseline"


@pytest.fixture(autouse=True)
def _close_logging():
    yield
    reset_logging()


def _cleaned_text(fixture: Path) -> str:
    content = fixture.read_bytes()
    if fixture.suffix == ".txt":
        parsed = TxtParser().parse(fixture.name, content)
    else:
        parsed = DocxParser().parse(fixture.name, content)
    return clean_text(parsed)


def _response(text: str) -> dict:
    excerpt = text.splitlines()[0]
    return {
        "risks": [
            {
                "risk_type": "probation_exceed",
                "severity": "high",
                "excerpt": excerpt,
                "explanation": "",
                "is_violation": True,
            },
            {
                "risk_type": "illegal_penalty",
                "severity": "medium",
                "excerpt": excerpt,
                "explanation": "",
                "is_violation": False,
            },
            {
                "risk_type": "open_ended_denied",
                "severity": "medium",
                "excerpt": "原文没有这句",
                "explanation": "",
                "is_violation": True,
            },
        ],
        "other_observations": [
            {"excerpt": excerpt, "note": ""},
            {"excerpt": "原文没有这句", "note": ""},
        ],
    }


def test_setup_logging_writes_valid_json_lines(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    log_path = setup_logging("test_run")
    logger = logging.getLogger("test.module")

    logger.info(
        "", extra={"event": "test.info", "foo": "bar", "count": 2}
    )
    logger.warning(
        "", extra={"event": "test.warn", "risk_type": "probation_exceed"}
    )
    for handler in logging.getLogger().handlers:
        handler.flush()

    payloads = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        payload["event"] == "test.info"
        and payload["foo"] == "bar"
        and payload["count"] == 2
        for payload in payloads
    )
    assert any(
        payload["event"] == "test.warn"
        and payload["risk_type"] == "probation_exceed"
        for payload in payloads
    )
    assert all("run_id" in payload for payload in payloads)


def test_llm_client_logs_timing_and_retry(monkeypatch, caplog):
    client = DeepSeekLLMClient(
        config=LLMConfig(
            api_key="test",
            base_url="http://localhost",
            model="fake",
            thinking="disabled",
        )
    )

    def fail(prompt):
        raise json.JSONDecodeError("bad json", "{}", 0)

    monkeypatch.setattr(client, "_request", fail)

    with caplog.at_level(logging.INFO):
        result = client.call_llm("prompt")

    assert result["llm_failed"] is True
    events = [
        getattr(record, "event", None) for record in caplog.records
    ]
    assert events.count("llm.call_start") == 2
    assert events.count("llm.call_failed") == 2
    assert events.count("llm.retry") == 1
    assert "llm.call_done" not in events
    assert "llm.failed" in events
    failures = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "llm.call_failed"
    ]
    assert all(hasattr(record, "elapsed") for record in failures)


@pytest.mark.parametrize(
    "fixture",
    sorted(FIXTURES.glob("C-*.*")),
    ids=lambda fixture: fixture.name,
)
def test_final_report_matches_pre_logging_baseline(fixture):
    text = _cleaned_text(fixture)
    report = run_full_review(
        text,
        extract_fields(text),
        FakeLLM(_response(text)),
        run_id="baseline_run",
    )
    report["meta"].pop("run_id")

    actual = json.dumps(report, ensure_ascii=False, indent=2)
    expected = (BASELINE / f"{fixture.name}.json").read_text(
        encoding="utf-8"
    )

    assert actual == expected
