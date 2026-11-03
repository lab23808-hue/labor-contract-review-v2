import logging

import httpx
import pytest
from openai import RateLimitError

from config.llm import LLMConfig
from infra.llm_client import DeepSeekLLMClient, LLMTransientError


def _client(client=None, **kwargs):
    return DeepSeekLLMClient(
        config=LLMConfig(
            api_key="test",
            base_url="http://localhost",
            model="fake",
            thinking="disabled",
        ),
        backoff_base=0,
        jitter=0,
        client=client if client is not None else object(),
        **kwargs,
    )


def _reasons(caplog):
    return [
        record.reason
        for record in caplog.records
        if getattr(record, "event", None) == "llm.call_failed"
    ]


def test_request_maps_rate_limit_error_to_429():
    request = httpx.Request("POST", "http://localhost")
    response = httpx.Response(429, request=request)
    error = RateLimitError("429", response=response, body=None)

    class Completions:
        def create(self, **kwargs):
            raise error

    class Chat:
        completions = Completions()

    class FakeOpenAI:
        chat = Chat()

    client = _client(client=FakeOpenAI())

    with pytest.raises(LLMTransientError) as exc:
        client._request("prompt")

    assert exc.value.reason == "429"


def test_429_triggers_retry_then_failure(monkeypatch, caplog):
    client = _client()

    def fail(prompt):
        raise LLMTransientError("429")

    monkeypatch.setattr(client, "_request", fail)

    with caplog.at_level(logging.INFO):
        result = client.call_llm("prompt")

    assert result["llm_failed"] is True
    events = [getattr(record, "event", None) for record in caplog.records]
    assert events.count("llm.call_start") == 2
    assert events.count("llm.call_failed") == 2
    assert events.count("llm.retry") == 1
    assert _reasons(caplog) == ["429", "429"]
    assert result["_usage"]["total_tokens"] == 0


def test_empty_response_triggers_retry_then_failure(monkeypatch, caplog):
    client = _client()
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 0,
        "total_tokens": 10,
        "estimated": False,
    }
    monkeypatch.setattr(client, "_request", lambda prompt: ("", usage))

    with caplog.at_level(logging.INFO):
        result = client.call_llm("prompt")

    assert result["llm_failed"] is True
    assert _reasons(caplog) == ["empty_response", "empty_response"]
    assert result["_usage"] == usage


def test_timeout_triggers_retry_then_failure(monkeypatch, caplog):
    client = _client()

    def fail(prompt):
        raise LLMTransientError("timeout")

    monkeypatch.setattr(client, "_request", fail)

    with caplog.at_level(logging.INFO):
        result = client.call_llm("prompt")

    assert result["llm_failed"] is True
    assert _reasons(caplog) == ["timeout", "timeout"]


def test_call_done_and_result_carry_usage(monkeypatch, caplog):
    client = _client()
    usage = {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
        "estimated": False,
    }
    payload = '{"risks": [], "other_observations": []}'
    monkeypatch.setattr(
        client, "_request", lambda prompt: (payload, usage)
    )

    with caplog.at_level(logging.INFO):
        result = client.call_llm("prompt")

    assert result["_usage"] == usage
    done = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "llm.call_done"
    ]
    assert done[0].total_tokens == 17


def test_chaos_injects_timeout(monkeypatch, caplog):
    client = _client(chaos=True, chaos_rate=1.0)
    monkeypatch.setattr(
        "infra.llm_client.random.choice", lambda choices: "timeout"
    )

    with caplog.at_level(logging.INFO):
        result = client.call_llm("prompt")

    assert result["llm_failed"] is True
    assert _reasons(caplog) == ["timeout", "timeout"]
