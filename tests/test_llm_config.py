import pytest

from config.llm import get_llm_config


def test_missing_key_raises_with_guidance(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        get_llm_config()

    message = str(excinfo.value)
    assert "DEEPSEEK_API_KEY" in message
    assert ".env" in message
    assert ".env.example" in message


def test_reads_values_from_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.test")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    monkeypatch.setenv("DEEPSEEK_THINKING", "enabled")

    config = get_llm_config()

    assert config.api_key == "test-key"
    assert config.base_url == "https://example.test"
    assert config.model == "test-model"
    assert config.thinking == "enabled"
