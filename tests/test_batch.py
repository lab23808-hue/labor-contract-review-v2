import json
import logging
from pathlib import Path

import pytest

from app.offline_batch import run_batch
from app.review_engine import run_full_review
from cli import main
from domain.cleaning import clean_text
from domain.extractor import extract_fields
from infra.llm_client import FakeLLM
from infra.txt_parser import TxtParser
from logging_setup import reset_logging

CLEAN = """劳动合同书
甲方：测试公司
乙方：测试员工
第一条 合同期限
本合同为固定期限劳动合同，自2025年3月1日起至2027年2月28日止，其中试用期为二个月（自2025年3月1日起至2025年4月30日止）。
第二条 劳动报酬
乙方月工资为人民币10000元，试用期期间月工资为10000元。
第三条 社会保险
甲方依法为乙方办理社会保险登记，缴纳养老、医疗、失业、工伤、生育保险费。
第四条 争议处理
因履行本合同发生争议的，可依法向劳动争议仲裁委员会申请仲裁。
"""

WAGE = CLEAN.replace(
    "试用期期间月工资为10000元", "试用期期间月工资为7000元"
)

ZERO_TOKENS = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "estimated": False,
}


@pytest.fixture(autouse=True)
def _close_logging():
    yield
    reset_logging()


def _write_corpus(directory: Path) -> None:
    (directory / "a.txt").write_text(CLEAN, encoding="utf-8")
    (directory / "b.txt").write_text(WAGE, encoding="utf-8")


class FlakyClient:
    def __init__(self):
        self.calls = 0

    def call_llm(self, prompt):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom")
        return {"risks": [], "other_observations": []}


class FailingLLM:
    def call_llm(self, prompt):
        return {
            "risks": [],
            "other_observations": [],
            "llm_failed": True,
            "error": "chaos",
        }


def test_batch_matches_single_runs_and_sums_zero_tokens(tmp_path):
    _write_corpus(tmp_path)
    client = FakeLLM({"risks": [], "other_observations": []})

    summary = run_batch(
        tmp_path,
        client,
        workers=2,
        batch_run_id="batch_test",
        parsers={"txt": TxtParser()},
    )

    assert summary["ok"] == 2
    assert summary["failed"] == 0
    assert summary["llm_failed"] == 0
    assert summary["tokens"] == ZERO_TOKENS

    for item in summary["items"]:
        path = tmp_path / item["file"]
        text = clean_text(TxtParser().parse(path.name, path.read_bytes()))
        single = run_full_review(
            text,
            extract_fields(text),
            FakeLLM({"risks": [], "other_observations": []}),
            run_id=item["run_id"],
        )
        assert json.dumps(item["report"], ensure_ascii=False, indent=2) == (
            json.dumps(single, ensure_ascii=False, indent=2)
        )


def test_batch_isolates_a_client_exception(tmp_path, caplog):
    _write_corpus(tmp_path)

    with caplog.at_level(logging.WARNING):
        summary = run_batch(
            tmp_path,
            FlakyClient(),
            workers=1,
            batch_run_id="batch_test",
            parsers={"txt": TxtParser()},
        )

    assert summary["ok"] == 1
    assert summary["failed"] == 1
    assert summary["llm_failed"] == 0
    assert any(
        getattr(record, "event", None) == "batch.item_failed"
        for record in caplog.records
    )


def test_batch_marks_llm_failed_without_crashing(tmp_path):
    _write_corpus(tmp_path)

    summary = run_batch(
        tmp_path,
        FailingLLM(),
        workers=2,
        batch_run_id="batch_test",
        parsers={"txt": TxtParser()},
    )

    assert summary["ok"] == 0
    assert summary["llm_failed"] == 2
    assert summary["failed"] == 0


def test_batch_summary_sums_tokens(tmp_path):
    _write_corpus(tmp_path)
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
        "estimated": False,
    }
    client = FakeLLM(
        {
            "risks": [],
            "other_observations": [],
            "_usage": usage,
        }
    )

    summary = run_batch(
        tmp_path,
        client,
        workers=2,
        batch_run_id="batch_test",
        parsers={"txt": TxtParser()},
    )

    assert summary["tokens"] == {
        "prompt_tokens": 20,
        "completion_tokens": 4,
        "total_tokens": 24,
        "estimated": False,
    }
    assert all(
        item["report"]["meta"]["tokens"] == usage
        for item in summary["items"]
    )


def test_batch_cli_runs_with_fake_client(
    tmp_path, monkeypatch, capsys
):
    _write_corpus(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "cli.DeepSeekLLMClient",
        lambda **kwargs: FakeLLM(
            {"risks": [], "other_observations": []}
        ),
    )

    assert (
        main(
            [
                "batch",
                str(tmp_path),
                "--workers",
                "2",
                "--rps",
                "100",
                "--max-inflight",
                "2",
            ]
        )
        == 0
    )

    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] == 2
    assert summary["failed"] == 0
    assert len(summary["items"]) == 2
