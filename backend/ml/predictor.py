"""
Points Predictor Model

XGBoost-based model for predicting player points.
"""

import os
import logging
import pickle
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import numpy as np

logger = logging.getLogger(__name__)

# Try to import ML libraries
try:
    import xgboost as xgb
    from sklearn.model_selection import train_test_split, cross_val_score
    from sklearn.metrics import mean_absolute_error, mean_squared_error
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False
    logger.warning("ML libraries not installed. Install with: pip install xgboost scikit-learn")

from .features import PlayerFeatures, FeatureEngineer


class PointsPredictor:
    """XGBoost model for predicting player points."""
    
    DEFAULT_PARAMS = {
        "objective": "reg:squarederror",
        "max_depth": 6,
        "learning_rate": 0.1,
        "n_estimators": 100,
        "min_child_weight": 3,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "random_state": 42,
    }
    
    def __init__(
        self,
        model_path: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None
    ):
        """
        Initialize the predictor.
        
        Args:
            model_path: Path to saved model file
            params: XGBoost parameters
        """
        if not ML_AVAILABLE:
            raise ImportError("ML libraries required. Install with: pip install xgboost scikit-learn")
        
        self.params = params or self.DEFAULT_PARAMS.copy()
        self.model: Optional[xgb.XGBRegressor] = None
        self.model_path = model_path
        self.feature_names = FeatureEngineer.FEATURE_NAMES
        
        # Load existing model if path provided
        if model_path and Path(model_path).exists():
            self.load(model_path)
    
    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        eval_split: float = 0.2,
        early_stopping_rounds: int = 10
    ) -> Dict[str, float]:
        """
        Train the model.
        
        Args:
            X: Feature matrix (n_samples, n_features)
            y: Target values (actual points)
            eval_split: Fraction for evaluation set
            early_stopping_rounds: Early stopping patience
            
        Returns:
            Dictionary with training metrics
        """
        # Split data
        X_train, X_eval, y_train, y_eval = train_test_split(
            X, y, test_size=eval_split, random_state=42
        )
        
        # Create model
        self.model = xgb.XGBRegressor(**self.params)
        
        # Train with early stopping
        self.model.fit(
            X_train, y_train,
            eval_set=[(X_eval, y_eval)],
            verbose=False
        )
        
        # Evaluate
        train_pred = self.model.predict(X_train)
        eval_pred = self.model.predict(X_eval)
        
        metrics = {
            "train_mae": mean_absolute_error(y_train, train_pred),
            "train_rmse": np.sqrt(mean_squared_error(y_train, train_pred)),
            "eval_mae": mean_absolute_error(y_eval, eval_pred),
            "eval_rmse": np.sqrt(mean_squared_error(y_eval, eval_pred)),
            "n_samples": len(X),
            "n_features": X.shape[1],
        }
        
        logger.info(f"Training complete. Eval MAE: {metrics['eval_mae']:.3f}")
        
        return metrics
    
    def cross_validate(
        self,
        X: np.ndarray,
        y: np.ndarray,
        cv: int = 5
    ) -> Dict[str, float]:
        """
        Perform cross-validation.
        
        Args:
            X: Feature matrix
            y: Target values
            cv: Number of folds
            
        Returns:
            Cross-validation metrics
        """
        model = xgb.XGBRegressor(**self.params)
        
        # Use negative MAE (sklearn convention)
        scores = cross_val_score(
            model, X, y,
            cv=cv,
            scoring="neg_mean_absolute_error"
        )
        
        return {
            "cv_mae_mean": -scores.mean(),
            "cv_mae_std": scores.std(),
            "cv_scores": (-scores).tolist(),
        }
    
    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Predict points for given features.
        
        Args:
            X: Feature matrix
            
        Returns:
            Predicted points
        """
        if self.model is None:
            raise ValueError("Model not trained. Call train() first.")
        
        return self.model.predict(X)
    
    def predict_player(self, features: PlayerFeatures) -> float:
        """
        Predict points for a single player.
        
        Args:
            features: PlayerFeatures object
            
        Returns:
            Predicted points
        """
        X = np.array([features.feature_vector])
        return float(self.predict(X)[0])
    
    def predict_players(
        self,
        features_list: List[PlayerFeatures]
    ) -> List[Tuple[int, str, float]]:
        """
        Predict points for multiple players.
        
        Args:
            features_list: List of PlayerFeatures
            
        Returns:
            List of (player_id, player_name, predicted_points)
        """
        if not features_list:
            return []
        
        X = np.array([f.feature_vector for f in features_list])
        predictions = self.predict(X)
        
        results = [
            (f.player_id, f.player_name, float(pred))
            for f, pred in zip(features_list, predictions)
        ]
        
        # Sort by predicted points descending
        results.sort(key=lambda x: x[2], reverse=True)
        
        return results
    
    def get_feature_importance(self) -> Dict[str, float]:
        """
        Get feature importances.
        
        Returns:
            Dictionary of feature_name -> importance
        """
        if self.model is None:
            raise ValueError("Model not trained.")
        
        importances = self.model.feature_importances_
        
        return {
            name: float(imp)
            for name, imp in zip(self.feature_names, importances)
        }
    
    def save(self, path: str) -> None:
        """Save model to file."""
        if self.model is None:
            raise ValueError("No model to save.")
        
        with open(path, "wb") as f:
            pickle.dump({
                "model": self.model,
                "params": self.params,
                "feature_names": self.feature_names,
            }, f)
        
        logger.info(f"Model saved to {path}")
    
    def load(self, path: str) -> None:
        """Load model from file."""
        with open(path, "rb") as f:
            data = pickle.load(f)
        
        self.model = data["model"]
        self.params = data.get("params", self.DEFAULT_PARAMS)
        self.feature_names = data.get("feature_names", FeatureEngineer.FEATURE_NAMES)
        
        logger.info(f"Model loaded from {path}")


class HeuristicPredictor:
    """
    Fallback heuristic predictor when ML model is unavailable.
    
    Uses a weighted combination of form, fixtures, and stats.
    """
    
    # Position-specific weights
    POSITION_WEIGHTS = {
        1: {"clean_sheet": 4, "save": 0.33, "goal": 6},  # GK
        2: {"clean_sheet": 4, "goal": 6, "assist": 3},   # DEF
        3: {"goal": 5, "assist": 3, "clean_sheet": 1},   # MID
        4: {"goal": 4, "assist": 3},                      # FWD
    }
    
    def __init__(self):
        """Initialize heuristic predictor."""
        pass
    
    def predict_player(self, features: PlayerFeatures) -> float:
        """
        Predict points using heuristics.
        
        Args:
            features: PlayerFeatures object
            
        Returns:
            Predicted points
        """
        # Base prediction from form and PPG
        base = (features.form * 0.4 + features.points_per_game * 0.6)
        
        # Adjust for fixture difficulty (1=easy, 5=hard)
        fixture_multiplier = 1.2 - (features.next_fixture_difficulty - 1) * 0.1
        
        # Adjust for availability
        availability_mult = features.availability
        
        # Adjust for home/away
        home_bonus = 0.2 if features.is_home else 0
        
        # Position-specific adjustments
        pos_weight = self.POSITION_WEIGHTS.get(features.position, {})
        
        # xG/xA contribution
        xg_contribution = features.xG * pos_weight.get("goal", 4) / 38  # Per game
        xa_contribution = features.xA * pos_weight.get("assist", 3) / 38
        
        # Clean sheet potential (for GK/DEF)
        cs_contribution = 0
        if features.position in [1, 2]:
            # Lower opponent strength = higher CS chance
            cs_chance = max(0, (6 - features.next_fixture_difficulty) / 5)
            cs_contribution = cs_chance * pos_weight.get("clean_sheet", 0)
        
        # Combine
        predicted = (
            base * fixture_multiplier * availability_mult
            + home_bonus
            + xg_contribution
            + xa_contribution
            + cs_contribution
        )
        
        # Minutes adjustment
        if features.avg_minutes_3 < 60:
            predicted *= features.avg_minutes_3 / 90
        
        return max(0, predicted)
    
    def predict_players(
        self,
        features_list: List[PlayerFeatures]
    ) -> List[Tuple[int, str, float]]:
        """Predict for multiple players."""
        results = [
            (f.player_id, f.player_name, self.predict_player(f))
            for f in features_list
        ]
        results.sort(key=lambda x: x[2], reverse=True)
        return results


def get_predictor(model_path: Optional[str] = None) -> Any:
    """
    Get the best available predictor.
    
    Returns XGBoost predictor if available, otherwise heuristic.
    """
    if ML_AVAILABLE:
        try:
            return PointsPredictor(model_path=model_path)
        except Exception as e:
            logger.warning(f"Could not load ML predictor: {e}")
    
    logger.info("Using heuristic predictor")
    return HeuristicPredictor()

