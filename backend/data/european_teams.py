"""
European Competition Data

Tracks which PL teams are in European competitions and their fixture congestion.
This file is designed to be easily updated each season.
"""

from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass
import json
import os
import logging

logger = logging.getLogger(__name__)

# ============================================================
# SEASON CONFIGURATION - UPDATE THIS EACH SEASON
# ============================================================
CURRENT_SEASON = "2025-26"

# Teams in European competitions
# Update this at the start of each season based on:
# - UCL: Top 4 + European Performance Spot (if applicable) + UCL/UEL winners
# - UEL: 5th-6th place + FA Cup winner (if not already qualified)
# - UECL: League Cup winner / other qualifiers
EUROPEAN_TEAMS_BY_SEASON = {
    "2025-26": {
        # Champions League (6 English teams this season!)
        # Liverpool (1st), Arsenal (2nd), Man City (3rd), Chelsea (4th)
        # Tottenham (UEL winners), Newcastle (European Performance Spot - 5th)
        "UCL": ["LIV", "ARS", "MCI", "CHE", "TOT", "NEW"],
        
        # Europa League
        # Aston Villa (6th), Nottingham Forest (promoted from UECL after Crystal Palace demotion)
        "UEL": ["AVL", "NFO"],
        
        # Conference League  
        # Crystal Palace (FA Cup winners - demoted from UEL due to multi-club ownership)
        "UECL": ["CRY"],
    },
    # Add future seasons here as they become known
    "2026-27": {
        "UCL": [],  # To be determined
        "UEL": [],
        "UECL": [],
    },
}

# European matchweek dates by season
# UCL new format (2024+): League phase with 8 matchdays Sep-Jan
# UEL: League phase with 8 matchdays Sep-Jan
# UECL: League phase with 6 matchdays Sep-Dec
EUROPEAN_MATCHWEEKS_BY_SEASON = {
    "2025-26": {
        # December 2025 - Matchday 6
        "2025-12-09": ["UCL"],
        "2025-12-10": ["UCL"],
        "2025-12-11": ["UCL", "UEL"],
        "2025-12-12": ["UEL", "UECL"],
        "2025-12-18": ["UECL"],
        "2025-12-19": ["UECL"],
        
        # January 2026 - Matchday 7 & 8
        "2026-01-21": ["UCL"],
        "2026-01-22": ["UCL", "UEL"],
        "2026-01-23": ["UEL", "UECL"],
        "2026-01-28": ["UCL"],
        "2026-01-29": ["UCL", "UEL"],
        "2026-01-30": ["UEL"],
        
        # February 2026 - Knockout playoffs
        "2026-02-11": ["UCL", "UEL"],
        "2026-02-12": ["UCL", "UEL"],
        "2026-02-13": ["UECL"],
        "2026-02-18": ["UCL", "UEL"],
        "2026-02-19": ["UCL", "UEL"],
        "2026-02-20": ["UECL"],
        
        # March 2026 - Round of 16
        "2026-03-04": ["UCL"],
        "2026-03-05": ["UCL", "UEL"],
        "2026-03-06": ["UEL", "UECL"],
        "2026-03-11": ["UCL"],
        "2026-03-12": ["UCL", "UEL"],
        "2026-03-13": ["UEL", "UECL"],
        
        # April 2026 - Quarter-finals
        "2026-04-08": ["UCL", "UEL"],
        "2026-04-09": ["UCL", "UEL"],
        "2026-04-10": ["UECL"],
        "2026-04-15": ["UCL", "UEL"],
        "2026-04-16": ["UCL", "UEL"],
        "2026-04-17": ["UECL"],
    },
}

# Competition importance (affects rotation likelihood)
COMPETITION_IMPORTANCE = {
    "UCL": 1.0,    # Highest - managers prioritize UCL
    "UEL": 0.8,    # High - still important
    "UECL": 0.6,   # Medium - less rotation
}

# ============================================================
# DYNAMIC TEAM LOOKUP
# ============================================================

def get_current_season() -> str:
    """
    Determine current season based on date.
    Season runs Aug-May, so:
    - Aug 2025 - May 2026 = "2025-26"
    """
    now = datetime.now()
    year = now.year
    month = now.month
    
    if month >= 8:  # Aug-Dec = first half of season
        return f"{year}-{str(year + 1)[2:]}"
    else:  # Jan-Jul = second half of season
        return f"{year - 1}-{str(year)[2:]}"


def get_european_teams(season: Optional[str] = None) -> Dict[str, str]:
    """
    Get mapping of team -> competition for a season.
    
    Returns:
        Dict mapping team short name to competition code
    """
    if season is None:
        season = get_current_season()
    
    season_data = EUROPEAN_TEAMS_BY_SEASON.get(season, {})
    
    teams = {}
    for comp, team_list in season_data.items():
        for team in team_list:
            teams[team] = comp
    
    return teams


def get_european_matchweeks(season: Optional[str] = None) -> Dict[str, List[str]]:
    """Get European matchweek dates for a season."""
    if season is None:
        season = get_current_season()
    
    return EUROPEAN_MATCHWEEKS_BY_SEASON.get(season, {})


# ============================================================
# ROTATION RISK ASSESSMENT
# ============================================================

@dataclass
class RotationRisk:
    """Rotation risk assessment for a team."""
    team: str
    competition: Optional[str]
    has_european_game: bool
    days_until_euro: Optional[int]
    days_since_euro: Optional[int]
    risk_level: str  # "high", "medium", "low", "none"
    risk_factor: float  # 0.0 to 1.0, where 1.0 = high rotation risk
    reason: str


def get_european_competition(team_short_name: str, season: Optional[str] = None) -> Optional[str]:
    """Get the European competition a team is in."""
    teams = get_european_teams(season)
    return teams.get(team_short_name)


def get_nearby_european_dates(pl_fixture_date: datetime, days_range: int = 4, season: Optional[str] = None) -> List[str]:
    """Get European matchweek dates within range of a PL fixture."""
    matchweeks = get_european_matchweeks(season)
    
    nearby = []
    # Make pl_fixture_date timezone-naive for comparison
    if pl_fixture_date.tzinfo is not None:
        pl_fixture_date = pl_fixture_date.replace(tzinfo=None)
    
    for date_str in matchweeks.keys():
        try:
            euro_date = datetime.strptime(date_str, "%Y-%m-%d")
            diff = abs((pl_fixture_date - euro_date).days)
            if diff <= days_range:
                nearby.append(date_str)
        except ValueError:
            continue
    
    return nearby


def assess_rotation_risk(
    team_short_name: str,
    pl_fixture_date: Optional[datetime] = None,
    opponent_difficulty: int = 3,
    season: Optional[str] = None
) -> RotationRisk:
    """
    Assess rotation risk for a team.
    
    Args:
        team_short_name: Team short name (e.g., "ARS")
        pl_fixture_date: Date of the PL fixture
        opponent_difficulty: FDR of the PL opponent (1-5)
        season: Season string (e.g., "2025-26"), defaults to current
    
    Returns:
        RotationRisk assessment
    """
    competition = get_european_competition(team_short_name, season)
    matchweeks = get_european_matchweeks(season)
    
    # Not in Europe - no rotation risk
    if not competition:
        return RotationRisk(
            team=team_short_name,
            competition=None,
            has_european_game=False,
            days_until_euro=None,
            days_since_euro=None,
            risk_level="none",
            risk_factor=0.0,
            reason="Not in European competition"
        )
    
    # Default to current date if not provided
    if pl_fixture_date is None:
        pl_fixture_date = datetime.now()
    
    # Make pl_fixture_date timezone-naive for comparison
    if pl_fixture_date.tzinfo is not None:
        pl_fixture_date = pl_fixture_date.replace(tzinfo=None)
    
    # Check for nearby European games
    nearby_dates = get_nearby_european_dates(pl_fixture_date, days_range=5, season=season)
    
    # Filter to dates that match this team's competition
    relevant_dates = []
    for date_str in nearby_dates:
        comps = matchweeks.get(date_str, [])
        if competition in comps:
            relevant_dates.append(datetime.strptime(date_str, "%Y-%m-%d"))
    
    if not relevant_dates:
        return RotationRisk(
            team=team_short_name,
            competition=competition,
            has_european_game=False,
            days_until_euro=None,
            days_since_euro=None,
            risk_level="low",
            risk_factor=0.1,
            reason=f"In {competition} but no nearby European fixture"
        )
    
    # Calculate days to/from nearest European game
    days_until = None
    days_since = None
    
    for euro_date in relevant_dates:
        diff = (euro_date - pl_fixture_date).days
        if diff > 0:  # Future game
            if days_until is None or diff < days_until:
                days_until = diff
        elif diff < 0:  # Past game
            if days_since is None or abs(diff) < days_since:
                days_since = abs(diff)
    
    # Calculate rotation risk
    risk_factor = 0.0
    reasons = []
    
    # Base risk from competition importance
    comp_importance = COMPETITION_IMPORTANCE.get(competition, 0.5)
    
    # Risk from upcoming European game (managers rest players before big games)
    if days_until is not None:
        if days_until <= 2:
            risk_factor += 0.5 * comp_importance
            reasons.append(f"{competition} in {days_until}d")
        elif days_until <= 4:
            risk_factor += 0.3 * comp_importance
            reasons.append(f"{competition} in {days_until}d")
    
    # Risk from recent European game (fatigue/recovery)
    if days_since is not None:
        if days_since <= 2:
            risk_factor += 0.3 * comp_importance
            reasons.append(f"{competition} was {days_since}d ago")
        elif days_since <= 4:
            risk_factor += 0.15 * comp_importance
    
    # Lower risk against tough opponents (less likely to rotate)
    if opponent_difficulty >= 4:
        risk_factor *= 0.5
        reasons.append("Tough opponent - less rotation")
    # Higher risk against easy opponents
    elif opponent_difficulty <= 2:
        risk_factor *= 1.3
        reasons.append("Easy opponent - more rotation likely")
    
    # Cap at 1.0
    risk_factor = min(1.0, risk_factor)
    
    # Determine risk level
    if risk_factor >= 0.5:
        risk_level = "high"
    elif risk_factor >= 0.25:
        risk_level = "medium"
    elif risk_factor > 0:
        risk_level = "low"
    else:
        risk_level = "none"
    
    reason = " • ".join(reasons) if reasons else f"In {competition}"
    
    return RotationRisk(
        team=team_short_name,
        competition=competition,
        has_european_game=True,
        days_until_euro=days_until,
        days_since_euro=days_since,
        risk_level=risk_level,
        risk_factor=risk_factor,
        reason=reason
    )


def get_all_rotation_risks(pl_fixture_date: Optional[datetime] = None, season: Optional[str] = None) -> Dict[str, RotationRisk]:
    """Get rotation risk for all European teams."""
    teams = get_european_teams(season)
    risks = {}
    for team in teams.keys():
        risks[team] = assess_rotation_risk(team, pl_fixture_date, season=season)
    return risks


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def print_european_teams(season: Optional[str] = None):
    """Print current European teams for debugging."""
    if season is None:
        season = get_current_season()
    
    print(f"\n=== European Teams for {season} ===")
    season_data = EUROPEAN_TEAMS_BY_SEASON.get(season, {})
    
    for comp in ["UCL", "UEL", "UECL"]:
        teams = season_data.get(comp, [])
        print(f"{comp}: {', '.join(teams) if teams else 'None'}")


if __name__ == "__main__":
    # Quick test
    print_european_teams()
    print(f"\nCurrent season detected: {get_current_season()}")
