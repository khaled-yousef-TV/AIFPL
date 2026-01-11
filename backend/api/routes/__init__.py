"""
API Routes Package

All route modules are aggregated here for easy importing into main.py.
"""

from .health import router as health_router, initialize_health_router
from .chips import router as chips_router, initialize_chips_router
from .gameweek import router as gameweek_router
from .players import router as players_router
from .tasks import router as tasks_router
from .fpl_teams import router as fpl_teams_router
from .squads import router as squads_router

__all__ = [
    # Health
    'health_router',
    'initialize_health_router',
    # Chips
    'chips_router', 
    'initialize_chips_router',
    # Gameweek
    'gameweek_router',
    # Players
    'players_router',
    # Tasks
    'tasks_router',
    # FPL Teams
    'fpl_teams_router',
    # Squads
    'squads_router',
]
