"""
Squad Building Service

Handles squad optimization including:
- Building optimal squads with MILP
- Lineup optimization
- Squad suggestions with different predictors
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from pulp import LpMaximize, LpProblem, LpVariable, lpSum, LpStatus

from .cache import cache
from .dependencies import get_dependencies
from data.european_teams import assess_rotation_risk
from data.trends import compute_team_trends

# Import constants - handle both relative and absolute imports
try:
    from constants import PlayerStatus, PlayerPosition
except ImportError:
    import sys
    import os
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from constants import PlayerStatus, PlayerPosition

logger = logging.getLogger(__name__)


def build_optimal_squad(players: List[Dict], budget: float) -> List[Dict]:
    """
    Build optimal 15-man squad using Mixed Integer Linear Programming.
    
    Constraints:
    - Exactly 2 GK, 5 DEF, 5 MID, 3 FWD
    - Max 3 players per team
    - Total cost <= budget
    
    Objective: Maximize total predicted points
    """
    prob = LpProblem("FPL_Squad", LpMaximize)

    # Create binary variable for each player
    player_vars = {p["id"]: LpVariable(f"player_{p['id']}", cat="Binary") for p in players}

    # Objective: maximize predicted points
    prob += lpSum(player_vars[p["id"]] * p["predicted"] for p in players)

    # Budget constraint
    prob += lpSum(player_vars[p["id"]] * p["price"] for p in players) <= budget

    # Position constraints (15 players: 2 GK, 5 DEF, 5 MID, 3 FWD)
    gks = [p for p in players if p["position_id"] == 1]
    defs = [p for p in players if p["position_id"] == 2]
    mids = [p for p in players if p["position_id"] == 3]
    fwds = [p for p in players if p["position_id"] == 4]

    prob += lpSum(player_vars[p["id"]] for p in gks) == 2
    prob += lpSum(player_vars[p["id"]] for p in defs) == 5
    prob += lpSum(player_vars[p["id"]] for p in mids) == 5
    prob += lpSum(player_vars[p["id"]] for p in fwds) == 3

    # Team constraint (max 3 from each team)
    teams = set(p["team_id"] for p in players)
    for team in teams:
        team_players = [p for p in players if p["team_id"] == team]
        prob += lpSum(player_vars[p["id"]] for p in team_players) <= 3

    # Solve
    prob.solve()

    if LpStatus[prob.status] != "Optimal":
        logger.warning(f"Squad optimization status: {LpStatus[prob.status]}")
        # Fallback: return top predicted players by position
        return _greedy_fallback(players, budget)

    # Extract selected squad
    squad = [p for p in players if player_vars[p["id"]].varValue == 1]
    return squad


def _greedy_fallback(players: List[Dict], budget: float) -> List[Dict]:
    """Greedy fallback when MILP fails."""
    squad = []
    remaining = budget
    
    for pos_id, count in [(1, 2), (2, 5), (3, 5), (4, 3)]:
        pos_players = sorted(
            [p for p in players if p["position_id"] == pos_id],
            key=lambda x: x["predicted"],
            reverse=True
        )
        for p in pos_players:
            if len([s for s in squad if s["position_id"] == pos_id]) < count:
                if p["price"] <= remaining:
                    team_count = len([s for s in squad if s["team_id"] == p["team_id"]])
                    if team_count < 3:
                        squad.append(p)
                        remaining -= p["price"]
    
    return squad


def optimize_lineup(squad: List[Dict]) -> tuple:
    """
    Optimize starting XI and bench from 15-man squad.
    
    Returns:
        Tuple of (starting_xi, bench, formation_string)
    """
    gks = sorted([p for p in squad if p["position_id"] == 1], key=lambda x: x["predicted"], reverse=True)
    defs = sorted([p for p in squad if p["position_id"] == 2], key=lambda x: x["predicted"], reverse=True)
    mids = sorted([p for p in squad if p["position_id"] == 3], key=lambda x: x["predicted"], reverse=True)
    fwds = sorted([p for p in squad if p["position_id"] == 4], key=lambda x: x["predicted"], reverse=True)

    # Formation options (DEF-MID-FWD)
    formations = [
        (3, 4, 3), (3, 5, 2), (4, 3, 3), (4, 4, 2), (4, 5, 1), (5, 3, 2), (5, 4, 1)
    ]

    best_xi = None
    best_points = -1
    best_formation = None

    for d, m, f in formations:
        if d > len(defs) or m > len(mids) or f > len(fwds):
            continue
        xi = gks[:1] + defs[:d] + mids[:m] + fwds[:f]
        pts = sum(p["predicted"] for p in xi)
        if pts > best_points:
            best_points = pts
            best_xi = xi
            best_formation = f"{d}-{m}-{f}"

    if best_xi is None:
        best_xi = gks[:1] + defs[:4] + mids[:4] + fwds[:2]
        best_formation = "4-4-2"

    bench = [p for p in squad if p not in best_xi]
    bench.sort(key=lambda x: (x["position_id"] != 1, -x["predicted"]))

    return best_xi, bench, best_formation


async def build_squad_with_predictor(
    predictor,
    method_name: str,
    budget: float = 100.0,
    force_refresh: bool = False
) -> Dict[str, Any]:
    """Build squad using a specific predictor method."""
    deps = get_dependencies()
    fpl_client = deps.fpl_client
    feature_eng = deps.feature_engineer
    betting_odds_client = deps.betting_odds_client
    
    # Force refresh FPL data if requested
    if force_refresh:
        fpl_client.get_bootstrap(force_refresh=True)
    
    next_gw = fpl_client.get_next_gameweek()
    gw_id = next_gw.id if next_gw else 0
    cache_key = (method_name, gw_id, round(budget, 1))
    
    # Skip cache if forcing refresh
    if not force_refresh:
        cached = cache.get("squad", cache_key)
        if cached is not None:
            return cached

    players = fpl_client.get_players()
    teams = fpl_client.get_teams()
    team_names = {t.id: t.short_name for t in teams}

    fixtures = fpl_client.get_fixtures(gameweek=gw_id if gw_id else None)
    gw_deadline = next_gw.deadline_time if next_gw else datetime.now()
    
    fixture_info = _build_fixture_info(fixtures, team_names)
    team_trends = _compute_team_trends(fpl_client, teams)
    fixture_odds_cache = _fetch_betting_odds(betting_odds_client, fixtures, team_names)
    
    player_predictions = _build_player_predictions(
        players, fpl_client, feature_eng, predictor, 
        team_names, fixture_info, gw_deadline, team_trends, 
        fixture_odds_cache, betting_odds_client
    )
    
    squad = build_optimal_squad(player_predictions, budget)
    starting_xi, bench, formation = optimize_lineup(squad)
    
    captain = max(starting_xi, key=lambda x: x["predicted"])
    vice_captain = sorted(starting_xi, key=lambda x: x["predicted"], reverse=True)[1]
    
    total_cost = sum(p["price"] for p in squad)
    total_predicted = sum(p["predicted"] for p in starting_xi) + captain["predicted"]
    
    result = {
        "method": method_name,
        "gameweek": next_gw.id if next_gw else None,
        "formation": formation,
        "starting_xi": [
            {**p, "is_captain": p["id"] == captain["id"], "is_vice_captain": p["id"] == vice_captain["id"]}
            for p in starting_xi
        ],
        "bench": bench,
        "captain": {"id": captain["id"], "name": captain["name"], "predicted": round(captain["predicted"], 2)},
        "vice_captain": {"id": vice_captain["id"], "name": vice_captain["name"], "predicted": round(vice_captain["predicted"], 2)},
        "total_cost": round(total_cost, 1),
        "remaining_budget": round(budget - total_cost, 1),
        "predicted_points": round(total_predicted, 1),
    }

    cache.set("squad", cache_key, result)
    return result


def _build_fixture_info(fixtures, team_names) -> Dict:
    """Build fixture info mapping team_id -> opponent info."""
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
    return fixture_info


def _compute_team_trends(fpl_client, teams) -> Dict:
    """Compute team trends from fixtures."""
    try:
        all_fixtures = fpl_client.get_fixtures(gameweek=None)
        return compute_team_trends(teams, all_fixtures, window=6, previous_window=6)
    except Exception:
        return {}


def _fetch_betting_odds(betting_odds_client, fixtures, team_names) -> Dict:
    """Fetch betting odds for fixtures."""
    fixture_odds_cache = {}
    if not betting_odds_client.enabled:
        return fixture_odds_cache
    
    try:
        all_odds_data = betting_odds_client._fetch_all_odds()
        if all_odds_data:
            for f in fixtures:
                home_team = team_names.get(f.team_h, "???")
                away_team = team_names.get(f.team_a, "???")
                odds = betting_odds_client.get_fixture_odds(home_team, away_team, all_odds_data)
                if odds:
                    fixture_odds_cache[f.team_h] = {**odds, "is_home": True}
                    fixture_odds_cache[f.team_a] = {**odds, "is_home": False}
    except Exception as e:
        logger.warning(f"Error fetching betting odds: {e}")
    
    return fixture_odds_cache


def _build_player_predictions(
    players, fpl_client, feature_eng, predictor,
    team_names, fixture_info, gw_deadline, team_trends,
    fixture_odds_cache, betting_odds_client
) -> List[Dict]:
    """Build predictions for all eligible players."""
    player_predictions = []
    
    for player in players:
        # Filter out ineligible players
        if not _is_player_eligible(player, fpl_client):
            continue
        
        try:
            features = feature_eng.extract_features(player.id, include_history=False)
            pred = predictor.predict_player(features)
            
            fix = fixture_info.get(player.team, {})
            opponent = fix.get("opponent", "???")
            difficulty = fix.get("difficulty", 3)
            is_home = fix.get("is_home", False)
            
            team_name = team_names.get(player.team, "???")
            rotation = assess_rotation_risk(team_name, gw_deadline, difficulty)
            trend = team_trends.get(player.team)
            reversal = trend.reversal_score if trend else 0.0
            
            # Get betting odds
            odds_data = fixture_odds_cache.get(player.team, {})
            anytime_goalscorer_prob, clean_sheet_prob, team_win_prob = _extract_odds(
                player, odds_data, betting_odds_client, is_home
            )
            
            reasons = _build_reasons(player, rotation, difficulty, opponent, is_home, pred, reversal, team_name)
            
            position_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
            player_predictions.append({
                "id": player.id,
                "name": player.web_name,
                "team": team_name,
                "team_id": player.team,
                "position": position_map.get(player.element_type, "MID"),
                "position_id": player.element_type,
                "price": player.price,
                "predicted": pred,
                "form": float(player.form),
                "total_points": player.total_points,
                "ownership": float(player.selected_by_percent),
                "opponent": opponent,
                "difficulty": difficulty,
                "is_home": is_home,
                "rotation_risk": rotation.risk_level,
                "european_comp": rotation.competition,
                "rotation_factor": rotation.risk_factor,
                "team_reversal": reversal,
                "status": player.status,
                "anytime_goalscorer_prob": anytime_goalscorer_prob,
                "clean_sheet_prob": clean_sheet_prob,
                "team_win_prob": team_win_prob,
                "reason": " • ".join(reasons[:2]),
            })
        except Exception as e:
            logger.debug(f"Skipping player {player.id} due to error: {e}")
            continue
    
    return player_predictions


def _is_player_eligible(player, fpl_client) -> bool:
    """Check if player is eligible for squad selection."""
    if player.minutes < 1:
        return False
    if player.status in [PlayerStatus.INJURED, PlayerStatus.SUSPENDED, 
                         PlayerStatus.UNAVAILABLE, PlayerStatus.NOT_AVAILABLE, PlayerStatus.DOUBTFUL]:
        return False
    
    chance = player.chance_of_playing_next_round
    if chance is not None and chance < 50:
        return False
    
    news_lower = (player.news or "").lower()
    if any(keyword in news_lower for keyword in ["injured", "injury", "suspended", "unavailable", "ruled out", "will miss", "out for"]):
        return False
    
    # Check recent playing time
    try:
        player_details = fpl_client.get_player_details(player.id)
        history = player_details.get("history", [])
        if history:
            finished_gws = [gw for gw in history if gw.get("round", 0) > 0]
            if finished_gws:
                finished_gws.sort(key=lambda x: x.get("round", 0), reverse=True)
                recent_gws = finished_gws[:3]
                recent_minutes = [gw.get("minutes", 0) for gw in recent_gws]
                if recent_minutes:
                    avg_recent_minutes = sum(recent_minutes) / len(recent_minutes)
                    if avg_recent_minutes < 30:
                        most_recent_minutes = recent_minutes[0] if recent_minutes else 0
                        if most_recent_minutes < 1:
                            return False
    except Exception:
        pass
    
    return True


def _extract_odds(player, odds_data, betting_odds_client, is_home) -> tuple:
    """Extract betting odds probabilities for a player."""
    anytime_goalscorer_prob = 0.0
    clean_sheet_prob = 0.0
    team_win_prob = 0.5
    
    if odds_data:
        if player.element_type in [PlayerPosition.MID, PlayerPosition.FWD]:
            games_played = max(1, player.minutes / 90.0) if player.minutes > 0 else 1
            player_stats = {
                "goals_per_game": player.goals_scored / games_played,
                "xg_per_game": float(player.expected_goals) / games_played,
                "position": player.element_type,
                "is_premium": player.price >= 9.0
            }
            anytime_goalscorer_prob = betting_odds_client.get_player_goalscorer_odds(
                player.web_name, odds_data, player_stats
            )
        elif player.element_type in [PlayerPosition.GK, PlayerPosition.DEF]:
            clean_sheet_prob = betting_odds_client.get_clean_sheet_probability(is_home, odds_data)
        
        team_win_prob = odds_data.get("home_win_prob" if is_home else "away_win_prob", 0.5)
    
    return anytime_goalscorer_prob, clean_sheet_prob, team_win_prob


def _build_reasons(player, rotation, difficulty, opponent, is_home, pred, reversal, team_name) -> List[str]:
    """Build reason strings for player selection."""
    reasons = []
    if rotation.risk_level == "high":
        reasons.append(f"⚠️ HIGH rotation ({rotation.competition})")
    elif rotation.risk_level == "medium":
        reasons.append(f"⚡ Rotation risk ({rotation.competition})")
    
    if float(player.form) >= 5.0:
        reasons.append(f"Hot form ({player.form})")
    if difficulty <= 2:
        reasons.append(f"Easy fixture vs {opponent} (FDR {difficulty})")
    elif is_home and difficulty <= 3:
        reasons.append(f"Home vs {opponent}")
    if float(player.selected_by_percent) < 10 and pred >= 5:
        reasons.append(f"Differential ({player.selected_by_percent}% owned)")
    if player.total_points >= 70:
        reasons.append(f"Season performer ({player.total_points} pts)")
    if reversal >= 1.2:
        reasons.append(f"Bounce-back spot ({team_name})")
    
    if not reasons:
        reasons.append(f"vs {opponent} ({'H' if is_home else 'A'})")
    
    return reasons

