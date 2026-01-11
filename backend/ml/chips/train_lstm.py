"""
Training Script for LSTM Model

Collects historical player data and trains the LSTM model.
Can be run as a script or imported for scheduled training.
"""

import os
import logging
from pathlib import Path
from typing import List, Dict, Any
import sys

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fpl.client import FPLClient
from ml.chips.lstm_model import LSTMPredictor, SEQUENCE_LENGTH

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)


def collect_training_data(
    fpl_client: FPLClient,
    min_gameweeks: int = SEQUENCE_LENGTH + 1,
    min_minutes: int = 90
) -> List[List[Dict[str, Any]]]:
    """
    Collect training data from FPL API.
    
    Args:
        fpl_client: FPL client instance
        min_gameweeks: Minimum number of gameweeks required per player
        min_minutes: Minimum total minutes for player eligibility
        
    Returns:
        List of player histories, each containing gameweek data
    """
    logger.info("Collecting training data from FPL API...")
    
    players = fpl_client.get_players()
    player_histories = []
    
    for i, player in enumerate(players):
        if player.minutes < min_minutes:
            continue
        
        try:
            # Get player details with history
            player_details = fpl_client.get_player_details(player.id)
            history = player_details.get("history", [])
            
            if len(history) < min_gameweeks:
                continue
            
            # Filter out future gameweeks and invalid entries
            valid_history = []
            for entry in history:
                if entry.get("round", 0) > 0 and entry.get("minutes", 0) > 0:
                    valid_history.append(entry)
            
            if len(valid_history) >= min_gameweeks:
                player_histories.append(valid_history)
        
        except Exception as e:
            logger.debug(f"Error getting history for player {player.id}: {e}")
            continue
        
        if (i + 1) % 100 == 0:
            logger.info(f"Processed {i + 1}/{len(players)} players...")
    
    logger.info(f"Collected histories for {len(player_histories)} players")
    return player_histories


def train_lstm_model(
    model_dir: str = "backend/ml/models",
    epochs: int = 100,
    batch_size: int = 32,
    validation_split: float = 0.2
) -> Dict[str, Any]:
    """
    Train LSTM model on historical data.
    
    Args:
        model_dir: Directory to save model
        epochs: Number of training epochs
        batch_size: Batch size
        validation_split: Validation split ratio
        
    Returns:
        Dictionary with training metrics
    """
    # Initialize FPL client
    fpl_client = FPLClient(auth=None)
    
    # Collect training data
    player_histories = collect_training_data(fpl_client)
    
    if len(player_histories) < 100:
        raise ValueError(f"Insufficient training data: {len(player_histories)} players. Need at least 100.")
    
    # Initialize LSTM predictor
    lstm = LSTMPredictor()
    
    # Prepare training data
    logger.info("Preparing training sequences...")
    X, y = lstm.prepare_training_data(player_histories)
    
    if len(X) == 0:
        raise ValueError("No valid training sequences generated")
    
    logger.info(f"Training on {len(X)} sequences")
    
    # Train model
    metrics = lstm.train(
        X, y,
        validation_split=validation_split,
        epochs=epochs,
        batch_size=batch_size,
        model_dir=model_dir
    )
    
    # Save model
    model_path = os.path.join(model_dir, "lstm_wildcard_model.keras")
    lstm.save(model_path)
    
    logger.info(f"Model saved to {model_path}")
    logger.info(f"Training metrics: {metrics}")
    
    return metrics


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Train LSTM model for wildcard predictions")
    parser.add_argument(
        "--model-dir",
        type=str,
        default="backend/ml/models",
        help="Directory to save model"
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=100,
        help="Number of training epochs"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Batch size"
    )
    parser.add_argument(
        "--validation-split",
        type=float,
        default=0.2,
        help="Validation split ratio"
    )
    
    args = parser.parse_args()
    
    try:
        metrics = train_lstm_model(
            model_dir=args.model_dir,
            epochs=args.epochs,
            batch_size=args.batch_size,
            validation_split=args.validation_split
        )
        print(f"\nTraining completed successfully!")
        print(f"Validation MAE: {metrics['val_mae']:.3f}")
        print(f"Validation Loss: {metrics['val_loss']:.3f}")
    except Exception as e:
        logger.error(f"Training failed: {e}", exc_info=True)
        sys.exit(1)

