"""
Prediction Service

Handles all player prediction logic including:
- Getting player predictions for a gameweek
- Building squads with different predictors
- Caching predictions
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from .cache import cache
from .dependencies import get_dependencies
from data.european_teams import assess_rotation_risk

logger = logging.getLogger(__name__)


async def get_predictions(position: Optional[int] = None, top_n: int = 100) -> Dict[str, Any]:
    """
    Get player predictions for next gameweek.
    
    Args:
        position: Filter by position_id (1=GK, 2=DEF, 3=MID, 4=FWD)
        top_n: Number of predictions to return
        
    Returns:
        Dict with 'predictions' list
    """
    deps = get_dependencies()
    fpl_client = deps.fpl_client
    feature_eng = deps.feature_engineer
    predictor_heuristic = deps.predictor_heuristic
    
    next_gw = fpl_client.get_next_gameweek()
    gw_id = next_gw.id if next_gw else 0

    cache_key = ("heuristic", gw_id)
    all_predictions = cache.get("predictions", cache_key)

    if all_predictions is None:
        players = fpl_client.get_players()
        teams = fpl_client.get_teams()
        team_names = {t.id: t.short_name for t in teams}

        fixtures = fpl_client.get_fixtures(gameweek=gw_id if gw_id else None)
        gw_deadline = next_gw.deadline_time if next_gw else datetime.now()

        fixture_info = {}
        for f in fixtures:
            fixture_info[f.team_h] = {
                "opponent": team_names.get(f.team_a, "???"),
                "difficulty": f.team_h_difficulty,
                "is_home": True,
            }
            fixture_info[f.team_a] = {
                "opponent": team_names.get(f.team_h, "???"),
                "difficulty": f.team_a_difficulty,
                "is_home": False,
            }

        predictions = []
        total_players = len(players)
        filtered_minutes = 0
        filtered_status = 0
        errors = 0

        for player in players:
            if player.minutes < 1:
                filtered_minutes += 1
                continue
            if player.status in ["i", "s", "u", "n"]:
                filtered_status += 1
                continue

            try:
                features = feature_eng.extract_features(player.id, include_history=False)
                pred = predictor_heuristic.predict_player(features)

                fix = fixture_info.get(player.team, {})
                opponent = fix.get("opponent", "???")
                difficulty = fix.get("difficulty", 3)
                is_home = fix.get("is_home", False)

                team_name = team_names.get(player.team, "???")
                rotation = assess_rotation_risk(team_name, gw_deadline, difficulty)

                reasons = []
                if rotation.risk_level in ["high", "medium"]:
                    reasons.append(f"⚠️ {rotation.competition} rotation risk")
                if float(player.form) >= 5.0:
                    reasons.append(f"Form: {player.form}")
                if difficulty <= 2:
                    reasons.append(f"Easy fixture (FDR {difficulty})")
                if is_home:
                    reasons.append("Home advantage")
                if not reasons:
                    reasons.append(f"vs {opponent}")

                predictions.append({
                    "id": player.id,
                    "name": player.web_name,
                    "full_name": player.full_name,
                    "team": team_name,
                    "team_id": player.team,
                    "position": player.position,
                    "position_id": player.element_type,
                    "price": player.price,
                    "predicted_points": round(pred, 2),
                    "form": float(player.form),
                    "total_points": player.total_points,
                    "ownership": float(player.selected_by_percent),
                    "opponent": opponent,
                    "difficulty": difficulty,
                    "is_home": is_home,
                    "rotation_risk": rotation.risk_level,
                    "european_comp": rotation.competition,
                    "reason": " • ".join(reasons[:2]),
                    "status": player.status,
                    "news": player.news,
                })
            except Exception as e:
                errors += 1
                if errors <= 5:
                    logger.warning(f"Error predicting {player.web_name}: {e}")
                continue

        logger.info(
            f"Predictions: {total_players} total, {filtered_minutes} filtered (minutes), "
            f"{filtered_status} filtered (status), {errors} errors, {len(predictions)} successful"
        )

        predictions.sort(key=lambda x: x["predicted_points"], reverse=True)
        all_predictions = predictions
        cache.set("predictions", cache_key, all_predictions)

    filtered = all_predictions
    if position is not None:
        filtered = [p for p in filtered if p.get("position_id") == position]

    return {"predictions": filtered[:top_n]}


async def get_top_picks() -> Dict[str, List[Dict]]:
    """Get top 5 picks for each position."""
    result = {}
    for pos_id, pos_name in [(1, "goalkeepers"), (2, "defenders"), (3, "midfielders"), (4, "forwards")]:
        preds = await get_predictions(position=pos_id, top_n=5)
        result[pos_name] = preds["predictions"]
    return result


async def get_differentials(max_ownership: float = 10.0, top_n: int = 10) -> Dict[str, List[Dict]]:
    """Get differential picks (low ownership, high predicted points)."""
    preds = await get_predictions(top_n=500)
    differentials = [
        p for p in preds["predictions"]
        if p["ownership"] < max_ownership and p["predicted_points"] >= 4.0
    ]
    differentials.sort(key=lambda x: x["predicted_points"], reverse=True)
    return {"differentials": differentials[:top_n]}

