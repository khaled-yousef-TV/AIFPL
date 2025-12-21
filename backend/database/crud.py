"""
Database CRUD Operations

Create, Read, Update, Delete operations for the FPL Agent database.
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session

from .models import (
    Settings, GameWeekLog, Decision, Prediction,
    TransferHistory, PerformanceLog, SelectedTeam, DailySnapshot, init_db
)
# Import SavedSquad - must be imported after other models to avoid circular imports
try:
    from .models import SavedSquad
except ImportError:
    # Fallback: try absolute import
    import sys
    import os
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from database.models import SavedSquad

# Verify SavedSquad is available
if 'SavedSquad' not in globals():
    raise ImportError("Failed to import SavedSquad from database.models")

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manager for database operations."""
    
    def __init__(self, db_url: Optional[str] = None):
        """
        Initialize database manager.
        
        Args:
            db_url: Database connection URL (defaults to DATABASE_URL env var or sqlite:///fpl_agent.db)
        """
        import os
        if db_url is None:
            db_url = os.getenv("DATABASE_URL", "sqlite:///fpl_agent.db")
        
        # Log database type for debugging
        if db_url.startswith("sqlite"):
            logger.warning("⚠️  Using SQLite database - data will be lost on deployment! Use PostgreSQL for persistence.")
        elif db_url.startswith("postgresql") or db_url.startswith("postgres"):
            logger.info("✓ Using PostgreSQL database - data will persist across deployments")
        else:
            logger.info(f"Using database: {db_url.split('://')[0] if '://' in db_url else 'unknown'}")
        
        self.engine, self.SessionLocal = init_db(db_url)
    
    def get_session(self) -> Session:
        """Get a new database session."""
        return self.SessionLocal()
    
    # ==================== Settings ====================
    
    def get_setting(self, key: str) -> Optional[str]:
        """Get a setting value."""
        with self.get_session() as session:
            setting = session.query(Settings).filter(Settings.key == key).first()
            return setting.value if setting else None
    
    def set_setting(self, key: str, value: str) -> None:
        """Set a setting value."""
        with self.get_session() as session:
            setting = session.query(Settings).filter(Settings.key == key).first()
            if setting:
                setting.value = value
            else:
                setting = Settings(key=key, value=value)
                session.add(setting)
            session.commit()
    
    def get_all_settings(self) -> Dict[str, str]:
        """Get all settings as dictionary."""
        with self.get_session() as session:
            settings = session.query(Settings).all()
            return {s.key: s.value for s in settings}
    
    # ==================== GameWeek Logs ====================
    
    def create_gameweek_log(self, gameweek: int) -> int:
        """Create a new gameweek log."""
        with self.get_session() as session:
            log = GameWeekLog(gameweek=gameweek, status="pending")
            session.add(log)
            session.commit()
            return log.id
    
    def get_gameweek_log(self, gameweek: int) -> Optional[Dict[str, Any]]:
        """Get gameweek log."""
        with self.get_session() as session:
            log = session.query(GameWeekLog).filter(
                GameWeekLog.gameweek == gameweek
            ).first()
            
            if not log:
                return None
            
            return {
                "id": log.id,
                "gameweek": log.gameweek,
                "status": log.status,
                "created_at": log.created_at,
                "processed_at": log.processed_at,
                "executed_at": log.executed_at,
                "transfers_made": log.transfers_made,
                "captain_set": log.captain_set,
                "predicted_points": log.predicted_points,
                "actual_points": log.actual_points,
            }
    
    def update_gameweek_log(
        self,
        gameweek: int,
        status: Optional[str] = None,
        transfers_made: Optional[int] = None,
        captain_set: Optional[str] = None,
        predicted_points: Optional[float] = None,
        actual_points: Optional[float] = None
    ) -> None:
        """Update gameweek log."""
        with self.get_session() as session:
            log = session.query(GameWeekLog).filter(
                GameWeekLog.gameweek == gameweek
            ).first()
            
            if not log:
                return
            
            if status:
                log.status = status
                if status == "processing":
                    log.processed_at = datetime.utcnow()
                elif status == "completed":
                    log.executed_at = datetime.utcnow()
            
            if transfers_made is not None:
                log.transfers_made = transfers_made
            if captain_set:
                log.captain_set = captain_set
            if predicted_points is not None:
                log.predicted_points = predicted_points
            if actual_points is not None:
                log.actual_points = actual_points
            
            session.commit()
    
    # ==================== Decisions ====================
    
    def log_decision(
        self,
        gameweek: int,
        decision_type: str,
        details: Dict[str, Any],
        reasoning: str
    ) -> int:
        """Log an agent decision."""
        with self.get_session() as session:
            # Get or create gameweek log
            gw_log = session.query(GameWeekLog).filter(
                GameWeekLog.gameweek == gameweek
            ).first()
            
            if not gw_log:
                gw_log = GameWeekLog(gameweek=gameweek)
                session.add(gw_log)
                session.flush()
            
            decision = Decision(
                gameweek_log_id=gw_log.id,
                decision_type=decision_type,
                details=details,
                reasoning=reasoning
            )
            session.add(decision)
            session.commit()
            return decision.id
    
    def mark_decision_executed(
        self,
        decision_id: int,
        result: str = "success"
    ) -> None:
        """Mark a decision as executed."""
        with self.get_session() as session:
            decision = session.query(Decision).get(decision_id)
            if decision:
                decision.executed = True
                decision.executed_at = datetime.utcnow()
                decision.execution_result = result
                session.commit()
    
    def get_decisions(
        self,
        gameweek: Optional[int] = None,
        decision_type: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get decisions with optional filters."""
        with self.get_session() as session:
            query = session.query(Decision)
            
            if gameweek:
                query = query.join(GameWeekLog).filter(
                    GameWeekLog.gameweek == gameweek
                )
            
            if decision_type:
                query = query.filter(Decision.decision_type == decision_type)
            
            decisions = query.order_by(Decision.created_at.desc()).limit(limit).all()
            
            return [
                {
                    "id": d.id,
                    "decision_type": d.decision_type,
                    "details": d.details,
                    "reasoning": d.reasoning,
                    "executed": d.executed,
                    "executed_at": d.executed_at,
                    "created_at": d.created_at,
                }
                for d in decisions
            ]
    
    # ==================== Predictions ====================
    
    def log_predictions(
        self,
        gameweek: int,
        predictions: List[Dict[str, Any]]
    ) -> None:
        """Log player predictions for a gameweek."""
        with self.get_session() as session:
            # Get or create gameweek log
            gw_log = session.query(GameWeekLog).filter(
                GameWeekLog.gameweek == gameweek
            ).first()
            
            if not gw_log:
                gw_log = GameWeekLog(gameweek=gameweek)
                session.add(gw_log)
                session.flush()
            
            for pred in predictions:
                p = Prediction(
                    gameweek_log_id=gw_log.id,
                    player_id=pred["player_id"],
                    player_name=pred.get("player_name"),
                    team=pred.get("team"),
                    position=pred.get("position"),
                    predicted_points=pred["predicted_points"],
                    features=pred.get("features"),
                )
                session.add(p)
            
            session.commit()
    
    def update_actual_points(
        self,
        gameweek: int,
        actual_points: Dict[int, float]
    ) -> None:
        """Update actual points for predictions."""
        with self.get_session() as session:
            gw_log = session.query(GameWeekLog).filter(
                GameWeekLog.gameweek == gameweek
            ).first()
            
            if not gw_log:
                return
            
            predictions = session.query(Prediction).filter(
                Prediction.gameweek_log_id == gw_log.id
            ).all()
            
            for pred in predictions:
                if pred.player_id in actual_points:
                    pred.actual_points = actual_points[pred.player_id]
            
            session.commit()
    
    def get_predictions(
        self,
        gameweek: int,
        top_n: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get predictions for a gameweek."""
        with self.get_session() as session:
            query = session.query(Prediction).join(GameWeekLog).filter(
                GameWeekLog.gameweek == gameweek
            ).order_by(Prediction.predicted_points.desc())
            
            if top_n:
                query = query.limit(top_n)
            
            return [
                {
                    "player_id": p.player_id,
                    "player_name": p.player_name,
                    "team": p.team,
                    "position": p.position,
                    "predicted_points": p.predicted_points,
                    "actual_points": p.actual_points,
                }
                for p in query.all()
            ]
    
    # ==================== Performance ====================
    
    def log_performance(
        self,
        gameweek: int,
        total_points: int,
        overall_rank: int,
        gameweek_rank: int,
        captain_points: int = 0,
        bench_points: int = 0,
        transfer_cost: int = 0,
        average_points: int = 0,
        highest_points: int = 0,
        predictions_accuracy: float = 0.0
    ) -> None:
        """Log gameweek performance."""
        with self.get_session() as session:
            # Check if exists
            existing = session.query(PerformanceLog).filter(
                PerformanceLog.gameweek == gameweek
            ).first()
            
            if existing:
                existing.total_points = total_points
                existing.overall_rank = overall_rank
                existing.gameweek_rank = gameweek_rank
                existing.captain_points = captain_points
                existing.bench_points = bench_points
                existing.transfer_cost = transfer_cost
                existing.average_points = average_points
                existing.highest_points = highest_points
                existing.predictions_accuracy = predictions_accuracy
            else:
                perf = PerformanceLog(
                    gameweek=gameweek,
                    total_points=total_points,
                    overall_rank=overall_rank,
                    gameweek_rank=gameweek_rank,
                    captain_points=captain_points,
                    bench_points=bench_points,
                    transfer_cost=transfer_cost,
                    average_points=average_points,
                    highest_points=highest_points,
                    predictions_accuracy=predictions_accuracy
                )
                session.add(perf)
            
            session.commit()
    
    def get_performance_history(
        self,
        limit: int = 38
    ) -> List[Dict[str, Any]]:
        """Get performance history."""
        with self.get_session() as session:
            perfs = session.query(PerformanceLog).order_by(
                PerformanceLog.gameweek.desc()
            ).limit(limit).all()
            
            return [
                {
                    "gameweek": p.gameweek,
                    "total_points": p.total_points,
                    "overall_rank": p.overall_rank,
                    "gameweek_rank": p.gameweek_rank,
                    "captain_points": p.captain_points,
                    "transfer_cost": p.transfer_cost,
                    "average_points": p.average_points,
                    "predictions_accuracy": p.predictions_accuracy,
                }
                for p in perfs
            ]
    
    # ==================== Selected Teams ====================
    
    def save_selected_team(self, gameweek: int, squad_data: Dict[str, Any]) -> bool:
        """
        Save or update a selected team (suggested squad) for a gameweek.
        
        Args:
            gameweek: Gameweek number
            squad_data: Full SuggestedSquad dictionary (JSON-serializable)
            
        Returns:
            True if saved, False if error
        """
        try:
            with self.get_session() as session:
                existing = session.query(SelectedTeam).filter(
                    SelectedTeam.gameweek == gameweek
                ).first()
                
                if existing:
                    # Update existing
                    existing.squad_data = squad_data
                    existing.saved_at = datetime.utcnow()
                else:
                    # Create new
                    selected_team = SelectedTeam(
                        gameweek=gameweek,
                        squad_data=squad_data,
                        saved_at=datetime.utcnow()
                    )
                    session.add(selected_team)
                
                session.commit()
                logger.info(f"Saved selected team for Gameweek {gameweek}")
                return True
        except Exception as e:
            logger.error(f"Failed to save selected team for GW{gameweek}: {e}")
            return False
    
    def get_selected_team(self, gameweek: int) -> Optional[Dict[str, Any]]:
        """Get selected team for a gameweek."""
        with self.get_session() as session:
            team = session.query(SelectedTeam).filter(
                SelectedTeam.gameweek == gameweek
            ).first()
            
            if team:
                return {
                    "gameweek": team.gameweek,
                    "squad": team.squad_data,
                    "saved_at": team.saved_at.isoformat() if team.saved_at else None
                }
            return None
    
    def get_all_selected_teams(self) -> List[Dict[str, Any]]:
        """Get all selected teams, sorted by gameweek (newest first)."""
        with self.get_session() as session:
            teams = session.query(SelectedTeam).order_by(
                SelectedTeam.gameweek.desc()
            ).all()
            
            return [
                {
                    "gameweek": team.gameweek,
                    "squad": team.squad_data,
                    "saved_at": team.saved_at.isoformat() if team.saved_at else None
                }
                for team in teams
            ]
    
    # ==================== Daily Snapshots ====================
    
    def save_daily_snapshot(self, gameweek: int, squad_data: Dict[str, Any]) -> bool:
        """
        Save or update a daily snapshot for a gameweek.
        This creates a new snapshot entry (doesn't update existing).
        
        Args:
            gameweek: Gameweek number
            squad_data: Full SuggestedSquad dictionary (JSON-serializable)
            
        Returns:
            True if saved, False if error
        """
        try:
            with self.get_session() as session:
                # Create new snapshot (we keep history, but only use latest)
                snapshot = DailySnapshot(
                    gameweek=gameweek,
                    squad_data=squad_data,
                    saved_at=datetime.utcnow()
                )
                session.add(snapshot)
                session.commit()
                logger.info(f"Saved daily snapshot for Gameweek {gameweek}")
                return True
        except Exception as e:
            logger.error(f"Failed to save daily snapshot for GW{gameweek}: {e}")
            return False
    
    def get_latest_daily_snapshot(self, gameweek: int) -> Optional[Dict[str, Any]]:
        """Get the latest daily snapshot for a gameweek."""
        with self.get_session() as session:
            snapshot = session.query(DailySnapshot).filter(
                DailySnapshot.gameweek == gameweek
            ).order_by(DailySnapshot.saved_at.desc()).first()
            
            if snapshot:
                return {
                    "gameweek": snapshot.gameweek,
                    "squad": snapshot.squad_data,
                    "saved_at": snapshot.saved_at.isoformat() if snapshot.saved_at else None
                }
            return None
    
    # ==================== Saved Squads (User-saved with custom names) ====================
    
    def save_saved_squad(self, name: str, squad_data: Dict[str, Any]) -> bool:
        """
        Save or update a user-saved squad with a custom name.
        
        Args:
            name: Custom name for the squad
            squad_data: Full squad dictionary (formation, starting_xi, bench, captain, etc.)
            
        Returns:
            True if saved, False if error
        """
        try:
            with self.get_session() as session:
                existing = session.query(SavedSquad).filter(
                    SavedSquad.name == name
                ).first()
                
                if existing:
                    # Update existing
                    existing.squad_data = squad_data
                    existing.updated_at = datetime.utcnow()
                else:
                    # Create new
                    saved_squad = SavedSquad(
                        name=name,
                        squad_data=squad_data,
                        saved_at=datetime.utcnow(),
                        updated_at=datetime.utcnow()
                    )
                    session.add(saved_squad)
                
                session.commit()
                logger.info(f"Saved squad: '{name}'")
                return True
        except Exception as e:
            logger.error(f"Failed to save squad '{name}': {e}")
            return False
    
    def get_saved_squad(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a saved squad by name."""
        with self.get_session() as session:
            squad = session.query(SavedSquad).filter(
                SavedSquad.name == name
            ).first()
            
            if squad:
                return {
                    "id": squad.id,
                    "name": squad.name,
                    "squad": squad.squad_data,
                    "saved_at": squad.saved_at.isoformat() if squad.saved_at else None,
                    "updated_at": squad.updated_at.isoformat() if squad.updated_at else None
                }
            return None
    
    def get_all_saved_squads(self) -> List[Dict[str, Any]]:
        """Get all saved squads, sorted by most recently updated first."""
        with self.get_session() as session:
            squads = session.query(SavedSquad).order_by(
                SavedSquad.updated_at.desc()
            ).all()
            
            return [
                {
                    "id": squad.id,
                    "name": squad.name,
                    "squad": squad.squad_data,
                    "saved_at": squad.saved_at.isoformat() if squad.saved_at else None,
                    "updated_at": squad.updated_at.isoformat() if squad.updated_at else None
                }
                for squad in squads
            ]
    
    def delete_saved_squad(self, name: str) -> bool:
        """Delete a saved squad by name."""
        try:
            with self.get_session() as session:
                squad = session.query(SavedSquad).filter(
                    SavedSquad.name == name
                ).first()
                
                if squad:
                    session.delete(squad)
                    session.commit()
                    logger.info(f"Deleted saved squad: '{name}'")
                    return True
                else:
                    logger.warning(f"Saved squad '{name}' not found for deletion")
                    return False
        except Exception as e:
            logger.error(f"Failed to delete saved squad '{name}': {e}")
            return False
    
    def delete_saved_squad_by_id(self, squad_id: int) -> bool:
        """Delete a saved squad by ID."""
        try:
            with self.get_session() as session:
                squad = session.query(SavedSquad).filter(
                    SavedSquad.id == squad_id
                ).first()
                
                if squad:
                    session.delete(squad)
                    session.commit()
                    logger.info(f"Deleted saved squad ID: {squad_id}")
                    return True
                else:
                    logger.warning(f"Saved squad ID {squad_id} not found for deletion")
                    return False
        except Exception as e:
            logger.error(f"Failed to delete saved squad ID {squad_id}: {e}")
            return False


