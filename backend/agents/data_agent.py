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
        from services.prediction_service import compute_predictions

        predictions = compute_predictions()

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
            snapshots.append(PlayerSnapshot(
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
            players=snapshots,
        )

        top3 = ", ".join(
            f"{s.name} ({s.predicted_points:.1f})" for s in snapshots[:3]
        )
        summary = (
            f"{len(snapshots)} candidate players for GW{ctx.gameweek} "
            f"(top {ctx.top_n} predicted + user team). Top picks: {top3}."
        )
        return summary, payload, "ok"
