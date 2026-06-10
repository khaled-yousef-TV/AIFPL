"""
News & sentiment agent (the only LLM-powered agent).

Hunts two kinds of signal:
1. Hard news: injuries, suspensions, press-conference team news.
2. Player incentives ("mental goals"): record chases, Golden Boot races,
   milestones, contract years, call-up pushes — narratives that change
   on-pitch behavior (e.g. Bruno Fernandes chasing the assist record
   => passes more => assist potential up).

Degrades in layers: no search -> FPL news fields + LLM; no LLM ->
deterministic conversion of FPL news fields only.
"""

import json
import logging
from typing import List, Tuple

from pydantic import BaseModel

from .availability_agent import NON_AVAILABLE_STATUSES
from .base import AgentContext, BaseAgent
from .schemas import NewsItem, NewsSignals

logger = logging.getLogger(__name__)

MAX_SEARCH_QUERIES = 8
MAX_ITEMS = 15

NEWS_SYSTEM_PROMPT = """You are an FPL news analyst. Given search results and official FPL \
injury notes, extract items relevant to Fantasy Premier League decisions for the upcoming \
gameweek.

Look for BOTH:
- Hard news: injuries, suspensions, returns, confirmed lineups, manager quotes about minutes.
- Player incentives ("mental goals") that change behavior: a player chasing an assist/goal \
record, the Golden Boot race, a scoring milestone, contract-year motivation, playing for a \
transfer or a national-team call-up, revenge games vs former clubs. For each incentive, state \
the behavioral implication (e.g. "chasing the assist record - will look to pass more").

Respond with ONLY a JSON object:
{"items": [{"player_name": str|null, "team": str|null, "headline": str, "summary": str,
"sentiment": float (-1..1), "impact": "out|doubt|boost|neutral|incentive",
"incentive_type": "record_chase|golden_boot|milestone|contract|call_up|revenge|other"|null,
"behavioral_implication": str|null, "source_url": str|null}]}
Only include items genuinely useful for FPL decisions. Max 15 items."""


def build_search_queries(top_player_names: List[str], flagged_names: List[str]) -> List[str]:
    """Bounded query set: hard news + incentive-oriented searches."""
    queries = [
        "Premier League injury news team news this week",
        "Premier League press conference team news",
        # Incentive hunting (user requirement: "mental goals")
        "Premier League Golden Boot race",
        "Premier League assist record chase",
        "Premier League player chasing record milestone",
    ]
    # Targeted searches for the most relevant individuals
    for name in flagged_names[:2]:
        queries.append(f"{name} injury return news")
    for name in top_player_names[:1]:
        queries.append(f"{name} form record news")
    return queries[:MAX_SEARCH_QUERIES]


def fpl_news_fallback(ctx: AgentContext) -> List[NewsItem]:
    """Deterministic items from the official FPL news field (no LLM/search)."""
    items = []
    teams = {t.id: t.short_name for t in ctx.fpl_client.get_teams()}
    for p in ctx.fpl_client.get_players():
        if not p.news:
            continue
        if p.minutes == 0 and float(p.selected_by_percent) < 1.0:
            continue
        impact = "out" if p.status in NON_AVAILABLE_STATUSES else "doubt"
        if p.status == "a":
            impact = "neutral"
        items.append(NewsItem(
            player_id=p.id,
            team=teams.get(p.team),
            headline=f"{p.web_name}: {p.news[:80]}",
            summary=p.news[:200],
            sentiment=-0.5 if impact in ("out", "doubt") else 0.0,
            impact=impact,
        ))
    # Hard outs first, cap the list
    items.sort(key=lambda i: 0 if i.impact == "out" else 1)
    return items[:MAX_ITEMS]


class NewsAgent(BaseAgent):
    name = "news"

    def _build(self, ctx: AgentContext) -> Tuple[str, BaseModel, str]:
        search = ctx.search_provider
        llm = ctx.llm_client

        # No LLM: deterministic FPL-news-only mode
        if llm is None:
            items = fpl_news_fallback(ctx)
            payload = NewsSignals(search_used=False, items=items)
            return (
                f"{len(items)} items from official FPL news only (LLM/search unavailable).",
                payload,
                "degraded",
            )

        # Gather context for the LLM
        players = ctx.fpl_client.get_players()
        top_names = [
            p.web_name for p in sorted(players, key=lambda x: float(x.form), reverse=True)[:10]
        ]
        flagged_names = [p.web_name for p in players if p.news and p.minutes > 0][:10]

        search_used = False
        search_blocks = []
        if search is not None and getattr(search, "available", False):
            for query in build_search_queries(top_names, flagged_names):
                results = search.search(query, max_results=4)
                if results:
                    search_used = True
                    lines = [f"### {query}"]
                    lines += [f"- {r['title']} ({r['url']}): {r['snippet']}" for r in results]
                    search_blocks.append("\n".join(lines))

        fpl_notes = [
            f"- {p.web_name} ({p.status}, chance={p.chance_of_playing_next_round}): {p.news}"
            for p in players if p.news and (p.minutes > 0 or float(p.selected_by_percent) >= 1.0)
        ][:25]

        user_prompt = (
            f"# Upcoming gameweek: GW{ctx.gameweek}\n\n"
            "## Official FPL injury/news notes\n" + ("\n".join(fpl_notes) or "none")
            + "\n\n## Web search results\n"
            + ("\n\n".join(search_blocks) or "No search results available.")
        )

        try:
            raw, _usage = llm.complete(NEWS_SYSTEM_PROMPT, user_prompt, max_tokens=3000)
            items = self._parse_items(raw, ctx)
        except Exception as e:
            logger.error(f"News agent LLM call failed: {e}", exc_info=True)
            items = fpl_news_fallback(ctx)
            payload = NewsSignals(search_used=search_used, items=items)
            return (
                f"LLM failed ({e}); {len(items)} items from official FPL news only.",
                payload,
                "degraded",
            )

        payload = NewsSignals(search_used=search_used, items=items[:MAX_ITEMS])
        incentives = sum(1 for i in items if i.impact == "incentive")
        status = "ok" if search_used else "degraded"
        summary = (
            f"{len(items)} news items ({incentives} incentive signals)"
            + ("" if search_used else " — no web search, FPL notes only")
            + "."
        )
        return summary, payload, status

    @staticmethod
    def _parse_items(raw: str, ctx: AgentContext) -> List[NewsItem]:
        """Parse LLM output, resolving player names to ids where possible."""
        from hermes.validation import extract_json_block

        data = json.loads(extract_json_block(raw))

        by_name = {}
        for p in ctx.fpl_client.get_players():
            by_name[p.web_name.lower()] = p.id
            by_name[p.full_name.lower()] = p.id

        items = []
        for entry in data.get("items", [])[:MAX_ITEMS]:
            if not isinstance(entry, dict) or not entry.get("headline"):
                continue
            player_id = None
            name = (entry.get("player_name") or "").strip().lower()
            if name:
                # Exact match on web_name or full_name only. A loose substring
                # match mis-attributes ("Son" -> "Anderson"); leaving player_id
                # None (team-level item) is safer than a wrong attribution.
                player_id = by_name.get(name)
                if player_id is None and len(name) >= 4:
                    # Allow a full-token match (e.g. "bruno" within "bruno fernandes")
                    matches = {
                        pid for known, pid in by_name.items()
                        if name in known.split()
                    }
                    if len(matches) == 1:  # unambiguous only
                        player_id = matches.pop()
            try:
                items.append(NewsItem(
                    player_id=player_id,
                    team=entry.get("team"),
                    headline=str(entry["headline"])[:160],
                    summary=str(entry.get("summary", ""))[:300],
                    sentiment=max(-1.0, min(1.0, float(entry.get("sentiment", 0) or 0))),
                    impact=entry.get("impact") if entry.get("impact") in
                           ("out", "doubt", "boost", "neutral", "incentive") else "neutral",
                    incentive_type=entry.get("incentive_type") if entry.get("incentive_type") in
                                   ("record_chase", "golden_boot", "milestone",
                                    "contract", "call_up", "revenge", "other") else None,
                    behavioral_implication=(
                        str(entry["behavioral_implication"])[:160]
                        if entry.get("behavioral_implication") else None
                    ),
                    source_url=entry.get("source_url"),
                ))
            except Exception as e:
                logger.warning(f"Skipping malformed news item: {e}")
        return items
