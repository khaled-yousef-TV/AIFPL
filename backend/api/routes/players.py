"""
Player search endpoints.
"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException

from services.dependencies import get_dependencies
from data.european_teams import assess_rotation_risk

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/search")
async def search_players(q: str = "", position: Optional[str] = None, limit: int = 50):
    """Search players by name or team for squad input."""
    try:
        deps = get_dependencies()
        fpl_client = deps.fpl_client
        
        # Get players with error handling
        try:
            players = fpl_client.get_players()
        except Exception as e:
            logger.error(f"Failed to get players from FPL API: {e}")
            raise HTTPException(status_code=503, detail=f"FPL API unavailable: {str(e)}")
        
        # Get teams with error handling
        try:
            teams = fpl_client.get_teams()
        except Exception as e:
            logger.error(f"Failed to get teams from FPL API: {e}")
            raise HTTPException(status_code=503, detail=f"FPL API unavailable: {str(e)}")
        
        team_names = {t.id: t.short_name for t in teams}

        # Rotation/EU badges are based on the upcoming gameweek context
        try:
            next_gw = fpl_client.get_next_gameweek()
            fixtures = fpl_client.get_fixtures(gameweek=next_gw.id if next_gw else None)
            gw_deadline = next_gw.deadline_time if next_gw else datetime.now()
        except Exception as e:
            logger.warning(f"Failed to get gameweek/fixtures, using defaults: {e}")
            fixtures = []
            gw_deadline = datetime.now()

        fixture_info: Dict[int, Dict[str, Any]] = {}
        for f in fixtures:
            fixture_info[f.team_h] = {"difficulty": f.team_h_difficulty, "is_home": True}
            fixture_info[f.team_a] = {"difficulty": f.team_a_difficulty, "is_home": False}

        q_lower = (q or "").strip().lower()
        limit = max(1, min(100, int(limit or 50)))

        # Filter by position first
        filtered = players
        if position:
            filtered = [p for p in filtered if p.position == position]

        # If q is empty, return cheapest players for that position (bench fodder)
        if not q_lower:
            filtered.sort(key=lambda p: (p.price, -p.minutes))
            filtered = filtered[: min(20, limit)]
        else:
            # Allow searching by team name/short code too
            team_match_ids = set()
            for t in teams:
                t_name = (t.name or "").lower()
                t_short = (t.short_name or "").lower()
                if q_lower in t_name or q_lower == t_short or q_lower in t_short:
                    team_match_ids.add(t.id)

            # Small alias support (common fan names)
            if q_lower in {"spurs", "tottenham", "tot"}:
                for t in teams:
                    if (t.short_name or "").lower() == "tot" or "spurs" in (t.name or "").lower():
                        team_match_ids.add(t.id)

            ranked = []
            for p in filtered:
                web = p.web_name.lower()
                full = p.full_name.lower()
                name_hit = (q_lower in web) or (q_lower in full)
                team_hit = p.team in team_match_ids
                if not (name_hit or team_hit):
                    continue

                rank = 0
                if web == q_lower or full == q_lower:
                    rank += 3
                if name_hit:
                    rank += 2
                if team_hit:
                    rank += 1

                ranked.append((-rank, -p.minutes, p.price, p.web_name, p))

            ranked.sort()
            filtered = [x[-1] for x in ranked][:limit]

        results = []
        for p in filtered:
            try:
                team_short = team_names.get(p.team, "???")
                fix = fixture_info.get(p.team, {})
                difficulty = fix.get("difficulty", 3)
                try:
                    rotation = assess_rotation_risk(team_short, gw_deadline, difficulty)
                    rotation_risk = rotation.risk_level
                    european_comp = rotation.competition
                except Exception as rot_error:
                    logger.warning(f"Rotation risk assessment failed for {team_short}: {rot_error}")
                    rotation_risk = "low"
                    european_comp = None
                
                results.append({
                    "id": p.id,
                    "name": p.web_name,
                    "full_name": p.full_name,
                    "team": team_short,
                    "position": p.position,
                    "price": p.price,
                    "minutes": p.minutes,
                    "status": p.status,
                    "rotation_risk": rotation_risk,
                    "european_comp": european_comp,
                })
            except Exception as player_error:
                logger.warning(f"Error processing player {p.id}: {player_error}")
                continue

        return {"players": results}
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"Search error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

