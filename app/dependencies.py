from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.fake import DemoLLM
from app.llm.openai_client import OpenAIProvider


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_mode == "real":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when LLM_MODE=real")
        return OpenAIProvider(settings.openai_api_key, settings.openai_model)
    return DemoLLM()
