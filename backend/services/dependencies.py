"""
Shared dependencies for the application.

This module holds initialized services that are shared across routes.
Initialized once at app startup and accessed via get_dependencies().
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class Dependencies:
    """Container for all shared dependencies."""
    fpl_client: any
    predictor_heuristic: any
    predictor_form: any
    predictor_fixture: any
    feature_engineer: any
    betting_odds_client: any
    db_manager: any


# Global dependencies instance - initialized by init_dependencies()
_deps: Optional[Dependencies] = None


def init_dependencies():
    """Initialize all dependencies. Called once at app startup."""
    global _deps
    
    if _deps is not None:
        return _deps
    
    # Import here to avoid circular imports
    from fpl.client import FPLClient
    from ml.features import FeatureEngineer
    from ml.predictor import HeuristicPredictor, FormPredictor, FixturePredictor
    from data.betting_odds import BettingOddsClient
    from database.crud import DatabaseManager
    
    logger.info("Initializing application dependencies...")
    
    # Initialize FPL client
    fpl_client = FPLClient(auth=None)
    
    # Initialize predictors
    predictor_heuristic = HeuristicPredictor()
    predictor_form = FormPredictor()
    predictor_fixture = FixturePredictor()
    
    # Initialize feature engineer
    feature_eng = FeatureEngineer(fpl_client)
    
    # Initialize betting odds client
    logger.info(f"Initializing BettingOddsClient...")
    betting_odds_client = BettingOddsClient()
    logger.info(f"BettingOddsClient: enabled={betting_odds_client.enabled}")
    
    # Initialize database manager
    db_manager = DatabaseManager()
    
    _deps = Dependencies(
        fpl_client=fpl_client,
        predictor_heuristic=predictor_heuristic,
        predictor_form=predictor_form,
        predictor_fixture=predictor_fixture,
        feature_engineer=feature_eng,
        betting_odds_client=betting_odds_client,
        db_manager=db_manager,
    )
    
    logger.info("All dependencies initialized successfully")
    return _deps


def get_dependencies() -> Dependencies:
    """Get initialized dependencies. Raises if not initialized."""
    if _deps is None:
        raise RuntimeError("Dependencies not initialized. Call init_dependencies() first.")
    return _deps

