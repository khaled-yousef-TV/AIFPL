# LSTM Model Training Guide

## Overview

The Wildcard Trajectory Optimizer uses a **hybrid LSTM-XGBoost model** (0.7×LSTM + 0.3×XGBoost) for predictions. The LSTM component is a real neural network trained on historical player data.

## Current Status

**The LSTM model has two modes:**

1. **Trained Model** (if available): Uses a real LSTM neural network trained on historical data
2. **Proxy Mode** (fallback): Uses form momentum and weighted averages when model isn't trained

## Training the LSTM Model

### Prerequisites

```bash
pip install tensorflow scikit-learn
```

### Training Command

```bash
# From project root
cd /Users/khaledyousef/AIFPL
source .venv/bin/activate

# Train the model
PYTHONPATH=backend python backend/ml/chips/train_lstm.py
```

### Training Options

```bash
python backend/ml/chips/train_lstm.py \
    --model-dir backend/ml/models \
    --epochs 100 \
    --batch-size 32 \
    --validation-split 0.2
```

**Parameters:**
- `--model-dir`: Directory to save model (default: `backend/ml/models`)
- `--epochs`: Number of training epochs (default: 100)
- `--batch-size`: Batch size (default: 32)
- `--validation-split`: Validation split ratio (default: 0.2)

### Training Process

1. **Data Collection**: Fetches historical player data from FPL API
   - Minimum 6 gameweeks per player (5 for sequence + 1 for target)
   - Minimum 90 total minutes for eligibility

2. **Sequence Generation**: Creates sliding windows of 5 gameweeks
   - Input: Last 5 gameweeks of player performance
   - Target: Points scored in next gameweek

3. **Model Architecture**:
   - LSTM Layer 1: 128 units (return_sequences=True)
   - LSTM Layer 2: 64 units (return_sequences=False)
   - Dropout: 0.2
   - Dense Output: 1 unit (predicted points)

4. **Training**:
   - Loss: Huber (robust to outliers)
   - Optimizer: Adam (learning_rate=0.001)
   - Early stopping: Patience=10 epochs
   - Validation split: 20%

5. **Model Saving**:
   - Model: `lstm_wildcard_model.keras`
   - Scaler: `lstm_wildcard_model_scaler.pkl`

## Model Usage

Once trained, the model is automatically loaded by `WildcardOptimizer`:

- **Model path**: `backend/ml/models/lstm_wildcard_model.keras`
- **Automatic fallback**: If model not found, uses proxy implementation
- **No breaking changes**: System works with or without trained model

## When to Retrain

**Recommended retraining schedule:**
- **Initial training**: Before first use of wildcard optimizer
- **Mid-season update**: After ~10 gameweeks of new data
- **End of season**: After season completes for next year's model
- **After major data shifts**: Significant changes in FPL scoring system

## Model Performance

**Expected metrics (after training):**
- Validation MAE: ~2.5-3.5 points per gameweek
- Validation Loss: ~8-12 (Huber loss)
- Training typically converges in 30-50 epochs

## Troubleshooting

### Model not found
```
INFO: LSTM model not found at backend/ml/models/lstm_wildcard_model.keras. Using proxy.
```
**Solution**: Train the model using the command above.

### TensorFlow not available
```
WARNING: TensorFlow not available. Install with: pip install tensorflow
```
**Solution**: `pip install tensorflow`

### Insufficient training data
```
ValueError: Insufficient training data: 50 players. Need at least 100.
```
**Solution**: Wait for more gameweeks of data, or reduce minimum requirements in `train_lstm.py`

### Training takes too long
- Reduce `--epochs` (e.g., 50 instead of 100)
- Increase `--batch-size` (e.g., 64 instead of 32)
- Use fewer players (adjust `min_minutes` in `collect_training_data`)

## Architecture Notes

The LSTM model is **completely independent** from the existing XGBoost/heuristic predictors:

- **Separate feature extraction**: Uses gameweek-by-gameweek features (not aggregated)
- **Temporal sequences**: Processes time series data (not single vectors)
- **Optional dependency**: System works without LSTM (uses proxy)
- **Clean integration**: HybridPredictor automatically uses trained model if available

