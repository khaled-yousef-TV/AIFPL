"""
LSTM Model for Player Points Prediction

Real LSTM neural network for predicting player points using temporal sequences.
Trains on historical gameweek data with sequences of player performance.
"""

import os
import logging
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import numpy as np

logger = logging.getLogger(__name__)

# Try to import TensorFlow/Keras
TF_AVAILABLE = False
try:
    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import LSTM, Dense, Dropout, Masking
    from tensorflow.keras.optimizers import Adam
    from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
    from sklearn.preprocessing import StandardScaler
    TF_AVAILABLE = True
except ImportError:
    # TensorFlow not available - class will raise error on initialization
    pass

SEQUENCE_LENGTH = 5  # Use last 5 gameweeks to predict next
FEATURE_COUNT = 15  # Number of features per gameweek


class LSTMPredictor:
    """
    LSTM model for predicting player points from temporal sequences.
    
    Architecture:
    - Input: Sequences of (sequence_length, feature_count) 
    - LSTM Layer 1: 128 units, return_sequences=True
    - LSTM Layer 2: 64 units, return_sequences=False
    - Dropout: 0.2
    - Dense: 1 unit (predicted points)
    """
    
    DEFAULT_PARAMS = {
        "lstm_units_1": 128,
        "lstm_units_2": 64,
        "dropout_rate": 0.2,
        "learning_rate": 0.001,
        "batch_size": 32,
        "epochs": 100,
        "validation_split": 0.2,
    }
    
    def __init__(
        self,
        model_path: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None
    ):
        """
        Initialize LSTM predictor.
        
        Args:
            model_path: Path to saved model file
            params: Model hyperparameters
        """
        if not TF_AVAILABLE:
            raise ImportError("TensorFlow required. Install with: pip install tensorflow")
        
        self.params = params or self.DEFAULT_PARAMS.copy()
        self.model = None  # Will be keras.Model if TensorFlow available
        self.scaler: Optional[StandardScaler] = None
        self.model_path = model_path
        self.feature_names = self._get_feature_names()
        
        # Load existing model if path provided
        if model_path and Path(model_path).exists():
            self.load(model_path)
    
    def _get_feature_names(self) -> List[str]:
        """Get list of feature names for LSTM input."""
        return [
            "points", "minutes", "goals", "assists", "clean_sheets",
            "bonus", "influence", "creativity", "threat", "ict_index",
            "xG", "xA", "fixture_difficulty", "is_home", "price"
        ]
    
    def _build_model(self, input_shape: Tuple[int, int]):
        """
        Build LSTM model architecture.
        
        Args:
            input_shape: (sequence_length, feature_count)
            
        Returns:
            Compiled Keras model
        """
        if not TF_AVAILABLE:
            raise ImportError("TensorFlow required. Install with: pip install tensorflow")
        
        model = Sequential([
            # Masking layer for missing timesteps
            Masking(mask_value=0.0, input_shape=input_shape),
            
            # First LSTM layer
            LSTM(
                self.params["lstm_units_1"],
                return_sequences=True,
                dropout=0.2,
                recurrent_dropout=0.2
            ),
            
            # Second LSTM layer
            LSTM(
                self.params["lstm_units_2"],
                return_sequences=False,
                dropout=0.2,
                recurrent_dropout=0.2
            ),
            
            # Dropout
            Dropout(self.params["dropout_rate"]),
            
            # Output layer
            Dense(1, activation='relu')  # Points are non-negative
        ])
        
        # Compile model
        model.compile(
            optimizer=Adam(learning_rate=self.params["learning_rate"]),
            loss='huber',  # Robust to outliers
            metrics=['mae', 'mse']
        )
        
        return model
    
    def prepare_training_data(
        self,
        player_histories: List[List[Dict[str, Any]]],
        sequence_length: int = SEQUENCE_LENGTH
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Prepare training data from player histories.
        
        Creates sequences of (sequence_length) gameweeks to predict the next gameweek.
        
        Args:
            player_histories: List of player histories, each containing gameweek data
            sequence_length: Number of gameweeks to use as input
            
        Returns:
            Tuple of (X, y) where:
            - X: shape (n_samples, sequence_length, n_features)
            - y: shape (n_samples,) - actual points for next gameweek
        """
        sequences = []
        targets = []
        
        for history in player_histories:
            if len(history) < sequence_length + 1:
                continue  # Need at least sequence_length + 1 for input and target
            
            # Sort by gameweek
            sorted_history = sorted(history, key=lambda x: x.get("gameweek", 0))
            
            # Create sliding windows
            for i in range(len(sorted_history) - sequence_length):
                sequence_data = sorted_history[i:i + sequence_length]
                target_data = sorted_history[i + sequence_length]
                
                # Extract features for sequence
                sequence_features = []
                for gw_data in sequence_data:
                    features = self._extract_features_from_history(gw_data)
                    sequence_features.append(features)
                
                sequences.append(sequence_features)
                targets.append(target_data.get("total_points", 0))
        
        if not sequences:
            logger.warning("No valid sequences found for training")
            return np.array([]), np.array([])
        
        X = np.array(sequences)
        y = np.array(targets)
        
        # Normalize features
        if self.scaler is None:
            self.scaler = StandardScaler()
            # Reshape for scaler: (n_samples * sequence_length, n_features)
            X_reshaped = X.reshape(-1, X.shape[-1])
            self.scaler.fit(X_reshaped)
        
        # Apply normalization
        X_reshaped = X.reshape(-1, X.shape[-1])
        X_normalized = self.scaler.transform(X_reshaped)
        X = X_normalized.reshape(X.shape)
        
        logger.info(f"Prepared {len(X)} sequences for training")
        return X, y
    
    def _extract_features_from_history(self, gw_data: Dict[str, Any]) -> List[float]:
        """
        Extract features from a single gameweek history entry.
        
        Args:
            gw_data: Dictionary with gameweek data
            
        Returns:
            List of feature values
        """
        return [
            float(gw_data.get("total_points", 0)),
            float(gw_data.get("minutes", 0)),
            int(gw_data.get("goals_scored", 0)),
            int(gw_data.get("assists", 0)),
            int(gw_data.get("clean_sheets", 0)),
            int(gw_data.get("bonus", 0)),
            float(gw_data.get("influence", 0)),
            float(gw_data.get("creativity", 0)),
            float(gw_data.get("threat", 0)),
            float(gw_data.get("ict_index", 0)),
            float(gw_data.get("expected_goals", 0)),
            float(gw_data.get("expected_assists", 0)),
            float(gw_data.get("difficulty", 3)),  # Default to medium
            float(gw_data.get("was_home", 0)),  # 1 if home, 0 if away
            float(gw_data.get("value", 0)) / 10.0,  # Convert to millions
        ]
    
    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        validation_split: float = 0.2,
        epochs: int = 100,
        batch_size: int = 32,
        model_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Train the LSTM model.
        
        Args:
            X: Training sequences (n_samples, sequence_length, n_features)
            y: Target points (n_samples,)
            validation_split: Fraction of data for validation
            epochs: Number of training epochs
            batch_size: Batch size
            model_dir: Directory to save checkpoints
            
        Returns:
            Dictionary with training history and metrics
        """
        if X.size == 0 or y.size == 0:
            raise ValueError("No training data provided")
        
        if self.model is None:
            input_shape = (X.shape[1], X.shape[2])
            self.model = self._build_model(input_shape)
        
        # Callbacks
        callbacks = [
            EarlyStopping(
                monitor='val_loss',
                patience=10,
                restore_best_weights=True,
                verbose=1
            )
        ]
        
        if model_dir:
            Path(model_dir).mkdir(parents=True, exist_ok=True)
            checkpoint_path = os.path.join(model_dir, "lstm_model_best.keras")
            callbacks.append(
                ModelCheckpoint(
                    checkpoint_path,
                    monitor='val_loss',
                    save_best_only=True,
                    verbose=1
                )
            )
        
        # Train model
        history = self.model.fit(
            X, y,
            validation_split=validation_split,
            epochs=epochs,
            batch_size=batch_size,
            callbacks=callbacks,
            verbose=1
        )
        
        # Get final metrics
        train_loss = history.history['loss'][-1]
        val_loss = history.history['val_loss'][-1]
        train_mae = history.history['mae'][-1]
        val_mae = history.history['val_mae'][-1]
        
        metrics = {
            "train_loss": float(train_loss),
            "val_loss": float(val_loss),
            "train_mae": float(train_mae),
            "val_mae": float(val_mae),
            "epochs_trained": len(history.history['loss']),
            "n_samples": len(X),
        }
        
        logger.info(f"Training complete. Val MAE: {val_mae:.3f}, Val Loss: {val_loss:.3f}")
        
        return metrics
    
    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Predict points for sequences.
        
        Args:
            X: Input sequences (n_samples, sequence_length, n_features)
            
        Returns:
            Predicted points (n_samples,)
        """
        if self.model is None:
            raise ValueError("Model not trained. Call train() first.")
        
        # Normalize if scaler is available
        if self.scaler is not None:
            X_reshaped = X.reshape(-1, X.shape[-1])
            X_normalized = self.scaler.transform(X_reshaped)
            X = X_normalized.reshape(X.shape)
        
        predictions = self.model.predict(X, verbose=0)
        return predictions.flatten()
    
    def predict_from_history(
        self,
        player_history: List[Dict[str, Any]],
        sequence_length: int = SEQUENCE_LENGTH
    ) -> float:
        """
        Predict next gameweek points from player history.
        
        Args:
            player_history: List of gameweek history entries
            sequence_length: Number of gameweeks to use
            
        Returns:
            Predicted points for next gameweek
        """
        if len(player_history) < sequence_length:
            # Not enough history, return form-based estimate
            if player_history:
                recent_points = [h.get("total_points", 0) for h in player_history[-3:]]
                return float(np.mean(recent_points)) if recent_points else 2.0
            return 2.0
        
        # Get last sequence_length gameweeks
        sorted_history = sorted(player_history, key=lambda x: x.get("gameweek", 0))
        sequence_data = sorted_history[-sequence_length:]
        
        # Extract features
        sequence_features = []
        for gw_data in sequence_data:
            features = self._extract_features_from_history(gw_data)
            sequence_features.append(features)
        
        X = np.array([sequence_features])  # Add batch dimension
        
        # Predict
        prediction = self.predict(X)[0]
        return max(0.0, float(prediction))  # Ensure non-negative
    
    def save(self, model_path: str) -> None:
        """Save model and scaler to disk."""
        if self.model is None:
            raise ValueError("No model to save.")
        
        Path(model_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Save model
        self.model.save(model_path)
        
        # Save scaler
        scaler_path = model_path.replace('.keras', '_scaler.pkl')
        import pickle
        with open(scaler_path, 'wb') as f:
            pickle.dump(self.scaler, f)
        
        logger.info(f"Model saved to {model_path}")
    
    def load(self, model_path: str) -> None:
        """Load model and scaler from disk."""
        if not Path(model_path).exists():
            raise FileNotFoundError(f"Model not found: {model_path}")
        
        # Load model
        self.model = keras.models.load_model(model_path)
        
        # Load scaler
        scaler_path = model_path.replace('.keras', '_scaler.pkl')
        if Path(scaler_path).exists():
            import pickle
            with open(scaler_path, 'rb') as f:
                self.scaler = pickle.load(f)
        else:
            logger.warning(f"Scaler not found: {scaler_path}")
            self.scaler = None
        
        logger.info(f"Model loaded from {model_path}")

