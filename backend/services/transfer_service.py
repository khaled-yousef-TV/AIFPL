"""
Transfer Service

Handles transfer suggestion logic including:
- Squad analysis
- Player scoring (keep/buy scores)
- Transfer recommendation generation
- Hold suggestions
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Set

from .dependencies import get_dependencies
from data.european_teams import assess_rotation_risk
from data.trends import compute_team_trends

logger = logging.getLogger(__name__)


async def get_transfer_suggestions(
    squad: List[Dict],
    bank: float,
    free_transfers: int,
    suggestions_limit: int = 3
) -> Dict[str, Any]:
    """
    Get transfer suggestions based on user's current squad.
    
    Args:
        squad: List of squad players with id, name, position, price
        bank: Money in the bank
        free_transfers: Number of free transfers
        suggestions_limit: Max suggestions to return
        
    Returns:
        Dict with squad_analysis, suggestions, warnings
    """
    deps = get_dependencies()
    fpl_client = deps.fpl_client
    feature_eng = deps.feature_engineer
    predictor = deps.predictor_heuristic
    betting_odds_client = deps.betting_odds_client
    
    players = fpl_client.get_players()
    teams = fpl_client.get_teams()
    team_names = {t.id: t.short_name for t in teams}
    players_by_id = {p.id: p for p in players}
    
    next_gw = fpl_client.get_next_gameweek()
    fixtures = fpl_client.get_fixtures(gameweek=next_gw.id if next_gw else None)
    gw_deadline = next_gw.deadline_time if next_gw else datetime.now()
    
    # Build fixture info
    fixture_info = _build_fixture_info(fixtures, team_names)
    avg_fixture_difficulty = _get_long_term_fixtures(fpl_client, next_gw)
    fixture_odds_cache = _fetch_betting_odds(betting_odds_client, fixtures, team_names)
    team_trends = _get_team_trends(fpl_client, teams)
    
    # Validate squad
    squad_ids = {p["id"] for p in squad}
    squad_by_pos = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for p in squad:
        squad_by_pos[p["position"]].append(p)
    
    warnings = _validate_squad(squad, squad_ids, squad_by_pos, players_by_id, team_names)
    
    # Get current team counts
    current_team_counts = _get_team_counts(squad, players_by_id)
    
    # Analyze squad
    squad_analysis = _analyze_squad(
        squad, players_by_id, team_names, fixture_info, 
        avg_fixture_difficulty, feature_eng, predictor, gw_deadline, team_trends
    )
    
    # Find transfer suggestions
    transfer_suggestions = _find_transfers(
        squad_analysis, players, squad_ids, current_team_counts,
        bank, team_names, fixture_info, avg_fixture_difficulty,
        feature_eng, predictor, gw_deadline, team_trends,
        fixture_odds_cache, betting_odds_client
    )
    
    # Sort and limit
    transfer_suggestions.sort(key=lambda x: x["priority_score"], reverse=True)
    
    # Consider hold suggestion
    hold_suggestion = _evaluate_hold(
        squad_analysis, transfer_suggestions, free_transfers
    )
    
    # Build final response
    top_transfers = transfer_suggestions[:suggestions_limit]
    if hold_suggestion:
        top_suggestions = [hold_suggestion] + top_transfers
    else:
        top_suggestions = top_transfers
    
    return {
        "squad_analysis": squad_analysis,
        "suggestions": top_suggestions,
        "bank": bank,
        "free_transfers": free_transfers,
        "warnings": warnings,
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


def _fetch_betting_odds(betting_odds_client, fixtures, team_names) -> Dict:
    """Fetch betting odds for fixtures."""
    if not betting_odds_client.enabled:
        return {}
    
    try:
        all_odds_data = betting_odds_client._fetch_all_odds()
        if not all_odds_data:
            return {}
        
        odds_cache = {}
        for f in fixtures:
            home_team = team_names.get(f.team_h, "???")
            away_team = team_names.get(f.team_a, "???")
            odds = betting_odds_client.get_fixture_odds(home_team, away_team, all_odds_data)
            if odds:
                odds_cache[f.team_h] = {**odds, "is_home": True}
                odds_cache[f.team_a] = {**odds, "is_home": False}
        return odds_cache
    except Exception:
        return {}


def _get_team_trends(fpl_client, teams) -> Dict:
    """Get team trends for reversal analysis."""
    try:
        all_fixtures = fpl_client.get_fixtures(gameweek=None)
        return compute_team_trends(teams, all_fixtures, window=6, previous_window=6)
    except Exception:
        return {}


def _validate_squad(squad, squad_ids, squad_by_pos, players_by_id, team_names) -> List[str]:
    """Validate squad and return warnings."""
    warnings = []
    
    if len(squad) != len(squad_ids):
        warnings.append("Duplicate player(s) detected in squad input.")
    if len(squad) not in (11, 12, 13, 14, 15):
        warnings.append("Squad size looks unusual.")
    
    pos_counts = {k: len(v) for k, v in squad_by_pos.items()}
    if len(squad) == 15 and pos_counts != {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}:
        warnings.append(f"Full squad composition is unusual (expected 2/5/5/3, got {pos_counts}).")
    
    current_team_counts = {}
    missing_ids = []
    for sp in squad:
        pl = players_by_id.get(sp["id"])
        if not pl:
            missing_ids.append(sp["id"])
            continue
        current_team_counts[pl.team] = current_team_counts.get(pl.team, 0) + 1
    
    if missing_ids:
        warnings.append(f"{len(missing_ids)} squad player(s) not found.")
    
    invalid_clubs = [
        team_names.get(tid, str(tid)) 
        for tid, c in current_team_counts.items() if c > 3
    ]
    if invalid_clubs:
        warnings.append(f"Squad violates max 3 per club: {', '.join(invalid_clubs)}")
    
    return warnings


def _get_team_counts(squad, players_by_id) -> Dict[int, int]:
    """Get current team player counts."""
    counts = {}
    for sp in squad:
        pl = players_by_id.get(sp["id"])
        if pl:
            counts[pl.team] = counts.get(pl.team, 0) + 1
    return counts


def _analyze_squad(
    squad, players_by_id, team_names, fixture_info,
    avg_fixture_difficulty, feature_eng, predictor, gw_deadline, team_trends
) -> List[Dict]:
    """Analyze each player in squad."""
    squad_analysis = []
    
    for sp in squad:
        player = players_by_id.get(sp["id"])
        if not player:
            continue
        
        team_name = team_names.get(player.team, "???")
        fix = fixture_info.get(player.team, {})
        rotation = assess_rotation_risk(team_name, gw_deadline, fix.get("difficulty", 3))
        trend = team_trends.get(player.team)
        reversal = trend.reversal_score if trend else 0.0
        avg_diff = avg_fixture_difficulty.get(player.team, 3.0)
        
        try:
            features = feature_eng.extract_features(player.id, include_history=False)
            pred = predictor.predict_player(features)
        except Exception:
            pred = float(player.form) if player.form else 2.0
        
        keep_score = _calculate_keep_score(
            pred, fix, avg_diff, rotation, reversal, player
        )
        
        squad_analysis.append({
            "id": player.id,
            "name": player.web_name,
            "team": team_name,
            "team_id": player.team,
            "position": sp["position"],
            "price": sp["price"],
            "predicted": round(pred, 2),
            "form": float(player.form),
            "keep_score": round(keep_score, 2),
            "fixture": fix.get("opponent", "???"),
            "fixture_difficulty": fix.get("difficulty", 3),
            "avg_fixture_5gw": round(avg_diff, 2),
            "rotation_risk": rotation.risk_level,
            "european_comp": rotation.competition,
            "status": player.status,
        })
    
    squad_analysis.sort(key=lambda x: x["keep_score"])
    return squad_analysis


def _calculate_keep_score(pred, fix, avg_diff, rotation, reversal, player) -> float:
    """Calculate keep score - lower = more likely to transfer out."""
    keep_score = pred
    
    if fix.get("difficulty", 3) >= 4:
        keep_score -= 1.5
    if avg_diff >= 3.5:
        keep_score -= 1.0
    if rotation.risk_level == "high":
        keep_score -= 2.0
    elif rotation.risk_level == "medium":
        keep_score -= 1.0
    if reversal >= 1.2:
        keep_score += 0.4
    if float(player.form) < 3.0:
        keep_score -= 1.0
    if player.status == "d":
        keep_score -= 1.5
    elif player.status in ["i", "s", "u", "n"]:
        keep_score -= 5.0
    
    return keep_score


def _find_transfers(
    squad_analysis, players, squad_ids, current_team_counts,
    bank, team_names, fixture_info, avg_fixture_difficulty,
    feature_eng, predictor, gw_deadline, team_trends,
    fixture_odds_cache, betting_odds_client
) -> List[Dict]:
    """Find transfer suggestions for worst players."""
    transfer_suggestions = []
    transfer_out_candidates = squad_analysis[:min(10, len(squad_analysis))]
    per_out_replacements = 3
    
    for out_player in transfer_out_candidates:
        pos = out_player["position"]
        max_price = out_player["price"] + bank
        
        # Simulate removing out player
        counts_after_out = dict(current_team_counts)
        out_team_id = out_player.get("team_id")
        if isinstance(out_team_id, int):
            counts_after_out[out_team_id] = max(0, counts_after_out.get(out_team_id, 0) - 1)
        
        # Find replacements
        replacements = _find_replacements(
            players, squad_ids, pos, max_price, counts_after_out,
            team_names, fixture_info, avg_fixture_difficulty,
            feature_eng, predictor, gw_deadline, team_trends,
            fixture_odds_cache, betting_odds_client
        )
        
        if replacements:
            for chosen in replacements[:per_out_replacements]:
                suggestion = _create_transfer_suggestion(
                    out_player, chosen, replacements
                )
                transfer_suggestions.append(suggestion)
    
    return transfer_suggestions


def _find_replacements(
    players, squad_ids, pos, max_price, counts_after_out,
    team_names, fixture_info, avg_fixture_difficulty,
    feature_eng, predictor, gw_deadline, team_trends,
    fixture_odds_cache, betting_odds_client
) -> List[Dict]:
    """Find replacement players for a position."""
    replacements = []
    
    for player in players:
        if player.id in squad_ids:
            continue
        if player.position != pos:
            continue
        if player.price > max_price:
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
        if counts_after_out.get(player.team, 0) >= 3:
            continue
        
        team_name = team_names.get(player.team, "???")
        fix = fixture_info.get(player.team, {})
        rotation = assess_rotation_risk(team_name, gw_deadline, fix.get("difficulty", 3))
        avg_diff = avg_fixture_difficulty.get(player.team, 3.0)
        trend = team_trends.get(player.team)
        reversal = trend.reversal_score if trend else 0.0
        
        try:
            features = feature_eng.extract_features(player.id, include_history=False)
            pred = predictor.predict_player(features)
        except Exception:
            pred = float(player.form) if player.form else 2.0
        
        buy_score = _calculate_buy_score(
            pred, fix, avg_diff, rotation, reversal, player,
            fixture_odds_cache, betting_odds_client
        )
        
        replacements.append({
            "id": player.id,
            "name": player.web_name,
            "team": team_name,
            "team_id": player.team,
            "position": pos,
            "price": player.price,
            "minutes": player.minutes,
            "predicted": round(pred, 2),
            "form": float(player.form),
            "buy_score": round(buy_score, 2),
            "fixture": fix.get("opponent", "???"),
            "fixture_difficulty": fix.get("difficulty", 3),
            "avg_fixture_5gw": round(avg_diff, 2),
            "rotation_risk": rotation.risk_level,
            "european_comp": rotation.competition,
            "ownership": float(player.selected_by_percent),
        })
    
    replacements.sort(key=lambda x: x["buy_score"], reverse=True)
    return replacements


def _calculate_buy_score(
    pred, fix, avg_diff, rotation, reversal, player,
    fixture_odds_cache, betting_odds_client
) -> float:
    """Calculate buy score - higher = better transfer in."""
    buy_score = pred
    
    if fix.get("difficulty", 3) <= 2:
        buy_score += 2.0
    if avg_diff <= 2.5:
        buy_score += 1.5
    elif avg_diff <= 3.0:
        buy_score += 0.5
    if rotation.risk_level == "high":
        buy_score -= 2.0
    elif rotation.risk_level == "medium":
        buy_score -= 1.0
    if float(player.form) >= 6.0:
        buy_score += 1.5
    elif float(player.form) >= 4.0:
        buy_score += 0.5
    if float(player.selected_by_percent) < 10:
        buy_score += 0.5
    if reversal >= 1.2:
        buy_score += 0.6
    
    # Add betting odds bonus
    if betting_odds_client.enabled:
        odds_data = fixture_odds_cache.get(player.team, {})
        if odds_data:
            buy_score = _add_odds_bonus(buy_score, player, odds_data, fix, betting_odds_client)
    
    return buy_score


def _add_odds_bonus(buy_score, player, odds_data, fix, betting_odds_client) -> float:
    """Add betting odds bonus to buy score."""
    odds_weight = betting_odds_client.weight
    is_home = fix.get("is_home", True)
    
    if player.element_type in [3, 4]:  # MID/FWD
        games_played = max(1, player.minutes / 90.0)
        player_stats = {
            "goals_per_game": player.goals_scored / games_played,
            "xg_per_game": float(player.expected_goals) / games_played,
            "position": player.element_type,
            "is_premium": player.price >= 9.0
        }
        goalscorer_prob = betting_odds_client.get_player_goalscorer_odds(
            player.web_name, odds_data, player_stats
        )
        if goalscorer_prob > 0:
            buy_score += goalscorer_prob * 2.5 * odds_weight
    elif player.element_type in [1, 2]:  # GK/DEF
        cs_prob = betting_odds_client.get_clean_sheet_probability(is_home, odds_data)
        if cs_prob > 0:
            buy_score += cs_prob * 2.0 * odds_weight
    
    team_win_prob = odds_data.get("home_win_prob" if is_home else "away_win_prob", 0.5)
    buy_score += (team_win_prob - 0.5) * 0.4 * odds_weight
    
    return buy_score


def _create_transfer_suggestion(out_player, chosen, replacements) -> Dict:
    """Create a transfer suggestion with comparison data."""
    points_gain = chosen["predicted"] - out_player["predicted"]
    teammate_comparison = _build_teammate_comparison(chosen, replacements)
    reasons = _build_transfer_reasons(out_player, chosen, points_gain)
    
    return {
        "out": out_player,
        "in": chosen,
        "cost": round(chosen["price"] - out_player["price"], 1),
        "points_gain": round(points_gain, 2),
        "priority_score": round(chosen["buy_score"] - out_player["keep_score"], 2),
        "reason": reasons[0],
        "all_reasons": reasons,
        "teammate_comparison": teammate_comparison,
    }


def _build_teammate_comparison(chosen, replacements) -> Optional[Dict]:
    """Build comparison with same-team alternatives."""
    try:
        same_team = [
            r for r in replacements
            if r.get("team_id") == chosen.get("team_id") and r.get("id") != chosen.get("id")
        ]
        if not same_team:
            return None
        
        combined = [chosen] + same_team
        combined.sort(key=lambda x: x.get("buy_score", 0), reverse=True)
        rank = next((i for i, x in enumerate(combined) if x.get("id") == chosen.get("id")), 0) + 1
        
        return {
            "team": chosen.get("team"),
            "position": chosen.get("position"),
            "rank": rank,
            "total": len(combined),
            "alternatives": same_team[:5],
        }
    except Exception:
        return None


def _build_transfer_reasons(out_player, chosen, points_gain) -> List[str]:
    """Build reason strings for a transfer."""
    reasons = []
    
    if out_player["fixture_difficulty"] >= 4 and chosen["fixture_difficulty"] <= 2:
        reasons.append(f"Fixture swing: {out_player['fixture']} → {chosen['fixture']}")
    if out_player["avg_fixture_5gw"] > chosen["avg_fixture_5gw"] + 0.5:
        reasons.append(f"Better long-term fixtures ({chosen['avg_fixture_5gw']} vs {out_player['avg_fixture_5gw']})")
    if chosen["form"] > out_player["form"] + 2:
        reasons.append(f"Form upgrade: {out_player['form']} → {chosen['form']}")
    if out_player["status"] != "a":
        reasons.append(f"{out_player['name']} is {out_player['status']}")
    if out_player["rotation_risk"] in ["high", "medium"] and chosen["rotation_risk"] == "none":
        reasons.append("Avoids European rotation")
    if not reasons:
        reasons.append(f"+{round(points_gain, 1)} predicted points")
    
    return reasons


def _evaluate_hold(squad_analysis, transfer_suggestions, free_transfers) -> Optional[Dict]:
    """Evaluate if holding/saving transfer is recommended."""
    if not squad_analysis:
        return None
    
    best_move = transfer_suggestions[0] if transfer_suggestions else None
    hit_cost = 0 if free_transfers > 0 else 4
    best_net_gain = None
    
    if best_move:
        best_net_gain = round(float(best_move.get("points_gain", 0)) - hit_cost, 2)
    
    worst = squad_analysis[0]
    has_fire = (
        worst.get("status") in ["i", "s", "u", "n"] or
        (worst.get("status") == "d" and worst.get("keep_score", 0) < 3.5) or
        worst.get("fixture_difficulty", 3) >= 5
    )
    
    should_hold = (best_move is None) or (
        best_net_gain is not None and best_net_gain < 1.0 and not has_fire
    )
    
    if not should_hold and best_move and hit_cost == 4 and best_net_gain is not None:
        if best_net_gain < 2.5 and not has_fire:
            should_hold = True
    
    if should_hold:
        why = []
        if best_move is None:
            why.append("No clear upgrades found.")
        else:
            why.append(f"Best move is only ~{best_net_gain:+.2f} points.")
        why.append("Squad looks healthy.")
        why.append("Saving a transfer keeps flexibility.")
        
        return {
            "type": "hold",
            "hit_cost": hit_cost,
            "best_net_gain": best_net_gain,
            "reason": "Hold / Save transfer",
            "why": why,
            "best_alternative": best_move,
        }
    
    return None

