"""
Database Models

SQLAlchemy models for storing agent decisions, predictions, and settings.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, JSON, Text,
    ForeignKey, create_engine
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class Settings(Base):
    """User settings for the agent."""
    __tablename__ = "settings"
    
    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Common settings:
    # - fpl_email: FPL login email
    # - fpl_team_id: FPL team ID
    # - auto_execute: Whether to auto-execute decisions
    # - differential_mode: Prefer differential picks
    # - notification_email: Email for notifications
    # - risk_tolerance: low/medium/high


class GameWeekLog(Base):
    """Log of agent activity per gameweek."""
    __tablename__ = "gameweek_logs"
    
    id = Column(Integer, primary_key=True)
    gameweek = Column(Integer, nullable=False)
    
    # Status
    status = Column(String(50), default="pending")  # pending, processing, completed, failed
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)
    executed_at = Column(DateTime)
    
    # Summary
    transfers_made = Column(Integer, default=0)
    captain_set = Column(String(100))
    predicted_points = Column(Float)
    actual_points = Column(Float)
    
    # Relationships
    decisions = relationship("Decision", back_populates="gameweek_log")
    predictions = relationship("Prediction", back_populates="gameweek_log")


class Decision(Base):
    """Record of agent decisions."""
    __tablename__ = "decisions"
    
    id = Column(Integer, primary_key=True)
    gameweek_log_id = Column(Integer, ForeignKey("gameweek_logs.id"))
    
    # Decision type
    decision_type = Column(String(50), nullable=False)  # transfer, captain, lineup, chip
    
    # Details (JSON)
    details = Column(JSON)
    
    # Reasoning
    reasoning = Column(Text)
    
    # Execution
    executed = Column(Boolean, default=False)
    executed_at = Column(DateTime)
    execution_result = Column(Text)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    gameweek_log = relationship("GameWeekLog", back_populates="decisions")


class Prediction(Base):
    """Player predictions per gameweek."""
    __tablename__ = "predictions"
    
    id = Column(Integer, primary_key=True)
    gameweek_log_id = Column(Integer, ForeignKey("gameweek_logs.id"))
    
    # Player info
    player_id = Column(Integer, nullable=False)
    player_name = Column(String(100))
    team = Column(String(50))
    position = Column(String(10))
    
    # Prediction
    predicted_points = Column(Float)
    
    # Actual (filled in after gameweek)
    actual_points = Column(Float)
    
    # Features used (JSON)
    features = Column(JSON)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    gameweek_log = relationship("GameWeekLog", back_populates="predictions")


class TransferHistory(Base):
    """Record of actual transfers made."""
    __tablename__ = "transfer_history"
    
    id = Column(Integer, primary_key=True)
    gameweek = Column(Integer, nullable=False)
    
    # Transfer details
    player_out_id = Column(Integer)
    player_out_name = Column(String(100))
    player_in_id = Column(Integer)
    player_in_name = Column(String(100))
    
    # Prices
    selling_price = Column(Float)
    purchase_price = Column(Float)
    
    # Was it a hit?
    is_hit = Column(Boolean, default=False)
    
    # Timestamps
    executed_at = Column(DateTime, default=datetime.utcnow)


class PerformanceLog(Base):
    """Track overall agent performance."""
    __tablename__ = "performance_log"
    
    id = Column(Integer, primary_key=True)
    gameweek = Column(Integer, nullable=False, unique=True)
    
    # Points
    total_points = Column(Integer)
    captain_points = Column(Integer)
    bench_points = Column(Integer)
    transfer_cost = Column(Integer, default=0)
    
    # Ranking
    overall_rank = Column(Integer)
    gameweek_rank = Column(Integer)
    
    # Comparison
    average_points = Column(Integer)
    highest_points = Column(Integer)
    
    # Analysis
    predictions_accuracy = Column(Float)  # MAE of predictions
    
    # Timestamps
    recorded_at = Column(DateTime, default=datetime.utcnow)


class SelectedTeam(Base):
    """Store final suggested squad ('team of the week') saved 30 minutes before deadline."""
    __tablename__ = "selected_teams"
    
    id = Column(Integer, primary_key=True)
    gameweek = Column(Integer, nullable=False, unique=True, index=True)
    
    # Squad data (stored as JSON)
    squad_data = Column(JSON, nullable=False)  # Full SuggestedSquad dict
    
    # Timestamps
    saved_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f"<SelectedTeam(gameweek={self.gameweek}, saved_at={self.saved_at})>"


class DailySnapshot(Base):
    """Store daily snapshot of current combined suggestion for active gameweek."""
    __tablename__ = "daily_snapshots"
    
    id = Column(Integer, primary_key=True)
    gameweek = Column(Integer, nullable=False, index=True)
    
    # Squad data (stored as JSON)
    squad_data = Column(JSON, nullable=False)  # Full SuggestedSquad dict
    
    # Timestamps
    saved_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    def __repr__(self):
        return f"<DailySnapshot(gameweek={self.gameweek}, saved_at={self.saved_at})>"


class FplTeam(Base):
    """Store FPL team IDs imported by users for quick squad imports."""
    __tablename__ = "fpl_teams"
    
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, nullable=False, unique=True, index=True)  # FPL team ID
    team_name = Column(String(200), nullable=False)  # Team name from FPL API
    
    # Timestamps
    saved_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    last_imported = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False, index=True)
    
    def __repr__(self):
        return f"<FplTeam(id={self.id}, team_id={self.team_id}, team_name='{self.team_name}', last_imported={self.last_imported})>"


class TripleCaptainRecommendations(Base):
    """Store Triple Captain recommendations calculated daily."""
    __tablename__ = "triple_captain_recommendations"
    
    id = Column(Integer, primary_key=True)
    gameweek = Column(Integer, nullable=False, index=True)  # Gameweek these recommendations are for
    
    # Recommendations data (stored as JSON)
    recommendations = Column(JSON, nullable=False)  # List of recommendation dicts
    
    # Metadata
    gameweek_range = Column(Integer, default=5)  # Number of gameweeks analyzed
    total_recommendations = Column(Integer, default=0)  # Number of recommendations
    
    # Timestamps
    calculated_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    def __repr__(self):
        return f"<TripleCaptainRecommendations(gameweek={self.gameweek}, calculated_at={self.calculated_at})>"


class Task(Base):
    """Store background task status and progress."""
    __tablename__ = "tasks"
    
    id = Column(Integer, primary_key=True)
    task_id = Column(String(100), nullable=False, unique=True, index=True)  # Client-generated unique ID
    task_type = Column(String(50), nullable=False, index=True)  # daily_snapshot, triple_captain, etc.
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, index=True)  # pending, running, completed, failed
    progress = Column(Integer, default=0)  # 0-100
    error = Column(Text, nullable=True)  # Error message if failed
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True, index=True)
    
    def __repr__(self):
        return f"<Task(task_id='{self.task_id}', type='{self.task_type}', status='{self.status}')>"


class WildcardTrajectory(Base):
    """Store wildcard trajectory results."""
    __tablename__ = "wildcard_trajectories"
    
    id = Column(Integer, primary_key=True)
    
    # Trajectory data (stored as JSON)
    trajectory_data = Column(JSON, nullable=False)  # Full WildcardTrajectory dict
    
    # Metadata
    budget = Column(Float, nullable=False)  # Budget used
    horizon = Column(Integer, nullable=False)  # Number of gameweeks
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    def __repr__(self):
        return f"<WildcardTrajectory(id={self.id}, budget={self.budget}, horizon={self.horizon}, created_at={self.created_at})>"


def init_db(db_url: Optional[str] = None):
    """
    Initialize the database.
    
    Args:
        db_url: Database connection URL (defaults to DATABASE_URL env var or sqlite:///fpl_agent.db)
        
    Returns:
        Tuple of (engine, SessionLocal)
    """
    import os
    import logging
    logger_db = logging.getLogger(__name__)
    
    if db_url is None:
        db_url = os.getenv("DATABASE_URL", "sqlite:///fpl_agent.db")
    
    # Log database initialization
    if db_url.startswith("sqlite"):
        logger_db.warning("⚠️  Initializing SQLite database - NOT PERSISTENT on Render!")
        logger_db.warning("⚠️  Set DATABASE_URL to PostgreSQL connection string for persistence")
    elif db_url.startswith("postgresql") or db_url.startswith("postgres"):
        logger_db.info("✓ Initializing PostgreSQL database - persistent storage")
    else:
        logger_db.info(f"Initializing database: {db_url.split('://')[0] if '://' in db_url else 'unknown'}")
    
    # Handle PostgreSQL URL format (Render provides postgres://, SQLAlchemy needs postgresql://)
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
        logger_db.info("Converted postgres:// to postgresql:// for SQLAlchemy compatibility")
    
    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal


