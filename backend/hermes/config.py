"""
Hermes configuration from environment variables.

Provider-agnostic: any OpenAI-compatible API works (Nous Research,
OpenRouter, DeepSeek) by pointing LLM_BASE_URL at it.

Two ways to configure the LLM:
1. Legacy single-provider: LLM_BASE_URL / LLM_MODEL / LLM_API_KEY.
2. Named provider blocks + live switch: DEEPSEEK_* and OPENAI_* env vars,
   with the active one chosen by the `llm_provider` DB setting (toggleable
   from the UI without a restart) or the LLM_PROVIDER env var.
"""

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

# Named providers -> env var prefix. Extend here to add more.
LLM_PROVIDERS: Dict[str, str] = {
    "deepseek": "DEEPSEEK",
    "openai": "OPENAI",
}


def _provider_vars(provider: str) -> Dict[str, Optional[str]]:
    prefix = LLM_PROVIDERS[provider]
    return {
        "base_url": os.getenv(f"{prefix}_BASE_URL"),
        "model": os.getenv(f"{prefix}_MODEL"),
        "api_key": os.getenv(f"{prefix}_API_KEY"),
    }


def _provider_configured(provider: str) -> bool:
    v = _provider_vars(provider)
    return bool(v["base_url"] and v["model"] and v["api_key"])


def get_active_provider() -> Optional[str]:
    """
    The active named provider: DB setting wins (live-toggleable from the
    UI), then LLM_PROVIDER env. None means legacy LLM_* vars are used.
    """
    chosen = None
    try:
        from services.dependencies import get_dependencies
        chosen = get_dependencies().db_manager.get_setting("llm_provider")
    except Exception:
        pass  # dependencies not initialized yet (early startup) — env only
    chosen = (chosen or os.getenv("LLM_PROVIDER") or "").lower() or None
    if chosen in LLM_PROVIDERS and _provider_configured(chosen):
        return chosen
    return None


def list_llm_providers() -> List[Dict]:
    """Configured status per named provider (never exposes keys)."""
    return [
        {
            "id": name,
            "model": _provider_vars(name)["model"],
            "configured": _provider_configured(name),
        }
        for name in LLM_PROVIDERS
    ]


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
    provider = get_active_provider()
    llm = _provider_vars(provider) if provider else {
        "base_url": os.getenv("LLM_BASE_URL"),
        "model": os.getenv("LLM_MODEL"),
        "api_key": os.getenv("LLM_API_KEY"),
    }
    return HermesConfig(
        enabled=os.getenv("HERMES_ENABLED", "true").lower() == "true",
        base_url=llm["base_url"],
        model=llm["model"],
        api_key=llm["api_key"],
        max_output_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2000")),
        timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
        two_pass=os.getenv("HERMES_TWO_PASS", "false").lower() == "true",
        daily_briefing=os.getenv("HERMES_DAILY_BRIEFING", "false").lower() == "true",
    )
