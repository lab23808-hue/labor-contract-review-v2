import json
import logging
import random
import time
from typing import Any, Optional

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from config.llm import LLMConfig, get_llm_config
from infra.rate_limiter import RequestThrottle

logger = logging.getLogger(__name__)

_SYSTEM_MARKER = "[SYSTEM]\n"
_USER_SEPARATOR = "\n\n[USER]\n"
_THINKING_MODES = {"enabled", "disabled"}


class LLMTransientError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DeepSeekLLMClient:
    def __init__(
        self,
        config: Optional[LLMConfig] = None,
        throttle: Optional[RequestThrottle] = None,
        chaos: bool = False,
        chaos_rate: float = 0.2,
        backoff_base: Optional[float] = None,
        jitter: float = 0.5,
        client: Any = None,
    ) -> None:
        self._config = config or get_llm_config()
        self._client = (
            client
            if client is not None
            else OpenAI(
                api_key=self._config.api_key,
                base_url=self._config.base_url,
            )
        )
        self._throttle = throttle
        self._chaos = chaos
        self._chaos_rate = chaos_rate
        self._backoff_base = (
            self._config.backoff_base
            if backoff_base is None
            else backoff_base
        )
        self._jitter = jitter

    def call_llm(self, prompt: str) -> dict[str, Any]:
        last_error = ""
        last_reason = ""
        last_usage = _zero_usage()

        for attempt in (1, 2):
            started = time.monotonic()
            logger.info(
                "",
                extra={
                    "event": "llm.call_start",
                    "attempt": attempt,
                    "model": self._config.model,
                },
            )
            usage = _zero_usage()
            try:
                content, usage = self._request(prompt)
                last_usage = usage
                if not content or not content.strip():
                    raise LLMTransientError("empty_response")
                parsed = json.loads(content)
            except LLMTransientError as exc:
                elapsed = round(time.monotonic() - started, 3)
                last_error = exc.reason
                last_reason = exc.reason
                logger.warning(
                    "",
                    extra={
                        "event": "llm.call_failed",
                        "attempt": attempt,
                        "elapsed": elapsed,
                        "reason": exc.reason,
                        "error": exc.reason,
                        **usage,
                    },
                )
                if attempt == 1:
                    self._log_and_sleep_retry(exc.reason, attempt)
            except json.JSONDecodeError as exc:
                elapsed = round(time.monotonic() - started, 3)
                last_error = f"invalid_json: {exc}"
                last_reason = "invalid_json"
                logger.warning(
                    "",
                    extra={
                        "event": "llm.call_failed",
                        "attempt": attempt,
                        "elapsed": elapsed,
                        "reason": "invalid_json",
                        "error": last_error,
                        **usage,
                    },
                )
                if attempt == 1:
                    self._log_and_sleep_retry("invalid_json", attempt)
            except Exception as exc:
                elapsed = round(time.monotonic() - started, 3)
                logger.warning(
                    "",
                    extra={
                        "event": "llm.call_failed",
                        "attempt": attempt,
                        "elapsed": elapsed,
                        "reason": "unexpected",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                raise
            else:
                elapsed = round(time.monotonic() - started, 3)
                logger.info(
                    "",
                    extra={
                        "event": "llm.call_done",
                        "attempt": attempt,
                        "elapsed": elapsed,
                        **usage,
                    },
                )
                return {**parsed, "_usage": usage}

        logger.error(
            "",
            extra={
                "event": "llm.failed",
                "error": last_error,
                "reason": last_reason,
                **last_usage,
            },
        )
        return {
            "risks": [],
            "other_observations": [],
            "llm_failed": True,
            "error": last_error,
            "_usage": last_usage,
        }

    def _log_and_sleep_retry(self, reason: str, attempt: int) -> None:
        delay = min(8.0, self._backoff_base * (2 ** (attempt - 1)))
        delay += random.uniform(0, self._jitter)
        logger.warning(
            "",
            extra={
                "event": "llm.retry",
                "attempt": attempt + 1,
                "reason": reason,
                "delay": round(delay, 3),
            },
        )
        time.sleep(delay)

    def _request(self, prompt: str) -> tuple[str, dict[str, Any]]:
        if self._chaos and random.random() < self._chaos_rate:
            injection = random.choice(
                ("timeout", "invalid_json", "connection")
            )
            if injection == "invalid_json":
                return "{invalid json", _zero_usage()
            raise LLMTransientError(injection)

        system, user = _split_prompt(prompt)
        if self._throttle is not None:
            self._throttle.acquire()
        try:
            response = self._client.chat.completions.create(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                response_format={"type": "json_object"},
                timeout=self._config.timeout,
                extra_body={
                    "thinking": {"type": _thinking_mode(self._config.thinking)}
                },
            )
        except RateLimitError as exc:
            raise LLMTransientError("429") from exc
        except APITimeoutError as exc:
            raise LLMTransientError("timeout") from exc
        except APIConnectionError as exc:
            raise LLMTransientError("connection") from exc
        finally:
            if self._throttle is not None:
                self._throttle.release()

        content = response.choices[0].message.content
        return content or "", _usage_from(response)


class FakeLLM:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def call_llm(self, prompt: str) -> dict[str, Any]:
        return self._response


def _usage_from(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return _zero_usage(estimated=True)
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(
            getattr(usage, "completion_tokens", 0) or 0
        ),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        "estimated": False,
    }


def _zero_usage(estimated: bool = False) -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated": estimated,
    }


def _split_prompt(prompt: str) -> tuple[str, str]:
    rest = (
        prompt[len(_SYSTEM_MARKER) :]
        if prompt.startswith(_SYSTEM_MARKER)
        else prompt
    )
    system, separator, user = rest.partition(_USER_SEPARATOR)
    if not separator:
        return "", prompt
    return system.strip(), user


def _thinking_mode(value: str) -> str:
    mode = (value or "disabled").strip().lower()
    return mode if mode in _THINKING_MODES else "disabled"
