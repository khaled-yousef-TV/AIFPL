# LSTM Implementation - Phase Breakdown

## Overview
Transform the current point-in-time prediction system (XGBoost/Heuristic) into a temporal LSTM-based system that maintains hidden state (form/fatigue) across gameweeks.

## ⚠️ Architecture Separation

**CRITICAL**: The LSTM implementation is **completely independent** from existing ML code.

- **Zero Dependencies**: LSTM does NOT import from `ml/features.py` or `ml/predictor.py`
- **Own Modules**: LSTM has its own feature extraction, data loader, model, and predictor
- **Clean Integration**: API integration is minimal - just another method option
- **See**: `LSTM_ARCHITECTURE_SEPARATION.md` for complete separation strategy

**Directory Structure**:
```
backend/ml/
├── features.py          # ✅ EXISTING - XGBoost/heuristic (DO NOT USE in LSTM)
├── predictor.py         # ✅ EXISTING - XGBoost/heuristic (DO NOT USE in LSTM)
└── lstm/               # 🆕 NEW - Completely independent
    ├── features.py      # Own feature extraction
    ├── data_loader.py   # Own data processing
    ├── model.py         # Own model
    ├── predictor.py     # Own predictor
    └── ...
```

---

## Phase 1: Data Processing & Sequence Generation
**Goal**: Transform raw FPL data into 3D tensors for LSTM training

**Deliverables**:
- `backend/ml/lstm/data_loader.py` - Sequence generation and 3D tensor creation
- `backend/ml/lstm/__init__.py` - Package initialization

**Key Features**:
1. **Sliding Window**: Create sequences of 5 consecutive gameweeks per player
   - Input: `[GW-4, GW-3, GW-2, GW-1, GW]` → Output: `GW+1` points
2. **3D Tensor Structure**: `[samples, timesteps=5, features=~20]`
3. **Missing Data Handling**: Masking layer support for injuries/rotation
4. **Normalization**: StandardScaler for feature normalization
5. **Temporal Split**: Train (2022/23-2023/24), Val (2024/25 first 15 GWs), Test (current GW)

**Dependencies**:
- Existing: `backend/ml/features.py`, `backend/fpl/client.py`
- New: `scikit-learn` (already installed for StandardScaler)

**Estimated Time**: 2-3 hours

**Success Criteria**:
- Can generate 3D tensors from FPL API data
- Handles missing games correctly
- Proper train/val/test split by season

---

## Phase 2: LSTM Model Architecture
**Goal**: Build PyTorch LSTM model with proper architecture

**Deliverables**:
- `backend/ml/lstm/model.py` - LSTM model class
- `backend/ml/lstm/dataset.py` - PyTorch Dataset wrapper

**Key Features**:
1. **Model Architecture**:
   - Layer 1: LSTM(128 units, return_sequences=True)
   - Layer 2: LSTM(64 units, return_sequences=False)
   - Layer 3: Dropout(0.2)
   - Layer 4: Dense(1, ReLU) → Expected Points
2. **Masking Support**: Handle missing timesteps
3. **Model Persistence**: Save/load PyTorch `.pt` files
4. **Inference Interface**: `predict()` method compatible with existing `PointsPredictor`

**Dependencies**:
- New: `torch>=2.0.0` (PyTorch)

**Estimated Time**: 2-3 hours

**Success Criteria**:
- Model can forward pass 3D tensors
- Model can save/load weights
- Compatible with existing predictor interface

---

## Phase 3: Training Pipeline
**Goal**: Train LSTM model on historical data

**Deliverables**:
- `backend/ml/lstm/trainer.py` - Training script/class
- Training script/command to run training
- Model checkpoint saving

**Key Features**:
1. **Training Loop**:
   - DataLoader with batching
   - Huber Loss (robust to outliers)
   - Adam optimizer
   - Learning rate scheduling
2. **Early Stopping**: Based on validation loss
3. **Checkpointing**: Save best model during training
4. **Metrics Logging**: Track train/val loss, MAE, RMSE

**Dependencies**:
- Phase 1 (data loader)
- Phase 2 (model architecture)

**Estimated Time**: 3-4 hours

**Success Criteria**:
- Model trains successfully on historical data
- Validation loss decreases
- Best model checkpoint saved

---

## Phase 4: API Integration
**Goal**: Integrate LSTM predictor into existing API

**Deliverables**:
- Update `backend/ml/predictor.py` - Add `LSTMPredictor` class
- Update `backend/api/main.py` - Add LSTM method to `/api/suggested-squad`
- Model loading logic (check if trained model exists)

**Key Features**:
1. **LSTMPredictor Class**: 
   - Same interface as `PointsPredictor`
   - Loads trained model on initialization
   - Falls back to heuristic if model not found
2. **API Endpoint**: `/api/suggested-squad?method=lstm`
3. **Feature Extraction**: Reuse existing `FeatureEngineer`
4. **Sequence Generation**: Convert current player state to sequence format

**Dependencies**:
- Phase 1 (data loader)
- Phase 2 (model)
- Phase 3 (trained model)

**Estimated Time**: 1-2 hours

**Success Criteria**:
- API endpoint returns LSTM predictions
- Frontend "Squad LSTM" tab works
- Graceful fallback if model unavailable

---

## Phase 5: Integer Programming Optimizer (Optional)
**Goal**: Replace greedy algorithm with global optimization

**Deliverables**:
- `backend/ml/lstm/optimizer.py` - IP optimization class
- Update `_build_optimal_squad()` to use IP when available

**Key Features**:
1. **PuLP Integration**:
   - Decision variables: Binary (player selected or not)
   - Objective: Maximize ∑predicted_points
   - Constraints:
     - Budget ≤ £100.0
     - Max 3 players per team
     - Valid formation (1 GK, 3-5 DEF, 3-5 MID, 1-3 FWD)
2. **Multi-GW Support**: Optimize across 3-5 gameweeks
3. **Fallback**: Use greedy if IP solver fails

**Dependencies**:
- New: `pulp>=2.7.0` (optional)
- Can use existing greedy as fallback

**Estimated Time**: 2-3 hours

**Success Criteria**:
- IP optimizer finds valid squads
- Results comparable/better than greedy
- Handles edge cases gracefully

**Note**: This is optional. Greedy algorithm may be sufficient for most cases.

---

## Phase 6: Multi-Gameweek Predictions (Future Enhancement)
**Goal**: Predict points for next 3-5 gameweeks (not just next GW)

**Deliverables**:
- Update LSTM model to output sequence (not just single value)
- Update optimizer to use multi-GW predictions

**Key Features**:
1. **Sequence Output**: LSTM predicts `[GW+1, GW+2, GW+3, GW+4, GW+5]`
2. **Optimization**: Maximize total points across 3-5 gameweeks
3. **Weighted Optimization**: Can weight earlier GWs more heavily

**Dependencies**:
- All previous phases
- Model architecture change (output sequence)

**Estimated Time**: 3-4 hours

**Status**: Future enhancement, not in initial plan

---

## Implementation Order

### Recommended Sequence:
1. **Phase 1** → Foundation (data processing)
2. **Phase 2** → Core (model architecture)
3. **Phase 3** → Training (get a working model)
4. **Phase 4** → Integration (make it usable)
5. **Phase 5** → Optimization (optional improvement)
6. **Phase 6** → Enhancement (future)

### Critical Path:
- Phase 1 → Phase 2 → Phase 3 → Phase 4 (must be sequential)
- Phase 5 can be done in parallel or after Phase 4
- Phase 6 is future work

---

## Dependencies Summary

### Python Packages to Add:
```python
# requirements.txt additions:
torch>=2.0.0          # PyTorch for LSTM (Phase 2)
pulp>=2.7.0           # Integer programming (Phase 5, optional)
```

### Existing Dependencies (Already Installed):
- `numpy>=1.24.0` ✅
- `scikit-learn` (via XGBoost) ✅
- `requests>=2.31.0` ✅

---

## File Structure

```
backend/ml/
├── __init__.py
├── features.py          # ✅ EXISTS - Feature extraction
├── predictor.py         # ✅ EXISTS - Add LSTMPredictor here
└── lstm/               # NEW DIRECTORY
    ├── __init__.py
    ├── data_loader.py  # Phase 1: Sequence generation
    ├── model.py        # Phase 2: LSTM architecture
    ├── dataset.py      # Phase 2: PyTorch Dataset
    ├── trainer.py      # Phase 3: Training pipeline
    └── optimizer.py    # Phase 5: IP optimization (optional)
```

---

## Testing Strategy

### Phase 1 Testing:
- Generate sequences for sample players
- Verify 3D tensor shape: `[samples, 5, features]`
- Test missing data handling
- Verify train/val/test split

### Phase 2 Testing:
- Forward pass with dummy data
- Save/load model weights
- Verify output shape: `[batch, 1]` (predicted points)

### Phase 3 Testing:
- Training completes without errors
- Validation loss decreases
- Model checkpoint saved

### Phase 4 Testing:
- API endpoint returns predictions
- Frontend displays LSTM squad
- Fallback works if model missing

### Phase 5 Testing:
- IP optimizer finds valid squads
- Results match or beat greedy algorithm
- Handles edge cases (tight budget, etc.)

---

## Risk Mitigation

1. **Model Training Time**: LSTM training is slower than XGBoost
   - **Mitigation**: Train offline, deploy pre-trained model
   - **Fallback**: Keep heuristic predictor as backup

2. **Data Quality**: Missing/incomplete historical data
   - **Mitigation**: Robust masking layer, handle missing games gracefully
   - **Fallback**: Use defaults/zeros for missing features

3. **Prediction Latency**: LSTM inference slower than XGBoost
   - **Mitigation**: Batch predictions, caching
   - **Fallback**: Async processing, show loading state

4. **Model Size**: PyTorch models larger than XGBoost
   - **Mitigation**: Model compression, efficient storage
   - **Fallback**: Store in cloud, lazy load

---

## Success Metrics

### Phase 1:
- ✅ Can generate 10,000+ sequences from historical data
- ✅ Proper handling of missing games
- ✅ Correct train/val/test split

### Phase 2:
- ✅ Model architecture matches specification
- ✅ Forward pass works with 3D tensors
- ✅ Model save/load functional

### Phase 3:
- ✅ Training completes successfully
- ✅ Validation MAE < 2.0 (better than heuristic baseline)
- ✅ Model checkpoint saved

### Phase 4:
- ✅ API endpoint functional
- ✅ Frontend integration complete
- ✅ Graceful fallback working

### Phase 5 (Optional):
- ✅ IP optimizer finds valid squads
- ✅ Results comparable to greedy
- ✅ Performance acceptable (< 5 seconds)

---

## Estimated Total Time

- **Phase 1**: 2-3 hours
- **Phase 2**: 2-3 hours
- **Phase 3**: 3-4 hours
- **Phase 4**: 1-2 hours
- **Phase 5**: 2-3 hours (optional)

**Total (Phases 1-4)**: 8-12 hours
**With Phase 5**: 10-15 hours

---

## Ready to Begin?

Once you approve this breakdown, we'll start with **Phase 1: Data Processing & Sequence Generation**.

