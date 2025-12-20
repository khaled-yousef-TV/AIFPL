"""
FPL Agent Scheduler

Automated jobs for the FPL AI Agent.
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpl.client import FPLClient
from fpl.auth import FPLAuth
from ml.predictor import HeuristicPredictor
from ml.features import FeatureEngineer
from engine.captain import CaptainPicker
from engine.lineup import LineupOptimizer
from engine.transfers import TransferEngine
from database.crud import DatabaseManager

logger = logging.getLogger(__name__)


class FPLAgentScheduler:
    """
    Scheduler for FPL Agent automated tasks.
    
    Jobs:
    - Pre-deadline: Run predictions and execute changes
    - Post-gameweek: Update actual points and calculate accuracy
    - Daily: Check for price changes and injuries
    """
    
    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        auto_execute: bool = False
    ):
        """
        Initialize the scheduler.
        
        Args:
            db: Database manager instance
            auto_execute: Whether to automatically execute decisions
        """
        self.scheduler = BackgroundScheduler()
        self.db = db or DatabaseManager()
        self.auto_execute = auto_execute
        
        # FPL components
        self.fpl_auth = FPLAuth()
        self.fpl_client = FPLClient(auth=self.fpl_auth)
        self.predictor = HeuristicPredictor()
        self.feature_eng = FeatureEngineer(self.fpl_client)
        
        # Decision engines
        self.captain_picker = CaptainPicker()
        self.lineup_optimizer = LineupOptimizer()
        self.transfer_engine = TransferEngine()
        
        self._setup_jobs()
    
    def _setup_jobs(self) -> None:
        """Set up scheduled jobs."""
        # Run predictions every day at 8 AM
        self.scheduler.add_job(
            self.run_daily_update,
            CronTrigger(hour=8, minute=0),
            id="daily_update",
            name="Daily Update",
            replace_existing=True
        )
        
        # Check for deadline and schedule pre-deadline job
        self.scheduler.add_job(
            self.check_and_schedule_deadline,
            CronTrigger(hour="*/6"),  # Every 6 hours
            id="deadline_check",
            name="Deadline Check",
            replace_existing=True
        )
        
        logger.info("Scheduler jobs set up")
    
    def start(self) -> None:
        """Start the scheduler."""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("FPL Agent Scheduler started")
    
    def stop(self) -> None:
        """Stop the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("FPL Agent Scheduler stopped")
    
    def check_and_schedule_deadline(self) -> None:
        """Check for upcoming deadline and schedule pre-deadline job."""
        try:
            deadline = self.fpl_client.get_deadline()
            if not deadline:
                logger.info("No upcoming deadline found")
                return
            
            now = datetime.utcnow()
            time_until_deadline = deadline - now
            
            # Schedule job 1 hour before deadline
            pre_deadline_time = deadline - timedelta(hours=1)
            
            if pre_deadline_time > now:
                # Remove existing job if any
                try:
                    self.scheduler.remove_job("pre_deadline")
                except:
                    pass
                
                self.scheduler.add_job(
                    self.run_pre_deadline,
                    DateTrigger(run_date=pre_deadline_time),
                    id="pre_deadline",
                    name="Pre-Deadline Execution",
                    replace_existing=True
                )
                
                logger.info(f"Scheduled pre-deadline job for {pre_deadline_time}")
            
        except Exception as e:
            logger.error(f"Failed to check deadline: {e}")
    
    def run_daily_update(self) -> None:
        """Run daily update job."""
        logger.info("Running daily update...")
        
        try:
            # Login if needed
            if not self.fpl_auth.is_authenticated:
                self.fpl_auth.login()
            
            # Get all players and run predictions
            players = self.fpl_client.get_players()
            
            predictions = []
            for player in players:
                if player.minutes < 90:
                    continue
                
                try:
                    features = self.feature_eng.extract_features(
                        player.id,
                        include_history=False
                    )
                    pred = self.predictor.predict_player(features)
                    
                    predictions.append({
                        "player_id": player.id,
                        "player_name": player.web_name,
                        "team": player.team,
                        "position": player.position,
                        "predicted_points": pred,
                    })
                except Exception as e:
                    continue
            
            # Log predictions to database
            next_gw = self.fpl_client.get_next_gameweek()
            if next_gw:
                self.db.log_predictions(next_gw.id, predictions)
            
            logger.info(f"Daily update complete. {len(predictions)} predictions logged.")
            
        except Exception as e:
            logger.error(f"Daily update failed: {e}")
    
    def run_pre_deadline(self) -> None:
        """Run pre-deadline job - make decisions and optionally execute."""
        logger.info("Running pre-deadline job...")
        
        try:
            # Login if needed
            if not self.fpl_auth.is_authenticated:
                self.fpl_auth.login()
            
            next_gw = self.fpl_client.get_next_gameweek()
            if not next_gw:
                logger.warning("No next gameweek found")
                return
            
            # Create gameweek log
            self.db.create_gameweek_log(next_gw.id)
            self.db.update_gameweek_log(next_gw.id, status="processing")
            
            # Get current team
            my_team = self.fpl_client.get_my_team()
            team_ids = [p.element for p in my_team.picks]
            
            # Get predictions for team
            team_predictions = []
            player_ownership = {}
            player_positions = {}
            
            for player_id in team_ids:
                player = self.fpl_client.get_player(player_id)
                if player:
                    features = self.feature_eng.extract_features(player_id, include_history=False)
                    pred = self.predictor.predict_player(features)
                    team_predictions.append((player_id, player.web_name, pred))
                    player_ownership[player_id] = float(player.selected_by_percent)
                    player_positions[player_id] = player.element_type
            
            # 1. Captain Selection
            captain_pick = self.captain_picker.pick(
                team_predictions,
                player_ownership,
                prefer_differential=self.db.get_setting("differential_mode") == "true"
            )
            
            self.db.log_decision(
                gameweek=next_gw.id,
                decision_type="captain",
                details={
                    "captain_id": captain_pick.captain_id,
                    "captain_name": captain_pick.captain_name,
                    "vice_captain_id": captain_pick.vice_captain_id,
                    "vice_captain_name": captain_pick.vice_captain_name,
                },
                reasoning=captain_pick.reasoning
            )
            
            logger.info(f"Captain decision: {captain_pick.captain_name}")
            
            # 2. Lineup Optimization
            squad_predictions = [
                (p.element, self.fpl_client.get_player(p.element).web_name if self.fpl_client.get_player(p.element) else "Unknown", 
                 next((pred for pid, _, pred in team_predictions if pid == p.element), 0))
                for p in my_team.picks
            ]
            
            lineup = self.lineup_optimizer.optimize(
                squad_predictions,
                player_positions
            )
            
            self.db.log_decision(
                gameweek=next_gw.id,
                decision_type="lineup",
                details={
                    "formation": lineup.formation,
                    "starting": [p.player_id for p in lineup.starting_xi],
                    "bench": [p.player_id for p in lineup.bench],
                },
                reasoning=lineup.reasoning
            )
            
            logger.info(f"Lineup decision: {lineup.formation}")
            
            # Update predicted points
            total_predicted = lineup.total_predicted_points
            if captain_pick.captain_id in [p.player_id for p in lineup.starting_xi]:
                total_predicted += captain_pick.captain_predicted  # Captain doubles
            
            self.db.update_gameweek_log(
                next_gw.id,
                captain_set=captain_pick.captain_name,
                predicted_points=total_predicted
            )
            
            # 3. Execute if auto_execute is enabled
            if self.auto_execute or self.db.get_setting("auto_execute") == "true":
                self._execute_decisions(
                    next_gw.id,
                    lineup,
                    captain_pick
                )
            
            self.db.update_gameweek_log(next_gw.id, status="completed")
            logger.info("Pre-deadline job complete")
            
        except Exception as e:
            logger.error(f"Pre-deadline job failed: {e}")
            if next_gw:
                self.db.update_gameweek_log(next_gw.id, status="failed")
    
    def _execute_decisions(
        self,
        gameweek: int,
        lineup,
        captain_pick
    ) -> None:
        """Execute the decisions on FPL."""
        try:
            # Set lineup
            starting_ids = [p.player_id for p in lineup.starting_xi]
            bench_ids = [p.player_id for p in lineup.bench]
            
            result = self.fpl_client.set_lineup(
                starting_ids=starting_ids,
                bench_ids=bench_ids,
                captain_id=captain_pick.captain_id,
                vice_captain_id=captain_pick.vice_captain_id
            )
            
            logger.info(f"Lineup set successfully")
            
            self.db.update_gameweek_log(
                gameweek,
                status="executed"
            )
            
        except Exception as e:
            logger.error(f"Failed to execute decisions: {e}")
    
    def run_post_gameweek(self, gameweek: int) -> None:
        """Run post-gameweek analysis."""
        logger.info(f"Running post-gameweek analysis for GW{gameweek}...")
        
        try:
            # Get actual points for players
            players = self.fpl_client.get_players()
            actual_points = {}
            
            for player in players:
                details = self.fpl_client.get_player_details(player.id)
                history = details.get("history", [])
                
                for gw in history:
                    if gw.get("round") == gameweek:
                        actual_points[player.id] = gw.get("total_points", 0)
                        break
            
            # Update predictions with actual points
            self.db.update_actual_points(gameweek, actual_points)
            
            # Get team performance
            team_info = self.fpl_client.get_my_team_info()
            
            # Log performance
            self.db.log_performance(
                gameweek=gameweek,
                total_points=team_info.get("summary_event_points", 0),
                overall_rank=team_info.get("summary_overall_rank", 0),
                gameweek_rank=team_info.get("summary_event_rank", 0),
            )
            
            logger.info(f"Post-gameweek analysis complete for GW{gameweek}")
            
        except Exception as e:
            logger.error(f"Post-gameweek analysis failed: {e}")


def main():
    """Run the scheduler."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    scheduler = FPLAgentScheduler(auto_execute=False)
    scheduler.start()
    
    print("FPL Agent Scheduler running. Press Ctrl+C to stop.")
    
    try:
        import time
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.stop()
        print("Scheduler stopped.")


if __name__ == "__main__":
    main()


