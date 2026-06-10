"""
Betting agent.

Wraps the existing BettingOddsClient (The Odds API) and emits market
probabilities for next-GW fixtures: win/BTTS/clean-sheet per side and
anytime-scorer estimates for the candidate pool, plus "edges" where the
market disagrees materially with the model's predictions.

Degrades cleanly (status="degraded", empty payload) when odds are
disabled — never blocks the signal run.
"""

import logging
from typing import Tuple

from pydantic import BaseModel

from constants import PlayerPosition

from .base import AgentContext, BaseAgent
from .schemas import BettingSignals, FixtureOdds, MarketEdge, PlayerScorerOdds

logger = logging.getLogger(__name__)

# Edge thresholds: market vs model disagreement worth flagging
MARKET_HIGH_PROB = 0.45   # market very confident player scores...
MODEL_LOW_POINTS = 4.0    # ...but model predicts few points
MARKET_LOW_PROB = 0.15    # market sceptical player scores...
MODEL_HIGH_POINTS = 6.0   # ...but model predicts a big haul


class BettingAgent(BaseAgent):
    name = "betting"

    def _build(self, ctx: AgentContext) -> Tuple[str, BaseModel, str]:
        boc = ctx.betting_odds_client
        if boc is None or not boc.enabled:
            return (
                "Betting odds disabled (no THE_ODDS_API_KEY / BETTING_ODDS_ENABLED).",
                BettingSignals(enabled=False),
                "degraded",
            )

        client = ctx.fpl_client
        next_gw = client.get_next_gameweek()
        fixtures = client.get_fixtures(gameweek=next_gw.id if next_gw else None)
        short_names = {t.id: t.short_name for t in client.get_teams()}

        all_odds_data = boc._fetch_all_odds()
        if not all_odds_data:
            return (
                "Betting odds enabled but no odds data returned by provider.",
                BettingSignals(enabled=True),
                "degraded",
            )

        fixture_entries = []
        odds_by_team = {}  # team_id -> (odds_dict, is_home)
        for f in fixtures:
            home = short_names.get(f.team_h, "???")
            away = short_names.get(f.team_a, "???")
            odds = boc.get_fixture_odds(home, away, all_odds_data)
            if not odds:
                continue
            fixture_entries.append(FixtureOdds(
                home_team=home,
                away_team=away,
                home_win_prob=odds.get("home_win_prob"),
                away_win_prob=odds.get("away_win_prob"),
                btts_prob=odds.get("btts_prob"),
                home_clean_sheet_prob=round(boc.get_clean_sheet_probability(True, odds), 3),
                away_clean_sheet_prob=round(boc.get_clean_sheet_probability(False, odds), 3),
            ))
            odds_by_team[f.team_h] = (odds, True)
            odds_by_team[f.team_a] = (odds, False)

        # Anytime-scorer estimates for the candidate pool (attackers only)
        scorer_entries = []
        edges = []
        try:
            from services.prediction_service import compute_predictions
            candidates = compute_predictions()[:ctx.top_n]
        except Exception as e:
            logger.warning(f"Betting agent: predictions unavailable ({e})")
            candidates = []

        players_by_id = {p.id: p for p in client.get_players()}
        for cand in candidates:
            pl = players_by_id.get(cand["id"])
            if not pl or pl.element_type not in (PlayerPosition.MID, PlayerPosition.FWD):
                continue
            entry = odds_by_team.get(pl.team)
            if not entry:
                continue
            odds, _is_home = entry

            games_played = max(1.0, pl.minutes / 90.0) if pl.minutes > 0 else 1.0
            prob = boc.get_player_goalscorer_odds(
                pl.web_name, odds,
                {
                    "goals_per_game": pl.goals_scored / games_played,
                    "xg_per_game": float(pl.expected_goals) / games_played,
                    "position": pl.element_type,
                    "is_premium": pl.price >= 9.0,
                },
            )
            if prob <= 0:
                continue

            team_short = short_names.get(pl.team, "???")
            scorer_entries.append(PlayerScorerOdds(
                id=pl.id, name=pl.web_name, team=team_short,
                anytime_scorer_prob=round(prob, 3),
            ))

            predicted = cand["predicted_points"]
            if prob >= MARKET_HIGH_PROB and predicted < MODEL_LOW_POINTS:
                edges.append(MarketEdge(
                    id=pl.id, name=pl.web_name, team=team_short,
                    direction="market_higher",
                    note=f"Market scorer prob {prob:.0%} vs model {predicted:.1f} xPts",
                ))
            elif prob <= MARKET_LOW_PROB and predicted >= MODEL_HIGH_POINTS:
                edges.append(MarketEdge(
                    id=pl.id, name=pl.web_name, team=team_short,
                    direction="market_lower",
                    note=f"Market scorer prob only {prob:.0%} vs model {predicted:.1f} xPts",
                ))

        scorer_entries.sort(key=lambda s: s.anytime_scorer_prob, reverse=True)

        payload = BettingSignals(
            enabled=True,
            fixtures=fixture_entries,
            scorer_odds=scorer_entries,
            edges=edges,
        )

        status = "ok" if fixture_entries else "degraded"
        top_scorers = ", ".join(
            f"{s.name} ({s.anytime_scorer_prob:.0%})" for s in scorer_entries[:3]
        )
        summary = (
            f"Odds for {len(fixture_entries)}/{len(fixtures)} GW{ctx.gameweek} fixtures. "
            f"Top market scorers: {top_scorers or 'n/a'}. "
            f"{len(edges)} market-vs-model edges."
        )
        return summary, payload, status
