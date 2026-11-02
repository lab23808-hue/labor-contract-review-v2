import logging

from domain.merge.merger import merge_review


def _rule(risk_type: str, severity: str = "high") -> dict:
    return {
        "source": "rules",
        "risk_type": risk_type,
        "severity": severity,
        "excerpt": f"{risk_type}-原文",
        "explanation": "规则版",
    }


def _llm(risk_type: str, severity: str = "low") -> dict:
    return {
        "risk_type": risk_type,
        "severity": severity,
        "excerpt": f"{risk_type}-LLM",
        "explanation": "LLM 版",
    }


def test_rule_wins_on_dual_source_and_logs_dropped_llm(caplog):
    with caplog.at_level(logging.WARNING):
        report = merge_review(
            [_rule("probation_exceed", "high")],
            {"risks": [_llm("probation_exceed", "medium")], "other_observations": []},
        )

    assert len(report["risks"]) == 1
    assert report["risks"][0]["source"] == "rules"
    assert report["risks"][0]["severity"] == "high"
    assert "同 risk_type 双源命中" in caplog.text
    assert "probation_exceed-LLM" in caplog.text


def test_rule_only_single_source_kept():
    report = merge_review(
        [_rule("social_security_missing", "high")],
        {"risks": [], "other_observations": []},
    )

    assert report["risks"] == [_rule("social_security_missing", "high")]
    assert report["meta"]["sources"] == {"rules": 1, "llm": 0}


def test_llm_only_single_source_kept():
    report = merge_review(
        [],
        {"risks": [_llm("wage_below_floor", "medium")], "other_observations": []},
    )

    assert report["risks"] == [
        {**_llm("wage_below_floor", "medium"), "source": "llm"}
    ]
    assert report["meta"]["sources"] == {"rules": 0, "llm": 1}


def test_severity_follows_winner():
    report = merge_review(
        [_rule("probation_exceed", "high")],
        {
            "risks": [
                _llm("probation_exceed", "low"),
                _llm("wage_below_floor", "low"),
            ],
            "other_observations": [],
        },
    )
    by_type = {risk["risk_type"]: risk for risk in report["risks"]}

    assert by_type["probation_exceed"]["severity"] == "high"
    assert by_type["wage_below_floor"]["severity"] == "low"


def test_llm_failed_keeps_rules_and_marks_meta(caplog):
    with caplog.at_level(logging.WARNING):
        report = merge_review(
            [_rule("probation_exceed", "high")],
            {
                "risks": [],
                "other_observations": [],
                "llm_failed": True,
                "error": "bad json",
            },
        )

    assert [risk["risk_type"] for risk in report["risks"]] == [
        "probation_exceed"
    ]
    assert report["meta"]["llm_failed"] is True
    assert "LLM 分支失败" in caplog.text


def test_observations_passthrough_with_source_and_no_dedup():
    observations = [
        {"excerpt": "同一句", "note": "第一条"},
        {"excerpt": "同一句", "note": "第二条"},
    ]

    report = merge_review(
        [], {"risks": [], "other_observations": observations}
    )

    assert report["other_observations"] == [
        {"excerpt": "同一句", "note": "第一条", "source": "llm"},
        {"excerpt": "同一句", "note": "第二条", "source": "llm"},
    ]
    assert report["risks"] == []


def test_sorting_by_severity_then_risk_type():
    report = merge_review(
        [
            _rule("zeta", "medium"),
            _rule("alpha", "high"),
            _rule("beta", "low"),
        ],
        {"risks": [_llm("gamma", "high")], "other_observations": []},
    )

    assert [risk["risk_type"] for risk in report["risks"]] == [
        "alpha",
        "gamma",
        "zeta",
        "beta",
    ]


def test_meta_counts():
    report = merge_review(
        [_rule("a", "high"), _rule("b", "medium")],
        {
            "risks": [_llm("c", "low"), _llm("a", "low")],
            "other_observations": [],
        },
    )

    assert report["meta"] == {
        "llm_failed": False,
        "risk_count": 3,
        "sources": {"rules": 2, "llm": 1},
    }


def test_llm_entries_of_same_type_are_all_kept():
    llm_risks = [
        {
            "risk_type": "disguised_waiver",
            "severity": "high",
            "excerpt": "第4条",
            "explanation": "放弃社保",
        },
        {
            "risk_type": "disguised_waiver",
            "severity": "high",
            "excerpt": "第5条",
            "explanation": "伤亡免责",
        },
        {
            "risk_type": "disguised_waiver",
            "severity": "high",
            "excerpt": "第6条",
            "explanation": "自愿加班",
        },
    ]

    report = merge_review(
        [], {"risks": llm_risks, "other_observations": []}
    )

    assert [risk["excerpt"] for risk in report["risks"]] == [
        "第4条",
        "第5条",
        "第6条",
    ]
    assert [risk["source"] for risk in report["risks"]] == ["llm"] * 3
    assert report["meta"] == {
        "llm_failed": False,
        "risk_count": 3,
        "sources": {"rules": 0, "llm": 3},
    }


def test_rule_wins_discards_every_llm_entry_of_that_type(caplog):
    llm_risks = [
        {
            "risk_type": "probation_exceed",
            "severity": "medium",
            "excerpt": "第一条",
            "explanation": "LLM 版一",
        },
        {
            "risk_type": "probation_exceed",
            "severity": "low",
            "excerpt": "第二条",
            "explanation": "LLM 版二",
        },
    ]

    with caplog.at_level(logging.WARNING):
        report = merge_review(
            [_rule("probation_exceed", "high")],
            {"risks": llm_risks, "other_observations": []},
        )

    assert len(report["risks"]) == 1
    assert report["risks"][0]["source"] == "rules"
    assert caplog.text.count("同 risk_type 双源命中") == 2


def test_multiple_rule_entries_of_same_type_are_all_kept(caplog):
    rules = [
        {
            "source": "rules",
            "risk_type": "illegal_penalty",
            "severity": "medium",
            "excerpt": "第一条违约金",
            "explanation": "规则版一",
        },
        {
            "source": "rules",
            "risk_type": "illegal_penalty",
            "severity": "medium",
            "excerpt": "第二条违约金",
            "explanation": "规则版二",
        },
    ]
    llm_risks = [
        {
            "risk_type": "illegal_penalty",
            "severity": "high",
            "excerpt": "LLM 版",
            "explanation": "LLM 版",
            "is_violation": True,
        }
    ]

    with caplog.at_level(logging.WARNING):
        report = merge_review(
            rules, {"risks": llm_risks, "other_observations": []}
        )

    assert [risk["excerpt"] for risk in report["risks"]] == [
        "第一条违约金",
        "第二条违约金",
    ]
    assert [risk["source"] for risk in report["risks"]] == ["rules", "rules"]
    assert report["meta"] == {
        "llm_failed": False,
        "risk_count": 2,
        "sources": {"rules": 2, "llm": 0},
    }
    assert caplog.text.count("同 risk_type 双源命中") == 1
