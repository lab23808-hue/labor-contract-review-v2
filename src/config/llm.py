import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    thinking: str
    timeout: float = 60.0
    backoff_base: float = 1.0


def get_llm_config() -> LLMConfig:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "缺少 DEEPSEEK_API_KEY。请在项目根目录的 .env 中填写：\n"
            "  DEEPSEEK_API_KEY=你的 API Key\n"
            "可参考 .env.example；保存后重试。"
        )
    return LLMConfig(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        thinking=os.getenv("DEEPSEEK_THINKING", "disabled"),
        timeout=float(os.getenv("DEEPSEEK_TIMEOUT", "60")),
        backoff_base=float(os.getenv("DEEPSEEK_BACKOFF_BASE", "1")),
    )
