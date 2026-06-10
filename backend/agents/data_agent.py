"""
Data agent.

The "source of truth" provider: wraps the existing prediction pipeline
(FPLClient + FeatureEngineer + HeuristicPredictor via prediction_service)
and emits a candidate pool of player snapshots for Hermes.
"""

import logging
from typing import Tuple

from pydantic import BaseModel

from .base import AgentContext, BaseAgent
from .schemas import DataSignals, PlayerSnapshot

logger = logging.getLogger(__name__)


class DataAgent(BaseAgent):
    name = "data"

    def _build(self, ctx: AgentContext) -> Tuple[str, BaseModel, str]:
        from agents.mechanics_agent import determine_season_phase
        from services.prediction_service import compute_predictions

        predictions = compute_predictions()

        # Cold-start: attach last-season baselines during preseason/early GWs
        season_phase, _ = determine_season_phase(ctx.fpl_client.get_gameweeks())
        prior_by_name = {}
        if season_phase in ("preseason", "early"):
            try:
                from services.season_archive_service import load_prior_by_name
                prior_by_name = load_prior_by_name()
            except Exception as e:
                logger.warning(f"Data agent: prior archive unavailable ({e})")

        # Top-N by predicted points, plus every user-team player
        selected = list(predictions[:ctx.top_n])
        selected_ids = {p["id"] for p in selected}
        user_ids = set(ctx.user_player_ids)
        if user_ids:
            by_id = {p["id"]: p for p in predictions}
            for pid in user_ids:
                if pid not in selected_ids and pid in by_id:
                    selected.append(by_id[pid])
                    selected_ids.add(pid)

        players_by_id = {p.id: p for p in ctx.fpl_client.get_players()}

        snapshots = []
        for p in selected:
            pl = players_by_id.get(p["id"])
            prior = None
            if prior_by_name and pl:
                prior = (prior_by_name.get(pl.full_name.lower())
                         or prior_by_name.get(pl.web_name.lower()))
            snapshots.append(PlayerSnapshot(
                prior_ppg=prior["points_per_game"] if prior else None,
                prior_total_points=prior["total_points"] if prior else None,
                id=p["id"],
                name=p["name"],
                team=p["team"],
                team_id=p["team_id"],
                position=p["position"],
                position_id=p["position_id"],
                price=p["price"],
                form=p["form"],
                predicted_points=p["predicted_points"],
                points_per_game=float(pl.points_per_game) if pl else 0.0,
                total_points=p["total_points"],
                ownership=p["ownership"],
                xGI=float(pl.expected_goal_involvements) if pl else 0.0,
                xGC=float(pl.expected_goals_conceded) if pl else 0.0,
                opponent=p["opponent"],
                is_home=p["is_home"],
                fixture_difficulty=p["difficulty"],
                status=p["status"],
                in_user_team=p["id"] in user_ids,
            ))

        next_gw = ctx.fpl_client.get_next_gameweek()
        payload = DataSignals(
            gameweek_deadline=next_gw.deadline_time if next_gw else None,
            season_phase=season_phase,
            prior_season_available=bool(prior_by_name),
            players=snapshots,
        )

        top3 = ", ".join(
            f"{s.name} ({s.predicted_points:.1f})" for s in snapshots[:3]
        )
        summary = (
            f"{len(snapshots)} candidate players for GW{ctx.gameweek} "
            f"(top {ctx.top_n} predicted + user team). Top picks: {top3}."
            + (f" Cold-start: last-season priors attached ({season_phase})."
               if prior_by_name else "")
        )
        return summary, payload, "ok"
