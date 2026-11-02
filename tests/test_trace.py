import json
from pathlib import Path

from cli import main
from infra.llm_client import FakeLLM

FIXTURES = Path(__file__).parent / "fixtures" / "realistic"

LLM_RESPONSE = {
    "risks": [
        {
            "risk_type": "wage_below_floor",
            "severity": "medium",
            "excerpt": "试用期间月工资为3200元",
            "explanation": "低于 80%",
            "is_violation": True,
        },
        {
            "risk_type": "probation_exceed",
            "severity": "high",
            "excerpt": "试用期间月工资为3200元",
            "explanation": "边界情形",
            "is_violation": False,
        },
        {
            "risk_type": "probation_exceed",
            "severity": "high",
            "excerpt": "试用期间月工资为3200元",
            "explanation": "缺少裁决字段",
        },
        {
            "risk_type": "illegal_penalty",
            "severity": "medium",
            "excerpt": "原文没有这句",
            "explanation": "excerpt 不命中",
            "is_violation": True,
        },
    ],
    "other_observations": [
        {"excerpt": "工作地点随甲方项目所在地安排", "note": "可保留"},
        {"excerpt": "原文没有这句", "note": "编造"},
    ],
}

TRACE_FILES = [
    "00_input.meta.json",
    "01_parsed.txt",
    "02_cleaned.txt",
    "03_extraction.json",
    "04_rules.json",
    "05_llm_raw.json",
    "06_llm_verified.json",
    "07_merger.json",
    "08_final_report.json",
    "trace.log",
]


def test_trace_mode_writes_all_artifacts_and_matches_plain_report(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        "app.review._default_client", lambda: FakeLLM(LLM_RESPONSE)
    )
    monkeypatch.chdir(tmp_path)
    fixture = (FIXTURES / "C-006.txt").resolve()

    assert main(["review", str(fixture)]) == 0
    plain = json.loads(capsys.readouterr().out)

    assert main(["review", str(fixture), "--trace"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == plain
    assert "[trace]" in captured.err

    trace_dirs = sorted(tmp_path.glob("trace_*"))
    assert len(trace_dirs) == 1
    trace_dir = trace_dirs[0]
    for name in TRACE_FILES:
        assert (trace_dir / name).is_file(), name

    traced = json.loads(
        (trace_dir / "08_final_report.json").read_text(encoding="utf-8")
    )
    assert traced == plain

    meta = json.loads(
        (trace_dir / "00_input.meta.json").read_text(encoding="utf-8")
    )
    assert meta == {
        "path": str(fixture),
        "size": fixture.stat().st_size,
        "suffix": ".txt",
    }

    raw = json.loads(
        (trace_dir / "05_llm_raw.json").read_text(encoding="utf-8")
    )
    assert raw == LLM_RESPONSE

    verified = json.loads(
        (trace_dir / "06_llm_verified.json").read_text(encoding="utf-8")
    )
    assert {item["reason"] for item in verified["dropped"]} == {
        "excerpt 不命中",
        "is_violation=false",
        "字段缺失",
    }

    rules = json.loads(
        (trace_dir / "04_rules.json").read_text(encoding="utf-8")
    )
    assert set(rules) == {
        "probation",
        "term_conflict",
        "social_security",
        "wage",
        "penalty",
        "noncompete",
    }
    assert len(rules["social_security"]) == 1

    merger = json.loads(
        (trace_dir / "07_merger.json").read_text(encoding="utf-8")
    )
    assert merger["report"] == plain
    actions = {item["action"] for item in merger["arbitration"]}
    assert "keep_rule" in actions
    assert "keep_llm" in actions


def test_trace_counts_only_dropped_events_as_arbitration(
    tmp_path, monkeypatch, capsys
):
    response = {
        "risks": [
            {
                "risk_type": "illegal_penalty",
                "severity": "high",
                "excerpt": (
                    "乙方在合同期内提前离职的，"
                    "应向甲方支付违约金人民币20000元"
                ),
                "explanation": "LLM 版",
                "is_violation": True,
            }
        ],
        "other_observations": [],
    }
    monkeypatch.setattr(
        "app.review._default_client", lambda: FakeLLM(response)
    )
    monkeypatch.chdir(tmp_path)
    fixture = (FIXTURES / "C-003.txt").resolve()

    assert main(["review", str(fixture), "--trace"]) == 0
    capsys.readouterr()

    trace_dir = sorted(tmp_path.glob("trace_*"))[0]
    log = (trace_dir / "trace.log").read_text(encoding="utf-8")
    assert "arbitration_events=1" in log
    assert "total_events=3" in log

    merger = json.loads(
        (trace_dir / "07_merger.json").read_text(encoding="utf-8")
    )
    actions = [item["action"] for item in merger["arbitration"]]
    assert actions.count("keep_rule") == 2
    assert actions.count("drop_llm_rule_hit") == 1
