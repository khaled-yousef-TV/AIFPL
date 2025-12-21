# LSTM Architecture - Complete Separation Strategy

## Design Principles

1. **Zero Dependencies**: LSTM module should NOT import from existing `ml/predictor.py` or `ml/features.py`
2. **Standalone Module**: LSTM lives in `backend/ml/lstm/` as completely independent system
3. **Minimal Shared Code**: Only share FPL client (data source), nothing else
4. **Clean API Integration**: LSTM is just another method option, no tight coupling
5. **Independent Feature Extraction**: LSTM has its own feature extraction optimized for sequences

---

## Directory Structure

```
backend/ml/
├── __init__.py
├── features.py          # ✅ EXISTING - XGBoost/heuristic features (DO NOT USE)
├── predictor.py         # ✅ EXISTING - XGBoost/heuristic predictors (DO NOT USE)
└── lstm/               # 🆕 NEW - Completely independent LSTM system
    ├── __init__.py
    ├── data_loader.py   # Sequence generation (uses FPL client directly)
    ├── features.py      # LSTM-specific feature extraction (separate from ml/features.py)
    ├── model.py         # PyTorch LSTM model
    ├── dataset.py       # PyTorch Dataset
    ├── trainer.py       # Training pipeline
    ├── predictor.py     # LSTM predictor (separate from ml/predictor.py)
    └── optimizer.py     # IP optimizer (optional, standalone)
```

---

## Dependency Rules

### ✅ ALLOWED Dependencies (LSTM → External)

```python
# backend/ml/lstm/*.py can import:
from backend.fpl.client import FPLClient          # ✅ Data source only
from backend.fpl.models import Player, Team       # ✅ Data models only
import torch                                      # ✅ PyTorch
import numpy as np                                # ✅ NumPy
from sklearn.preprocessing import StandardScaler # ✅ Scikit-learn (normalization)
import pulp                                       # ✅ PuLP (optional)
```

### ❌ FORBIDDEN Dependencies (LSTM → Existing ML)

```python
# backend/ml/lstm/*.py CANNOT import:
from backend.ml.features import FeatureEngineer  # ❌ NO - Use own feature extraction
from backend.ml.predictor import PointsPredictor # ❌ NO - Use own predictor
from backend.ml.predictor import HeuristicPredictor # ❌ NO
```

### ✅ ALLOWED Dependencies (API → LSTM)

```python
# backend/api/main.py can import:
from backend.ml.lstm.predictor import LSTMPredictor  # ✅ Clean interface only
```

---

## Module Responsibilities

### `backend/ml/lstm/features.py` (NEW)
**Purpose**: LSTM-specific feature extraction for sequences

**Responsibilities**:
- Extract features from player history (gameweek-by-gameweek)
- Create feature vectors optimized for temporal sequences
- Handle missing games (injuries, rotation)
- Normalize features for LSTM input

**Dependencies**:
- `backend.fpl.client` (FPLClient only)
- `numpy`
- `sklearn.preprocessing.StandardScaler`

**NO dependencies on**:
- `backend.ml.features` ❌
- `backend.ml.predictor` ❌

---

### `backend/ml/lstm/data_loader.py` (NEW)
**Purpose**: Generate 3D tensors from FPL data

**Responsibilities**:
- Create sliding windows (5 GW sequences)
- Generate 3D tensors: `[samples, timesteps=5, features]`
- Handle temporal train/val/test split
- Apply masking for missing games

**Dependencies**:
- `backend.ml.lstm.features` (own feature extraction)
- `backend.fpl.client` (data source)
- `numpy`
- `sklearn.preprocessing.StandardScaler`

**NO dependencies on**:
- `backend.ml.features` ❌
- `backend.ml.predictor` ❌
- `backend.ml.data_loader` (doesn't exist, but if it did) ❌

---

### `backend/ml/lstm/model.py` (NEW)
**Purpose**: PyTorch LSTM model architecture

**Responsibilities**:
- Define LSTM architecture
- Forward pass with masking
- Model save/load

**Dependencies**:
- `torch` (PyTorch)
- `numpy` (for data handling)

**NO dependencies on**:
- Any other backend modules ❌
- Pure PyTorch model definition

---

### `backend/ml/lstm/dataset.py` (NEW)
**Purpose**: PyTorch Dataset wrapper

**Responsibilities**:
- Wrap 3D tensors in PyTorch Dataset
- Handle batching
- Apply transforms

**Dependencies**:
- `torch`
- `numpy`

**NO dependencies on**:
- Any other backend modules ❌

---

### `backend/ml/lstm/trainer.py` (NEW)
**Purpose**: Training pipeline

**Responsibilities**:
- Training loop
- Validation
- Checkpointing
- Metrics logging

**Dependencies**:
- `backend.ml.lstm.data_loader` (own data loader)
- `backend.ml.lstm.model` (own model)
- `backend.ml.lstm.dataset` (own dataset)
- `torch`

**NO dependencies on**:
- `backend.ml.predictor` ❌
- `backend.ml.features` ❌

---

### `backend/ml/lstm/predictor.py` (NEW)
**Purpose**: LSTM predictor interface (similar to existing predictors)

**Responsibilities**:
- Load trained LSTM model
- Predict points for players
- Interface compatible with API expectations
- Fallback handling if model unavailable

**Dependencies**:
- `backend.ml.lstm.model` (own model)
- `backend.ml.lstm.features` (own features)
- `backend.fpl.client` (data source)
- `torch`

**NO dependencies on**:
- `backend.ml.predictor` ❌
- `backend.ml.features` ❌

**Interface**:
```python
class LSTMPredictor:
    def __init__(self, model_path: Optional[str] = None):
        """Initialize LSTM predictor."""
        pass
    
    def predict_player(self, player_id: int, gameweek: Optional[int] = None) -> float:
        """Predict points for a single player."""
        pass
    
    def predict_players(self, player_ids: List[int], gameweek: Optional[int] = None) -> List[Tuple[int, str, float]]:
        """Predict points for multiple players."""
        pass
```

---

### `backend/ml/lstm/optimizer.py` (NEW, Optional)
**Purpose**: Integer programming optimization

**Responsibilities**:
- Formulate optimization problem
- Solve with PuLP/HiGHS
- Return optimal squad

**Dependencies**:
- `pulp` (PuLP library)
- `numpy`

**NO dependencies on**:
- `backend.ml.lstm.predictor` (can work standalone)
- `backend.api.main._build_optimal_squad` ❌

---

## API Integration (Minimal Coupling)

### Current API Structure
```python
# backend/api/main.py
from ml.predictor import HeuristicPredictor, FormPredictor, FixturePredictor

predictor_heuristic = HeuristicPredictor()
predictor_form = FormPredictor()
predictor_fixture = FixturePredictor()

@app.get("/api/suggested-squad")
async def get_suggested_squad(method: str = "combined", budget: float = 100.0):
    if method == "heuristic":
        return await _build_squad_with_predictor(predictor_heuristic, ...)
    elif method == "form":
        return await _build_squad_with_predictor(predictor_form, ...)
    elif method == "fixture":
        return await _build_squad_with_predictor(predictor_fixture, ...)
    elif method == "lstm":  # 🆕 NEW
        return await _build_squad_with_predictor(predictor_lstm, ...)
```

### Clean Integration
```python
# backend/api/main.py

# Existing predictors (unchanged)
from ml.predictor import HeuristicPredictor, FormPredictor, FixturePredictor

# LSTM predictor (separate import, optional)
try:
    from ml.lstm.predictor import LSTMPredictor
    predictor_lstm = LSTMPredictor()  # May fail if model not trained
    LSTM_AVAILABLE = True
except (ImportError, FileNotFoundError, Exception) as e:
    logger.warning(f"LSTM predictor not available: {e}")
    predictor_lstm = None
    LSTM_AVAILABLE = False

@app.get("/api/suggested-squad")
async def get_suggested_squad(method: str = "combined", budget: float = 100.0):
    # ... existing methods ...
    
    elif method == "lstm":
        if not LSTM_AVAILABLE or predictor_lstm is None:
            raise HTTPException(
                status_code=503,
                detail="LSTM model not available. Model may not be trained yet."
            )
        return await _build_squad_with_predictor(predictor_lstm, "LSTM", budget)
```

**Key Points**:
- LSTM is just another method option
- Graceful fallback if unavailable
- No changes to existing predictor logic
- `_build_squad_with_predictor()` works with any predictor that has `predict_player()` method

---

## Feature Extraction Separation

### Existing: `backend/ml/features.py`
- **Purpose**: Features for XGBoost/heuristic (point-in-time)
- **Output**: Single feature vector per player
- **Features**: Aggregated stats (season totals, rolling averages)
- **Used by**: XGBoost, Heuristic, Form, Fixture predictors

### New: `backend/ml/lstm/features.py`
- **Purpose**: Features for LSTM (temporal sequences)
- **Output**: Sequence of feature vectors (one per gameweek)
- **Features**: Gameweek-by-gameweek stats (not aggregated)
- **Used by**: LSTM only

**Key Differences**:
- **Existing**: `extract_features(player_id, gameweek)` → single vector
- **New**: `extract_sequence(player_id, start_gw, end_gw)` → list of vectors

**Example**:
```python
# Existing (ml/features.py)
features = feature_engineer.extract_features(player_id=1, gameweek=20)
# Returns: PlayerFeatures object with aggregated stats

# New (ml/lstm/features.py)
sequence = lstm_feature_engineer.extract_sequence(player_id=1, start_gw=15, end_gw=20)
# Returns: List[Dict] with features for each gameweek [GW15, GW16, GW17, GW18, GW19, GW20]
```

---

## Data Flow Comparison

### Existing (XGBoost/Heuristic)
```
FPL API → FeatureEngineer → Single Feature Vector → XGBoost/Heuristic → Prediction
```

### New (LSTM)
```
FPL API → LSTMFeatureEngineer → Sequence of Vectors → DataLoader → 3D Tensor → LSTM → Prediction
```

**No overlap, completely separate pipelines.**

---

## Testing Strategy

### Unit Tests (Isolated)
- `test_lstm_features.py`: Test LSTM feature extraction independently
- `test_lstm_data_loader.py`: Test sequence generation independently
- `test_lstm_model.py`: Test model architecture independently
- `test_lstm_predictor.py`: Test predictor interface independently

### Integration Tests (Minimal)
- `test_lstm_api.py`: Test API endpoint with LSTM method
- Verify LSTM doesn't break existing methods

---

## Benefits of This Separation

1. **Zero Coupling**: LSTM can be developed/tested independently
2. **Easy Removal**: Can remove LSTM without affecting existing code
3. **Different Teams**: Different developers can work on LSTM vs. existing ML
4. **Different Tech Stacks**: LSTM uses PyTorch, existing uses XGBoost
5. **Independent Deployment**: Can deploy LSTM separately
6. **Clear Boundaries**: Easy to understand what depends on what

---

## Implementation Checklist

### Phase 1: Data Processing
- [ ] Create `backend/ml/lstm/` directory
- [ ] Create `backend/ml/lstm/__init__.py`
- [ ] Create `backend/ml/lstm/features.py` (own feature extraction)
- [ ] Create `backend/ml/lstm/data_loader.py` (uses own features)
- [ ] Verify NO imports from `backend.ml.features` or `backend.ml.predictor`

### Phase 2: Model
- [ ] Create `backend/ml/lstm/model.py` (pure PyTorch)
- [ ] Create `backend/ml/lstm/dataset.py` (pure PyTorch)
- [ ] Verify NO imports from other backend modules

### Phase 3: Training
- [ ] Create `backend/ml/lstm/trainer.py`
- [ ] Uses only lstm/* modules
- [ ] Verify standalone training script works

### Phase 4: Integration
- [ ] Create `backend/ml/lstm/predictor.py` (clean interface)
- [ ] Update `backend/api/main.py` (minimal changes, just add method option)
- [ ] Verify graceful fallback if LSTM unavailable

---

## Summary

**LSTM is a completely independent module:**
- ✅ Own feature extraction (`lstm/features.py`)
- ✅ Own data processing (`lstm/data_loader.py`)
- ✅ Own model (`lstm/model.py`)
- ✅ Own predictor (`lstm/predictor.py`)
- ✅ Only shares FPL client (data source)
- ✅ API integration is just another method option
- ❌ NO dependencies on existing ML code
- ❌ NO shared feature extraction
- ❌ NO shared predictor logic

**This ensures:**
- Clean separation of concerns
- Easy maintenance
- Independent development
- No risk of breaking existing code

