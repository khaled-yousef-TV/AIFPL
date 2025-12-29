---
name: Chip Tabs Implementation
overview: Implement three FPL chip optimization tabs (Triple Captain, Bench Boost, Wildcard) using existing XGBoost/heuristic building blocks. NOTE: LSTM is NOT available after revert - will use XGBoost/heuristic for predictions instead. Phased approach starting from simplest (TC) to most complex (WC).
todos: []
---

# Chip Tabs Implementat

ion Plan

## Current Building Blocks

### Available Components

- **XGBoost Predictor**: Exists as `PointsPredictor` class (`backend/ml/predictor.py`) ✅
- **Heuristic Predictors**: `HeuristicPredictor`, `FormPredictor`, `FixturePredictor` classes (`backend/ml/predictor.py`) ✅
- **FPL Client**: Data source with fixtures, players, gameweeks (`backend/fpl/client.py`) ✅
- **Feature Engineering**: `FeatureEngineer` class with FDR support (`backend/ml/features.py`) ✅
- **Frontend Tab System**: Navigation tabs in `frontend/src/App.tsx` (see `navigationTabs` array) ✅
- **Fixture Difficulty**: FDR system available (`team_h_difficulty`, `team_a_difficulty` in fixtures) ✅
- **Chips Module**: Empty `backend/ml/chips/` directory exists (just `__init__.py`) ✅

### Missing Components (Critical)

- **LSTM Model**: ❌ NOT AVAILABLE - LSTM implementation was removed in revert
  - No `backend/ml/lstm/predictor.py`
  - No `backend/ml/lstm/model.py`
  - Only checkpoints directory exists (empty)
  - **Impact**: TC needs LSTM for xG/xA predictions, BB/WC need LSTM for multi-GW forecasting

### Missing Components

- **LSTM Model**: ❌ Complete LSTM implementation needed (was removed in revert)
  - Model architecture, training, predictor - all need to be rebuilt
  - This is a prerequisite for all chip implementations
- Multi-gameweek LSTM forecasting (needed for BB and WC)
- XGBoost static overlay integration (needed for WC)
- Opponent Elo ratings (needed for WC seasonality)
- MILP optimizer (needed for BB and WC)
- Minutes predictor (needed for BB)
- Chip-specific algorithms (Monte Carlo, MDP solver)

---

## Implementation Phases

### Phase 1: Triple Captain (TC) - Simplest

**Goal**: Monte Carlo simulation for haul probability (15+ points)**Files to Create**:

- `backend/ml/chips/__init__.py` - Package initialization
- `backend/ml/chips/triple_captain.py` - Monte Carlo simulation engine
- `backend/ml/chips/haul_probability.py` - Haul probability calculator
- `backend/api/routes/chips.py` - Chip API endpoints (new file)
- Update `frontend/src/App.tsx` - Add TC tab

**Key Implementation**:

1. **Poisson Simulation**: Use `scipy.stats.poisson` library to simulate goals and assists independently

   - Goals: `scipy.stats.poisson.rvs(lambda_xg)` where λ = xG prediction
     - **Note**: Since LSTM is not available, use XGBoost/heuristic to get xG from `PlayerFeatures.xG` or calculate from expected_goals
   - Assists: `scipy.stats.poisson.rvs(lambda_xa)` where λ = xA prediction
     - **Note**: Use `PlayerFeatures.xA` or calculate from expected_assists
- Simulate clean sheets (for DEF/GK) based on fixture difficulty
- Calculate bonus points based on goals, assists, BPS

2. **Monte Carlo Loop**: Run 10,000 iterations per player per gameweek

- For each iteration: Sample goals, assists, clean sheets, bonus points
- Calculate total points: `goals*points_per_goal + assists*points_per_assist + clean_sheet_points + bonus_points`
- Count iterations where `total_points ≥ 15` (haul)

3. **Haul Probability**: `haul_probability = haul_count / 10000`
4. **Double Gameweek Detection**: Check fixtures for same gameweek (player plays twice)

- Multiply haul probability by 2 (two chances)
- Weight by fixture difficulty

5. Return recommendations sorted by peak haul probability

**Dependencies to Add**:

- `scipy>=1.10.0` (for Poisson distribution)

**API Endpoint**:

```javascript
GET /api/chips/triple-captain?gameweek_range=5
```

**Frontend**: Add "Triple Captain" tab to `navigationTabs` array**Estimated Time**: 4-6 hours---

### Phase 2: Bench Boost (BB) - Medium Complexity

**Goal**: MILP optimization for 15-man squad (not just XI)**Prerequisites**: Phase 1 complete**Files to Create**:

- `backend/ml/chips/bench_boost.py` - MILP optimizer
- `backend/ml/chips/minutes_predictor.py` - Minutes probability predictor
- **LSTM Multi-GW**: Need to implement LSTM first, then add `predict_multi_gameweek()` method (3-GW horizon)
  - **Alternative**: Use XGBoost with rolling predictions or heuristic-based multi-GW forecast
- Update `backend/api/routes/chips.py` - Add BB endpoint
- Update `frontend/src/App.tsx` - Add BB tab

**Key Implementation**:

1. **Multi-GW Forecasting**: Need LSTM implementation first, then add rolling prediction method

   - **If LSTM available**: Use current sequence to predict GW1, append synthetic entry, predict GW2, GW3
   - **If LSTM not available**: Use XGBoost/heuristic with fixture-based adjustments for 3-GW forecast
   - **Fallback**: Simple heuristic: `pred_gw2 = pred_gw1 * fixture_factor_gw2`, etc.

2. **Minutes Predictor**: Heuristic based on form + fixture difficulty

- `xMins = (form / 10) * (1 - (FDR-1)/5) * availability`
- Filter bench players: `xMins > 0.75`

3. **MILP Optimizer**: Use PuLP

- Decision variables: `x_i` (selected), `start_i` (XI), `bench_i` (bench)
- Objective: `max ∑(xP_i * x_i)` where xP_i is 3-GW sum
- **Strict 15-man Squad Constraints**:
- Exactly 2 Goalkeepers (GKs)
- Exactly 5 Defenders (DEFs)
- Exactly 5 Midfielders (MIDs)
- Exactly 3 Forwards (FWDs)
- Total: 15 players (2 + 5 + 5 + 3 = 15)
- Additional constraints: Budget, max 3 per team, bench minutes (>75%)

**Dependencies to Add**:

- `pulp>=2.7.0` (MILP solver)

**API Endpoint**:

```javascript
POST /api/chips/bench-boost
Body: { budget: 100.0, gameweek_range: 3 }
```

**Frontend**: Add "Bench Boost" tab**Estimated Time**: 5-7 hours---

### Phase 3: Wildcard (WC) - Most Complex

**Goal**: 8-gameweek optimization with LSTM+XGBoost overlay**Prerequisites**: Phase 2 complete (multi-GW LSTM working)**Files to Create**:

- `backend/ml/chips/wildcard_predictor.py` - LSTM + XGBoost integration (or XGBoost-only if LSTM unavailable)
- `backend/ml/chips/wildcard_optimizer.py` - MILP optimizer for 8-GW
- `backend/ml/chips/fdr_overlay.py` - FDR adjustment calculations
- `backend/ml/chips/transfer_decay.py` - Transfer decay logic
- `backend/ml/chips/elo_ratings.py` - Elo rating system (optional for MVP)
- **LSTM 8-GW**: Need LSTM implementation first, then extend to 8-GW forecasting
  - **Alternative**: Use XGBoost with fixture-based multi-GW forecast
- Update `backend/api/routes/chips.py` - Add WC endpoint
- Update `frontend/src/App.tsx` - Add WC tab

**Key Implementation**:

1. **8-GW LSTM Forecasting**: Extend rolling predictions to 8 gameweeks

- Modify `predict_multi_gameweek()` to support `horizon=8`

2. **XGBoost Static Overlay**: Create `WildcardPredictor` class

- Get LSTM 8-GW forecast (temporal patterns)
- Get XGBoost single prediction (static features)
- Calculate 8-GW average FDR
- Combine: `0.7 * LSTM + 0.3 * XGBoost` with FDR adjustment
- FDR factor: `1.0 + (3.0 - avg_fdr) * 0.1` (boost for easy fixtures)

3. **Transfer Decay**: Implement as multiplicative factor in MILP objective

- Decay weights: `[0.8, 0.85, 0.9, 0.95, 1.0, 1.0, 1.0, 1.0]` for GW1-8
- Formula: `Weighted_xP = xP × Decay_Weight[gw]` for each gameweek
- Logic: Can fix GW1 issues with free transfers, GW8 issues are harder
- Apply to objective: `max ∑(Weighted_xP_i)` where `Weighted_xP_i = sum(xP_gw × Decay_Weight[gw] for gw in 1..8)`

4. **MILP Optimizer**: 8-GW optimization with transfer decay

- Objective: `max ∑(Weighted_xP_i * x_i)` where Weighted_xP uses multiplicative decay factor
- Constraints: Budget ≤ Current Team Value, formation (2 GK, 5 DEF, 5 MID, 3 FWD), max 3 per team
- Simplified approach (not full MDP for MVP)

5. **Elo Ratings** (Optional for MVP, Phase 3.5):

- Initialize from team strengths
- Update after matches
- Add to LSTM features as "future" input
- Enables seasonality feature

**API Endpoint**:

```javascript
POST /api/chips/wildcard
Body: { current_squad: [...], budget: 100.0, horizon: 8 }
```

**Frontend**: Add "Wildcard" tab with 8-GW timeline visualization**Estimated Time**: 8-12 hours (MVP), +4-6 hours for Elo ratings---

## File Structure

```javascript
backend/
├── ml/
│   ├── chips/                          # NEW
│   │   ├── __init__.py
│   │   ├── triple_captain.py           # Phase 1
│   │   ├── haul_probability.py         # Phase 1
│   │   ├── bench_boost.py               # Phase 2
│   │   ├── minutes_predictor.py        # Phase 2
│   │   ├── wildcard_predictor.py        # Phase 3
│   │   ├── wildcard_optimizer.py        # Phase 3
│   │   ├── fdr_overlay.py              # Phase 3
│   │   ├── transfer_decay.py           # Phase 3
│   │   └── elo_ratings.py              # Phase 3.5 (optional)
│   │
│   ├── lstm/
│   │   └── predictor.py                # MODIFY: Add multi-GW methods
│   │
│   └── predictor.py                    # EXISTS: XGBoost (use for overlay)
│
├── api/
│   └── routes/
│       └── chips.py                    # NEW: All chip endpoints
│
frontend/
└── src/
    └── App.tsx                          # MODIFY: Add 3 new tabs
```

---

## Dependencies

**Add to `requirements.txt`**:

```python
scipy>=1.10.0          # Phase 1: Poisson distribution
pulp>=2.7.0             # Phase 2 & 3: MILP solver
```

**Already Available**:

- `numpy>=1.24.0` ✅
- `torch>=2.0.0` ✅
- `xgboost` (via existing ML setup) ✅

---

## Integration Points

### Backend API

- Create new router file: `backend/api/routes/chips.py`
- Import into `backend/api/main.py`:
  ```python
            from api.routes import chips
            app.include_router(chips.router, prefix="/api/chips", tags=["chips"])
  ```


### Frontend Tabs

- Add to `navigationTabs` array in `frontend/src/App.tsx`:
  ```typescript
            { id: 'triple-captain', label: 'Triple Captain', ... },
            { id: 'bench-boost', label: 'Bench Boost', ... },
            { id: 'wildcard', label: 'Wildcard', ... }
  ```

- Create tab content components similar to existing tabs

---

## Success Criteria

### Phase 1 (TC)

- ✅ Calculates haul probability (15+ points) accurately
- ✅ Identifies DGW opportunities
- ✅ Recommends optimal gameweek
- ✅ Performance: < 5 seconds per request

### Phase 2 (BB)

- ✅ Optimizes 15-man squad (not just XI)
- ✅ Respects all constraints (budget, formation, minutes)
- ✅ Bench players have >75% start probability
- ✅ Performance: < 10 seconds

### Phase 3 (WC)

- ✅ Finds optimal 8-GW path
- ✅ Considers fixture blocks
- ✅ Minimizes transfer hits (via transfer decay)
- ✅ Performance: < 30 seconds

---

## Testing Strategy

### Unit Tests

- Test Poisson distribution calculations
- Test Monte Carlo simulation accuracy
- Test MILP constraint satisfaction
- Test multi-GW forecasting

### Integration Tests

- Test API endpoints return valid data
- Test frontend tabs display correctly
- Test LSTM integration works
- Test performance meets criteria

---

## Risk Mitigation

1. **Computational Cost (TC)**: Use parallel processing, cache results
2. **MILP Solver Performance**: Pre-filter players, use efficient solver (HiGHS)
3. **State Space Explosion (WC)**: Aggressive pruning, limit transfers per GW
4. **Multi-GW Forecasting Accuracy**: Start with rolling predictions, upgrade to multi-output LSTM later if needed

---

## Summary: What We Have vs What's Needed

### ✅ What We Have

- **XGBoost predictor** (`PointsPredictor` class) ✅
- **Heuristic predictors** (`HeuristicPredictor`, `FormPredictor`, `FixturePredictor`) ✅
- **FPL client** (data source with fixtures, players, gameweeks) ✅
- **Feature engineering** (`FeatureEngineer` with FDR support, xG, xA in `PlayerFeatures`) ✅
- **Frontend tab system** (navigation tabs in `App.tsx`) ✅
- **Fixture difficulty system** (FDR available in fixtures) ✅
- **Chips module directory** (`backend/ml/chips/` exists but empty) ✅

### ❌ What We DON'T Have (Critical)

- **LSTM Model**: ❌ Complete implementation missing (removed in revert)
  - No model, no predictor, no training code
  - Only empty checkpoints directory remains
  - **This is a blocker for chips that need LSTM**

### ❌ What's Needed

**Phase 1 (TC)**:

- **xG/xA source**: Since LSTM unavailable, use `PlayerFeatures.xG` and `PlayerFeatures.xA` from XGBoost/heuristic
- Monte Carlo simulation engine
- Poisson distribution integration
- DGW detection logic
- Haul probability calculator

**Phase 2 (BB)**:

- Multi-GW LSTM forecasting (3-GW)
- Minutes predictor
- MILP optimizer
- Bench optimization logic

**Phase 3 (WC)**:

- **8-GW forecasting**: Implement using XGBoost/heuristic with fixture-based adjustments (LSTM not available)
  - Use rolling predictions: `pred_gw_n = base_pred * product(fixture_factors_gw1_to_gwn)`
- XGBoost static overlay integration (can use existing `PointsPredictor`)
- FDR adjustment layer
- Transfer decay constraint
- 8-GW MILP optimizer
- Elo ratings (optional for MVP)

**All Phases**:**All Phases**:

- Chip API endpoints
- Frontend tabs
- Integration with existing systems

---

## Implementation Rules

### Triple Captain (TC) Simulation Rule

**CRITICAL**: Use `scipy.stats.poisson` library to simulate goals and assists independently, then combine them to calculate the probability of total points ≥ 15.

- Goals: Sample from `scipy.stats.poisson.rvs(lambda_xg)` where λ = LSTM's xG prediction
- Assists: Sample from `scipy.stats.poisson.rvs(lambda_xa)` where λ = LSTM's xA prediction
- Combine: `total_points = goals*points_per_goal + assists*points_per_assist + clean_sheet_points + bonus_points`
- Haul probability: Count iterations where `total_points ≥ 15` / 10000

### Bench Boost (BB) Optimization Rule

**CRITICAL**: Ensure the MILP constraints strictly enforce the 15-man squad rule (2 GKs, 5 DEFs, 5 MIDs, 3 FWDs) while maximizing the total predicted points over the chosen horizon.

- Exactly 2 Goalkeepers: `∑x_i for position=GK == 2`
- Exactly 5 Defenders: `∑x_i for position=DEF == 5`
- Exactly 5 Midfielders: `∑x_i for position=MID == 5`
- Exactly 3 Forwards: `∑x_i for position=FWD == 3`
- Total: 15 players (2 + 5 + 5 + 3 = 15)
- Objective: `max ∑(xP_i * x_i)` where xP_i is 3-GW sum

### Wildcard (WC) Transfer Decay Rule

**CRITICAL**: Implement the transfer decay as a multiplicative factor: `Weighted_xP = xP × Decay_Weight`. This should be applied to the objective function in the MILP solver.

- Decay weights: `[0.8, 0.85, 0.9, 0.95, 1.0, 1.0, 1.0, 1.0]` for GW1-8