# Betting Odds Integration Plan for FPL AI

## Overview
Incorporate betting odds into the player selection process to enhance prediction accuracy. Betting markets provide aggregated intelligence from bookmakers, reflecting probabilities for goals, assists, clean sheets, and other FPL-relevant outcomes.

## 1. Data Source & API Selection

### Options:
1. **The Odds API** (recommended)
   - Free tier: 500 requests/month
   - Provides odds from multiple bookmakers
   - Covers Premier League markets
   - Endpoint: `https://api.the-odds-api.com/v4/sports/soccer_epl/odds/`

2. **Betfair API**
   - Free but requires account
   - Excellent coverage, real-time odds
   - More complex setup (OAuth, API keys)

3. **Oddschecker API** (paid)
   - Comprehensive coverage
   - Requires subscription

4. **Alternative: Web scraping** (fallback)
   - Oddschecker.com (requires careful handling)
   - Not recommended for production (rate limits, legal considerations)

### Recommendation: Start with The Odds API
- Simple setup
- Good free tier for testing
- Can switch to Betfair later if needed

## 2. Relevant Betting Markets for FPL

### For Attackers (FWD/MID):
- **Anytime Goalscorer** (primary)
  - Most relevant for FPL points
  - Converts to probability: `implied_prob = 1 / decimal_odds`
  - Higher probability = higher chance of goal (4-6 FPL points)

- **First Goalscorer** (secondary)
  - Bonus points potential
  - More volatile but strong signal

- **Player to Score 2+ Goals** (tertiary)
  - Rare but high-value (bonus points)
  - Can identify premium captaincy options

### For Defenders/Goalkeepers:
- **Team Clean Sheet** (primary)
  - Critical for DEF/GK (4 FPL points)
  - Converts to probability
  - Higher probability = better DEF/GK pick

- **Team to Win** (secondary)
  - Bonus points correlation
  - Indicates defensive/attacking strength

- **Both Teams to Score (BTTS)** (tertiary)
  - Inverse signal for clean sheets
  - `clean_sheet_prob ≈ 1 - BTTS_prob` (approximate)

### Team-Level Markets:
- **Match Winner** (home/draw/away)
  - Affects all positions
  - Win bonus points potential

- **Over/Under Goals**
  - High-scoring games favor attackers
  - Low-scoring games favor defenders

## 3. Integration Architecture

### New Components:

```
backend/
├── data/
│   └── betting_odds.py          # Fetching and caching odds
├── ml/
│   ├── features.py              # Add odds features to PlayerFeatures
│   └── predictor.py             # (Optional: train on odds as feature)
├── api/
│   └── main.py                  # Integrate odds into player scoring
```

### Data Flow:

1. **Fetch Odds** (before each GW deadline)
   - Call betting API for upcoming fixtures
   - Parse and store odds for relevant markets
   - Cache for 24 hours (odds change frequently but update daily is sufficient)

2. **Match Players to Markets**
   - Map FPL player names to betting market names
   - Handle variations (e.g., "Mohamed Salah" vs "Mo Salah")
   - Create fallback matching logic

3. **Convert Odds to Probabilities**
   - Decimal odds: `prob = 1 / odds`
   - Handle bookmaker margin (overround): normalize probabilities
   - Average across multiple bookmakers for robustness

4. **Enhance Player Scoring**
   - Add odds-based bonuses to `player_score()` in `_build_optimal_squad()`
   - Weight by position (goalscorer odds for FWD/MID, clean sheet for DEF/GK)
   - Combine with existing prediction scores

## 4. Implementation Details

### Step 1: Create Betting Odds Module (`backend/data/betting_odds.py`)

```python
class BettingOddsClient:
    """Fetch and cache betting odds from The Odds API."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.the-odds-api.com/v4"
        self.cache_ttl = 3600  # 1 hour
    
    def get_player_goalscorer_odds(self, fixture_id, player_name) -> float:
        """Get anytime goalscorer odds for a player."""
        # Fetch odds, convert to probability
        pass
    
    def get_team_clean_sheet_odds(self, team_id, fixture_id) -> float:
        """Get clean sheet probability for a team."""
        pass
    
    def get_fixture_odds(self, fixture_id) -> dict:
        """Get all odds for a fixture (winner, BTTS, O/U goals)."""
        pass
```

### Step 2: Enhance Player Features

Add to `PlayerFeatures`:
```python
@dataclass
class PlayerFeatures:
    # ... existing features ...
    
    # Betting odds (probabilities, 0-1)
    anytime_goalscorer_prob: float = 0.0
    clean_sheet_prob: float = 0.0  # For DEF/GK
    team_win_prob: float = 0.0
    btts_prob: float = 0.0
```

### Step 3: Integrate into Player Scoring

Modify `player_score()` in `_build_optimal_squad()`:

```python
def player_score(p):
    # ... existing scoring logic ...
    
    # Betting odds bonus
    odds_bonus = 0.0
    if pos_id in [3, 4]:  # MID/FWD
        # Goalscorer probability weighted by position value
        goalscorer_prob = p.get("anytime_goalscorer_prob", 0.0)
        odds_bonus += goalscorer_prob * 3.0  # 3 points for goal expectation
        
    elif pos_id in [1, 2]:  # GK/DEF
        # Clean sheet probability
        cs_prob = p.get("clean_sheet_prob", 0.0)
        odds_bonus += cs_prob * 2.0  # 2 points for clean sheet expectation
    
    # Team win bonus (affects all positions)
    win_prob = p.get("team_win_prob", 0.5)
    win_bonus = (win_prob - 0.5) * 0.5  # Small bonus for favored teams
    
    score = pred + fixture_bonus + form_bonus + ... + odds_bonus + win_bonus
```

### Step 4: Name Matching Strategy

Challenge: FPL names vs betting market names
- FPL: "Mohamed Salah"
- Betting: "Mo Salah", "M. Salah", "Salah"

Solution:
1. Exact match
2. Normalize both (lowercase, remove accents, remove periods)
3. Fuzzy matching (Levenshtein distance)
4. Fallback: use team average if player-specific odds unavailable

## 5. Weighting & Calibration

### How to Weight Odds vs Existing Predictions:

**Option A: Additive Bonus** (recommended for start)
- Odds add a bonus on top of existing predictions
- Easier to calibrate and understand
- Formula: `final_score = ml_prediction + odds_bonus`

**Option B: Multiplicative**
- Odds multiply existing predictions
- More aggressive but can overcorrect
- Formula: `final_score = ml_prediction * (1 + odds_multiplier)`

**Option C: Hybrid**
- Use odds to adjust predictions for specific events
- Goalscorer odds → adjust goal probability
- Clean sheet odds → adjust CS probability
- More complex but potentially more accurate

**Recommendation**: Start with **Option A** (additive), weight odds at 20-30% of total score influence. Monitor and adjust.

## 6. Caching Strategy

- **Cache duration**: 1-6 hours (odds change but not minute-by-minute)
- **Refresh trigger**: 
  - Before each GW deadline
  - On-demand API call with rate limiting
  - Store in database or in-memory cache

## 7. Error Handling & Fallbacks

- **Missing odds**: If player/fixture odds unavailable, use:
  1. Team average odds
  2. Position average odds
  3. Fall back to existing prediction (no odds bonus)
  
- **API failures**: 
  - Continue without odds (graceful degradation)
  - Log warnings for monitoring
  - Retry with exponential backoff

## 8. Testing & Validation

1. **A/B Testing**: Compare squad selections with/without odds
2. **Backtesting**: Test on historical GWs with historical odds (if available)
3. **Correlation Analysis**: Verify odds probabilities correlate with actual outcomes
4. **Performance Metrics**: Track if odds improve prediction accuracy

## 9. Implementation Phases

### Phase 1: MVP (Week 1)
- ✅ Set up The Odds API client
- ✅ Fetch and parse goalscorer odds for next GW
- ✅ Basic name matching (exact + normalized)
- ✅ Add simple odds bonus to player scoring (additive)
- ✅ Deploy and monitor

### Phase 2: Enhanced (Week 2)
- ✅ Add clean sheet odds for DEF/GK
- ✅ Team-level odds (win, BTTS)
- ✅ Improve name matching (fuzzy matching)
- ✅ Caching and rate limiting

### Phase 3: Optimization (Week 3+)
- ✅ Calibrate weights based on performance
- ✅ Add more markets (first goalscorer, 2+ goals)
- ✅ Historical analysis and backtesting
- ✅ Fine-tune integration

## 10. Configuration

Add to environment variables:
```bash
THE_ODDS_API_KEY=your_api_key_here
BETTING_ODDS_ENABLED=true
BETTING_ODDS_WEIGHT=0.25  # 25% influence on final score
```

## 11. Costs & Rate Limits

- **The Odds API Free Tier**: 500 requests/month
- **Usage estimate**: 
  - 10 fixtures/week × 50 players/fixture = ~500 player lookups/week
  - Need to batch requests efficiently
  - Consider upgrading to paid tier if successful

## Questions to Consider

1. **Which API to use?** Start with The Odds API free tier, upgrade if needed
2. **How often to update?** Daily updates (before GW deadline) should be sufficient
3. **Weight of odds vs ML predictions?** Start conservative (20-30%), adjust based on results
4. **Should odds be a feature in ML training?** Initially no (separate signal), but could train model on odds later

## Next Steps

1. Get API key from The Odds API
2. Create `betting_odds.py` module with client
3. Add odds fetching to squad building flow
4. Integrate into player scoring
5. Test on next GW and compare results
6. Iterate based on performance

