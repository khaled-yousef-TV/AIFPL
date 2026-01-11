"""
Wildcard Service

Handles wildcard planning logic for coordinated multi-transfer plans.
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Set

from .dependencies import get_dependencies

logger = logging.getLogger(__name__)


async def get_wildcard_plan(
    squad: List[Dict],
    bank: float,
    free_transfers: int
) -> Dict[str, Any]:
    """
    Get coordinated multi-transfer plan for wildcard.
    
    Args:
        squad: Current squad players
        bank: Money in the bank
        free_transfers: Number of free transfers (must be >= 4)
        
    Returns:
        Wildcard plan with transfers_out, transfers_in, etc.
    """
    deps = get_dependencies()
    fpl_client = deps.fpl_client
    feature_eng = deps.feature_engineer
    predictor = deps.predictor_heuristic
    
    players = fpl_client.get_players()
    teams = fpl_client.get_teams()
    team_names = {t.id: t.short_name for t in teams}
    players_by_id = {p.id: p for p in players}
    
    next_gw = fpl_client.get_next_gameweek()
    fixtures = fpl_client.get_fixtures(gameweek=next_gw.id if next_gw else None)
    gw_deadline = next_gw.deadline_time if next_gw else datetime.now()
    
    fixture_info = _build_fixture_info(fixtures, team_names)
    avg_fixture_difficulty = _get_long_term_fixtures(fpl_client, next_gw)
    
    # Build current squad
    squad_ids = {p["id"] for p in squad}
    current_squad, current_team_counts = _build_current_squad(
        squad, players_by_id, team_names, fixture_info, feature_eng, predictor
    )
    
    # Build all available players
    all_players, player_predictions = _build_available_players(
        players, squad_ids, team_names, fixture_info, feature_eng, predictor
    )
    
    # Generate wildcard plan using the engine
    try:
        from backend.engine.mini_rebuild import WildcardEngine
    except ImportError:
        from engine.mini_rebuild import WildcardEngine
    
    engine = WildcardEngine()
    plan = engine.generate_plan(
        current_squad=current_squad,
        all_players=all_players,
        bank=bank,
        free_transfers=free_transfers,
        player_predictions=player_predictions,
        fixture_info=fixture_info,
        avg_fixture_5gw=avg_fixture_difficulty,
        team_counts=current_team_counts,
        team_names=team_names
    )
    
    if not plan:
        return None
    
    return {
        "transfers_out": plan.transfers_out,
        "transfers_in": plan.transfers_in,
        "total_points_gain": plan.total_points_gain,
        "total_cost": plan.total_cost,
        "resulting_squad": plan.resulting_squad,
        "combined_rationale": plan.combined_rationale,
        "individual_breakdowns": plan.individual_breakdowns,
        "before_total_points": plan.before_total_points,
        "after_total_points": plan.after_total_points,
        "kept_players": plan.kept_players,
    }


def _build_fixture_info(fixtures, team_names) -> Dict:
    """Build fixture info mapping."""
    fixture_info = {}
    for f in fixtures:
        fixture_info[f.team_h] = {
            "opponent": team_names.get(f.team_a, "???"),
            "difficulty": f.team_h_difficulty,
            "is_home": True
        }
        fixture_info[f.team_a] = {
            "opponent": team_names.get(f.team_h, "???"),
            "difficulty": f.team_a_difficulty,
            "is_home": False
        }
    return fixture_info


def _get_long_term_fixtures(fpl_client, next_gw) -> Dict[int, float]:
    """Get average fixture difficulty for next 5 GWs."""
    long_term_fixtures = {}
    for gw_offset in range(5):
        gw_num = (next_gw.id if next_gw else 1) + gw_offset
        try:
            gw_fixtures = fpl_client.get_fixtures(gameweek=gw_num)
            for f in gw_fixtures:
                if f.team_h not in long_term_fixtures:
                    long_term_fixtures[f.team_h] = []
                if f.team_a not in long_term_fixtures:
                    long_term_fixtures[f.team_a] = []
                long_term_fixtures[f.team_h].append(f.team_h_difficulty)
                long_term_fixtures[f.team_a].append(f.team_a_difficulty)
        except Exception:
            pass
    
    return {
        team_id: sum(diffs) / len(diffs) if diffs else 3.0
        for team_id, diffs in long_term_fixtures.items()
    }


def _build_current_squad(
    squad, players_by_id, team_names, fixture_info, feature_eng, predictor
) -> tuple:
    """Build current squad with predictions."""
    current_squad = []
    current_team_counts = {}
    
    for sp in squad:
        pl = players_by_id.get(sp["id"])
        if not pl:
            continue
        
        current_team_counts[pl.team] = current_team_counts.get(pl.team, 0) + 1
        
        try:
            features = feature_eng.extract_features(pl.id, include_history=False)
            pred = predictor.predict_player(features)
        except Exception:
            pred = float(pl.form) if pl.form else 2.0
        
        team_name = team_names.get(pl.team, "???")
        fix = fixture_info.get(pl.team, {})
        
        current_squad.append({
            "id": pl.id,
            "name": pl.web_name,
            "position": sp["position"],
            "position_id": pl.element_type,
            "price": sp["price"],
            "team": team_name,
            "team_id": pl.team,
            "predicted": round(pred, 2),
            "form": float(pl.form),
            "status": pl.status,
            "fixture": fix.get("opponent", "???"),
            "fixture_difficulty": fix.get("difficulty", 3),
        })
    
    return current_squad, current_team_counts


def _build_available_players(
    players, squad_ids, team_names, fixture_info, feature_eng, predictor
) -> tuple:
    """Build list of available players with predictions."""
    all_players = []
    player_predictions = {}
    
    for player in players:
        if player.id in squad_ids:
            continue
        if player.status in ["i", "s", "u", "n"]:
            continue
        
        chance = player.chance_of_playing_next_round
        if chance is not None and chance < 50:
            continue
        
        news_lower = (player.news or "").lower()
        if any(kw in news_lower for kw in ["injured", "injury", "suspended", "unavailable", "ruled out"]):
            continue
        
        if player.minutes < 1:
            continue
        
        try:
            features = feature_eng.extract_features(player.id, include_history=False)
            pred = predictor.predict_player(features)
        except Exception:
            pred = float(player.form) if player.form else 2.0
        
        player_predictions[player.id] = pred
        
        team_name = team_names.get(player.team, "???")
        fix = fixture_info.get(player.team, {})
        
        all_players.append({
            "id": player.id,
            "name": player.web_name,
            "position": {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}.get(player.element_type, "MID"),
            "position_id": player.element_type,
            "price": player.price,
            "team": team_name,
            "team_id": player.team,
            "predicted": round(pred, 2),
            "form": float(player.form),
            "status": player.status,
            "fixture": fix.get("opponent", "???"),
            "fixture_difficulty": fix.get("difficulty", 3),
        })
    
    return all_players, player_predictions

