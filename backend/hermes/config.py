"""
Hermes configuration from environment variables.

Provider-agnostic: any OpenAI-compatible API works (Nous Research,
OpenRouter, DeepSeek) by pointing LLM_BASE_URL at it.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HermesConfig:
    enabled: bool
    base_url: Optional[str]
    model: Optional[str]
    api_key: Optional[str]
    max_output_tokens: int
    timeout_seconds: int
    two_pass: bool
    daily_briefing: bool

    @property
    def llm_configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.model and self.api_key)


def load_hermes_config() -> HermesConfig:
    return HermesConfig(
        enabled=os.getenv("HERMES_ENABLED", "true").lower() == "true",
        base_url=os.getenv("LLM_BASE_URL"),
        model=os.getenv("LLM_MODEL"),
        api_key=os.getenv("LLM_API_KEY"),
        max_output_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2000")),
        timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
        two_pass=os.getenv("HERMES_TWO_PASS", "false").lower() == "true",
        daily_briefing=os.getenv("HERMES_DAILY_BRIEFING", "false").lower() == "true",
    )
