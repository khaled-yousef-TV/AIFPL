"""
OpenAI-compatible LLM client for Hermes.

Works with any OpenAI-compatible endpoint (Nous Research, OpenRouter,
DeepSeek) via LLM_BASE_URL/LLM_MODEL/LLM_API_KEY. Tries JSON mode
(response_format=json_object) and falls back gracefully for providers
that reject it.
"""

import logging
from typing import Optional, Tuple

from .config import HermesConfig

logger = logging.getLogger(__name__)


class LLMClient:
    """Thin synchronous wrapper around an OpenAI-compatible chat API."""

    def __init__(self, config: HermesConfig):
        self.config = config
        self._client = None
        self._supports_json_mode = True  # feature-detected on first failure
        # Newer OpenAI models reject max_tokens and demand max_completion_tokens;
        # DeepSeek and most compatible providers only accept max_tokens. Seed
        # by endpoint (clients are per-run, so a wrong guess self-corrects via
        # the adaptive retry but would otherwise repeat every run).
        self._uses_max_completion_tokens = "api.openai.com" in (config.base_url or "")
        # OpenAI's reasoning-series models (gpt-5.*, o1-*, o3-*, o4-*) only
        # accept the default temperature. Seed so we don't burn a 400 per run
        # rediscovering it; adaptive retry still self-corrects for anything
        # the pattern misses.
        model = (config.model or "").lower()
        is_openai = "api.openai.com" in (config.base_url or "")
        self._supports_temperature = not (
            is_openai and (model.startswith(("gpt-5", "o1", "o3", "o4-mini", "o4")))
        )
        self._reasoning_budget_floor = 0  # raised on first reasoning-starve event

    # Reasoning models spend most of the budget on invisible reasoning
    # tokens before emitting any visible content. Give them enough headroom
    # that a briefing (target ~2000 output tokens) has room after reasoning.
    _REASONING_BUDGET_FLOOR = 12000

    def _create(self, client, **kwargs):
        """
        create() with adaptive retries for per-model parameter quirks
        (newer OpenAI models reject max_tokens and non-default temperature)
        and for reasoning models that consume the whole budget internally.
        Learned quirks stick to the client instance, which lives for a run.
        """
        for _ in range(4):
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as e:
                from openai import APIStatusError
                if not (isinstance(e, APIStatusError) and e.status_code == 400):
                    raise
                msg = str(e)
                if "max_completion_tokens" in msg and "max_tokens" in kwargs:
                    logger.warning("Model requires max_completion_tokens; retrying.")
                    self._uses_max_completion_tokens = True
                    kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                    continue
                if "'temperature'" in msg and "temperature" in kwargs:
                    logger.warning("Model only supports default temperature; retrying without it.")
                    self._supports_temperature = False
                    kwargs.pop("temperature")
                    continue
                raise

            # Reasoning-model empty-response detection: retry once with a
            # larger budget so the visible answer actually fits.
            if not self._is_reasoning_starved(response):
                return response
            new_budget = max(self._REASONING_BUDGET_FLOOR, self._current_budget(kwargs) * 3)
            self._reasoning_budget_floor = new_budget  # remember for later calls
            token_key = "max_completion_tokens" if "max_completion_tokens" in kwargs else "max_tokens"
            logger.warning(
                f"Reasoning model consumed entire {self._current_budget(kwargs)}-token budget "
                f"before emitting output; retrying at {new_budget}."
            )
            kwargs[token_key] = new_budget

        return client.chat.completions.create(**kwargs)

    @staticmethod
    def _current_budget(kwargs) -> int:
        return int(kwargs.get("max_completion_tokens") or kwargs.get("max_tokens") or 0)

    def _is_reasoning_starved(self, response) -> bool:
        """True when reasoning consumed the whole budget and left no output."""
        try:
            choice = response.choices[0]
            if (choice.message.content or "").strip():
                return False
            if getattr(choice, "finish_reason", None) != "length":
                return False
            details = getattr(response.usage, "completion_tokens_details", None)
            reasoning = getattr(details, "reasoning_tokens", 0) if details else 0
            return reasoning > 0
        except Exception:
            return False

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=self.config.base_url,
                api_key=self.config.api_key,
                timeout=self.config.timeout_seconds,
                max_retries=1,
            )
        return self._client

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
    ) -> Tuple[str, dict]:
        """
        Run a chat completion, preferring JSON mode.

        Returns:
            (content, usage) where usage = {prompt_tokens, completion_tokens}
        """
        client = self._get_client()
        token_param = (
            "max_completion_tokens" if self._uses_max_completion_tokens else "max_tokens"
        )
        budget = max(max_tokens or self.config.max_output_tokens, self._reasoning_budget_floor)
        kwargs = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            token_param: budget,
        }
        if self._supports_temperature:
            kwargs["temperature"] = 0.3

        if self._supports_json_mode:
            try:
                response = self._create(
                    client, response_format={"type": "json_object"}, **kwargs
                )
                return self._extract(response)
            except Exception as e:
                # Provider may not support response_format — fall back once
                # and remember (only for 4xx-style errors; re-raise others).
                from openai import APIStatusError
                if isinstance(e, APIStatusError) and 400 <= e.status_code < 500:
                    logger.warning(
                        f"Provider rejected json_object response_format "
                        f"({e.status_code}); falling back to plain completion."
                    )
                    self._supports_json_mode = False
                else:
                    raise

        response = self._create(client, **kwargs)
        return self._extract(response)

    @staticmethod
    def _extract(response) -> Tuple[str, dict]:
        choice = response.choices[0]
        content = choice.message.content or ""
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) if response.usage else 0,
            "completion_tokens": getattr(response.usage, "completion_tokens", 0) if response.usage else 0,
        }
        # Reasoning models can consume the whole budget as invisible reasoning
        # tokens and return empty content — surface that so the run doesn't
        # just look like "the model wrote nothing".
        if not content.strip():
            details = getattr(response.usage, "completion_tokens_details", None) if response.usage else None
            reasoning = getattr(details, "reasoning_tokens", None) if details else None
            finish = getattr(choice, "finish_reason", None)
            logger.warning(
                f"LLM returned empty content (finish_reason={finish}, "
                f"completion_tokens={usage['completion_tokens']}, reasoning_tokens={reasoning}). "
                f"If reasoning_tokens is high, raise LLM_MAX_OUTPUT_TOKENS."
            )
        return content, usage
