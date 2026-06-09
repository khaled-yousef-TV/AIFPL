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
        kwargs = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens or self.config.max_output_tokens,
            "temperature": 0.3,
        }

        if self._supports_json_mode:
            try:
                response = client.chat.completions.create(
                    response_format={"type": "json_object"}, **kwargs
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

        response = client.chat.completions.create(**kwargs)
        return self._extract(response)

    @staticmethod
    def _extract(response) -> Tuple[str, dict]:
        content = response.choices[0].message.content or ""
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) if response.usage else 0,
            "completion_tokens": getattr(response.usage, "completion_tokens", 0) if response.usage else 0,
        }
        return content, usage
