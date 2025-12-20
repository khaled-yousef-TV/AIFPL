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
    """Store suggested squad ('team of the week') for each gameweek."""
    __tablename__ = "selected_teams"
    
    id = Column(Integer, primary_key=True)
    gameweek = Column(Integer, nullable=False, unique=True, index=True)
    
    # Squad data (stored as JSON)
    squad_data = Column(JSON, nullable=False)  # Full SuggestedSquad dict
    
    # Timestamps
    saved_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f"<SelectedTeam(gameweek={self.gameweek}, saved_at={self.saved_at})>"


def init_db(db_url: str = "sqlite:///fpl_agent.db"):
    """
    Initialize the database.
    
    Args:
        db_url: Database connection URL
        
    Returns:
        Tuple of (engine, SessionLocal)
    """
    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal


