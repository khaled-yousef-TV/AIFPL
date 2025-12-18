"""
Team Trend & Reversal Signals

Goal:
- Detect "bounce-back" spots where a strong team has underperformed recently.
- Detect "overheat" spots where a weaker team has overperformed recently.

We only use public FPL data:
- Team strength from bootstrap-static.
- Finished fixtures (scores) from /api/fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from fpl.models import Team, Fixture


@dataclass(frozen=True)
class TeamTrend:
    team_id: int
    short_name: str
    strength: int
    played: int
    season_ppm: float
    recent_ppm: float
    momentum: float  # recent_ppm - previous_window_ppm
    reversal_score: float  # higher => stronger bounce-back signal


def _fixture_points(scored: int, conceded: int) -> int:
    if scored > conceded:
        return 3
    if scored == conceded:
        return 1
    return 0


def compute_team_trends(
    teams: List[Team],
    fixtures: List[Fixture],
    window: int = 6,
    previous_window: int = 6,
    now: Optional[datetime] = None,
) -> Dict[int, TeamTrend]:
    """
    Compute per-team trend signals from finished fixtures.

    - season_ppm: points per match across all finished fixtures so far
    - recent_ppm: points per match over last `window` finished fixtures
    - momentum: recent_ppm - prev_ppm (prev_ppm over the window before the recent one)
    - reversal_score: strong teams underperforming recently => higher score
    """
    _ = now or datetime.utcnow()

    by_team_id: Dict[int, Dict[str, List[Tuple[datetime, int]]]] = {}
    for t in teams:
        by_team_id[t.id] = {"points": []}

    # Gather finished fixture points per team, ordered by kickoff_time
    for f in fixtures:
        if not f.finished:
            continue
        if f.kickoff_time is None:
            continue
        if f.team_h_score is None or f.team_a_score is None:
            continue

        h_pts = _fixture_points(f.team_h_score, f.team_a_score)
        a_pts = _fixture_points(f.team_a_score, f.team_h_score)

        if f.team_h in by_team_id:
            by_team_id[f.team_h]["points"].append((f.kickoff_time, h_pts))
        if f.team_a in by_team_id:
            by_team_id[f.team_a]["points"].append((f.kickoff_time, a_pts))

    # Strength normalization (simple min/max to keep it stable)
    strengths = [t.strength for t in teams if isinstance(t.strength, int)]
    s_min = min(strengths) if strengths else 1
    s_max = max(strengths) if strengths else 5
    s_rng = max(1, s_max - s_min)

    def norm_strength(s: int) -> float:
        return (s - s_min) / s_rng  # 0..1

    trends: Dict[int, TeamTrend] = {}

    team_by_id = {t.id: t for t in teams}

    for team_id, bucket in by_team_id.items():
        pts_series = sorted(bucket["points"], key=lambda x: x[0])
        pts_only = [p for _, p in pts_series]

        played = len(pts_only)
        season_ppm = (sum(pts_only) / played) if played else 0.0

        recent = pts_only[-window:] if window > 0 else []
        recent_ppm = (sum(recent) / len(recent)) if recent else season_ppm

        prev_end = max(0, played - window)
        prev_start = max(0, prev_end - previous_window)
        prev = pts_only[prev_start:prev_end]
        prev_ppm = (sum(prev) / len(prev)) if prev else season_ppm

        momentum = recent_ppm - prev_ppm

        t = team_by_id.get(team_id)
        if not t:
            continue

        # Reversal score:
        # - higher strength increases score
        # - underperforming recently (season_ppm - recent_ppm) increases score
        # - small momentum boosts (if turning upward)
        str_n = norm_strength(t.strength)
        underperf = season_ppm - recent_ppm  # positive => underperforming recently
        reversal_score = (str_n * 1.2) + (underperf * 0.9) + (max(0.0, momentum) * 0.4)

        trends[team_id] = TeamTrend(
            team_id=team_id,
            short_name=t.short_name,
            strength=t.strength,
            played=played,
            season_ppm=round(season_ppm, 3),
            recent_ppm=round(recent_ppm, 3),
            momentum=round(momentum, 3),
            reversal_score=round(reversal_score, 3),
        )

    return trends


