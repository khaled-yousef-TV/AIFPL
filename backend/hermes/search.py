"""
Web search providers for the news agent.

SearchProvider protocol: search(query, max_results) -> list of
{title, url, snippet}. Tavily (REST, no SDK) is the default; DuckDuckGo
is a keyless degraded fallback; NullProvider disables search cleanly.
"""

import logging
import os
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)


class NullProvider:
    """No search configured — news agent degrades to FPL news fields."""
    name = "none"
    available = False

    def search(self, query: str, max_results: int = 5) -> List[Dict]:
        return []


class TavilyProvider:
    """Tavily search via plain REST (https://docs.tavily.com)."""
    name = "tavily"
    ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.available = bool(api_key)

    def search(self, query: str, max_results: int = 5) -> List[Dict]:
        try:
            response = requests.post(
                self.ENDPOINT,
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                    "include_answer": False,
                },
                timeout=15,
            )
            response.raise_for_status()
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": (r.get("content") or "")[:500],
                }
                for r in response.json().get("results", [])
            ]
        except Exception as e:
            logger.warning(f"Tavily search failed for '{query}': {e}")
            return []


class DuckDuckGoProvider:
    """Keyless fallback via the duckduckgo-search package (brittle, best-effort)."""
    name = "duckduckgo"

    def __init__(self):
        try:
            from duckduckgo_search import DDGS  # noqa: F401
            self.available = True
        except ImportError:
            self.available = False

    def search(self, query: str, max_results: int = 5) -> List[Dict]:
        if not self.available:
            return []
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                return [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": (r.get("body") or "")[:500],
                    }
                    for r in ddgs.text(query, max_results=max_results)
                ]
        except Exception as e:
            logger.warning(f"DuckDuckGo search failed for '{query}': {e}")
            return []


def load_search_provider():
    """Build the configured search provider (SEARCH_PROVIDER env, default tavily)."""
    name = os.getenv("SEARCH_PROVIDER", "tavily").lower()

    if name == "tavily":
        key = os.getenv("TAVILY_API_KEY")
        if key:
            return TavilyProvider(key)
        logger.info("SEARCH_PROVIDER=tavily but TAVILY_API_KEY unset — trying DuckDuckGo fallback")
        ddg = DuckDuckGoProvider()
        return ddg if ddg.available else NullProvider()

    if name == "duckduckgo":
        ddg = DuckDuckGoProvider()
        return ddg if ddg.available else NullProvider()

    return NullProvider()
