"""
API Routes Package

All route modules are aggregated here for easy importing into main.py.
"""

from .health import router as health_router, initialize_health_router
from .chips import router as chips_router, initialize_chips_router

__all__ = [
    'health_router',
    'initialize_health_router',
    'chips_router', 
    'initialize_chips_router',
]

