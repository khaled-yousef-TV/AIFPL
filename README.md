# 🤖 FPL AI Squad Suggester

An AI-powered Fantasy Premier League **squad + transfers suggester** with a fast dashboard UI.

Built for **manual decision support** (no login required): it uses public FPL data and explains its picks.

![Python](https://img.shields.io/badge/Python-3.9+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-green)
![React](https://img.shields.io/badge/React-Dashboard-61dafb)

## ✨ Features

### Prediction Methods
- **🧠 Multi-method predictions**: 
  - **Statistics-based**: Heuristic / Form-focused / Fixture-focused + **Combined average**
  - **🆕 LSTM Neural Network**: Temporal sequence model (maintains hidden state for form/fatigue) - *Coming Soon*
- **👥 Suggested Squad**: full 15-man squad + best XI + formation
- **🧢 Captain & Vice**: picked from the suggested XI

### Transfer Management
- **🔁 Transfers Tab**: 
  - **Quick Transfers** (1-3 free transfers): AI-powered transfer suggestions with reasons
  - **🆕 Wildcard** (4+ free transfers): Coordinated multi-transfer optimization considering future fixtures (next 5 gameweeks)
  - **FPL rules enforced** (e.g. **max 3 players per club**, formation constraints)
  - **"Why this player over teammates?"** comparisons (same club + position)
  - **Grouped suggestions**: Multiple options for same player grouped in pills
  - **Premium player protection**: High-value players only kept if performing well (form-based)
- **🆕 Free Hit of the Week**: View saved suggested squads (auto-saved 30 mins before each deadline)
- **💷 Selling price editing**: use your **selling price** (can differ from current price)
- **🔎 Player search**: search by player name or team (e.g. `Spurs`, `TOT`) + cheap bench fodder lists

### Advanced Features
- **🛫 European rotation risk**: UCL/UEL/UECL congestion affects scores + displayed badges
- **📈 Trend reversal signal**: "bounce-back spots" for strong teams underperforming recently
- **💰 Betting odds integration**: Incorporate bookmaker odds (goalscorer, clean sheets) to enhance predictions
- **💾 Saved squads**: save/load/edit squads server-side (syncs across devices, PostgreSQL on Render)
- **💾 Draft squad**: auto-save uses localStorage (local-only, temporary work-in-progress)

## 🏗️ Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   React UI      │────▶│   FastAPI        │────▶│   FPL API       │
│   Dashboard     │◀────│   Backend        │◀────│   (Official)    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   ML Predictors      │
                    │   ├─ XGBoost         │
                    │   ├─ Heuristic       │
                    │   ├─ Form/Fixture    │
                    │   └─ LSTM (planned) │
                    │                      │
                    │   Decision Engines   │
                    │   ├─ Transfers       │
                    │   ├─ Wildcard        │
                    │   └─ Squad Builder   │
                    └──────────────────────┘
```

### ML Architecture Separation

**Statistics-based Predictors** (`backend/ml/`):
- `features.py`: Feature extraction for XGBoost/heuristic (point-in-time)
- `predictor.py`: XGBoost, Heuristic, Form, Fixture predictors

**LSTM Predictor** (`backend/ml/lstm/`) - *Planned*:
- **Completely independent module** (zero dependencies on existing ML code)
- Own feature extraction, data loader, model, and predictor
- Temporal sequence model with hidden state (form/fatigue memory)
- See `LSTM_ARCHITECTURE_SEPARATION.md` for details

## 🚀 Quick Start (Local)

### 1. Clone & Setup

```bash
git clone https://github.com/khaled-yousef-TV/AIFPL.git
cd AIFPL

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Start Backend

```bash
cd backend
python -m uvicorn api.main:app --reload --port 8001
```

### 3. Start Frontend (Dashboard)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000

### 4. (Optional) Betting Odds Integration

To enable betting odds integration for enhanced predictions:

1. **Get API key**: Sign up at [The Odds API](https://the-odds-api.com/) (free tier: 500 requests/month)

2. **Set environment variables**:
   ```bash
   # In backend/.env or system environment
   export THE_ODDS_API_KEY=your_api_key_here
   export BETTING_ODDS_ENABLED=true
   export BETTING_ODDS_WEIGHT=0.25  # 25% influence on player scores (0-1)
   ```

3. **Restart backend**: The betting odds client will automatically fetch odds for fixtures

**How it works**:
- Fetches odds for upcoming fixtures (match winner, BTTS, totals)
- Converts odds to probabilities (goalscorer for FWD/MID, clean sheets for DEF/GK)
- Adds odds-based bonuses to player scores in squad selection
- Caches odds for 6 hours to respect API rate limits
- Gracefully degrades if API unavailable (continues without odds)

**Note**: Works without API key - system will continue using ML predictions only.

## 📁 Project Structure

```
AIFPL/
├── backend/
│   ├── api/                 # FastAPI endpoints
│   │   ├── main.py
│   │   ├── config.py
│   │   └── response_models.py
│   ├── fpl/                 # FPL API client
│   │   ├── client.py
│   │   ├── auth.py
│   │   └── models.py
│   ├── ml/                  # ML predictions
│   │   ├── features.py      # Statistics-based feature extraction
│   │   ├── predictor.py     # XGBoost, Heuristic, Form, Fixture
│   │   └── lstm/            # LSTM neural network (planned, independent)
│   │       ├── features.py  # LSTM-specific feature extraction
│   │       ├── data_loader.py
│   │       ├── model.py
│   │       ├── predictor.py
│   │       └── trainer.py
│   ├── engine/              # Decision logic
│   │   ├── captain.py
│   │   ├── lineup.py
│   │   ├── transfers.py
│   │   ├── differentials.py
│   │   └── mini_rebuild.py  # Wildcard engine
│   ├── data/                # External data sources
│   │   ├── betting_odds.py
│   │   ├── european_teams.py
│   │   └── trends.py
│   ├── scheduler/           # Automation
│   │   └── jobs.py
│   ├── database/            # Data storage
│   │   ├── models.py
│   │   └── crud.py
│   └── constants.py         # Shared constants
├── frontend/
│   └── src/
│       ├── App.tsx          # Main dashboard
│       └── ...
├── requirements.txt
├── LSTM_IMPLEMENTATION_PHASES.md      # LSTM implementation roadmap
├── LSTM_ARCHITECTURE_SEPARATION.md    # LSTM separation strategy
└── README.md
```

## 🔌 API Endpoints

### Core Endpoints
- `GET /api/gameweek` – current/next gameweek info
- `GET /api/suggested-squad?method=combined|heuristic|form|fixture|lstm` – suggested squad
  - `combined`: Average of heuristic/form/fixture (default)
  - `heuristic`: Balanced approach
  - `form`: Recent form weighted
  - `fixture`: Fixture difficulty focused
  - `lstm`: LSTM neural network (planned)
- `GET /api/top-picks` – top player picks by position
- `GET /api/differentials` – low ownership, high potential players

### Transfer & Squad Management
- `POST /api/transfer-suggestions` – transfer ideas (1-3 transfers, supports `suggestions_limit`)
- `POST /api/wildcard` – coordinated multi-transfer plan (4+ transfers, considers future fixtures)
- `POST /api/saved-squads` – save/load/edit squads (server-side)
- `GET /api/players/search?q=&position=&limit=` – search by player or team; includes EU badges

### Utility
- `GET /api/team-trends` – debug trend reversal scores
- `GET /api/health` – health check

## 🚢 Deployment (Option A: GitHub Pages + separate backend)

### Frontend (GitHub Pages → `fplai.nl`)
- This repo includes a GitHub Actions workflow: `.github/workflows/deploy-pages.yml`
- It builds the Vite dashboard from `frontend/` and publishes `frontend/dist` to GitHub Pages.
- The workflow bakes in the production API base via:
  - `VITE_API_BASE=https://api.fplai.nl`
- The domain is set via `frontend/public/CNAME` (copied into the build output).

GitHub repo settings you must set once:
- **Repo → Settings → Pages → Source**: **GitHub Actions**
- **Custom domain**: `fplai.nl` (then enable **Enforce HTTPS** once available)

### Backend (hosted at `api.fplai.nl`)
GitHub Pages can’t run Python/FastAPI. Host the backend on a service like:
- Render / Fly.io / Railway / a VPS (Docker)

**Render Setup:**
1. Create a new **Web Service** on Render
2. Connect your GitHub repository
3. Set the following:
   - **Root Directory**: `backend`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
   - **Environment Variables**: Add `THE_ODDS_API_KEY`, `BETTING_ODDS_ENABLED=true`, `BETTING_ODDS_WEIGHT=0.3` (or your preferred weight)
4. Set a **Custom Domain**: `api.fplai.nl` (point your DNS to Render's provided CNAME)

Backend must expose the FastAPI app on HTTPS at:
- `https://api.fplai.nl`

Notes:
- CORS is configured to allow `fplai.nl` and `www.fplai.nl`
- Python version is pinned to 3.12.7 via `runtime.txt`

## 🧠 How It Works

### Points Prediction

**Statistics-based Methods** (Current):
1. **Form-based** - Recent performance weighted
2. **Fixture difficulty** - Opponent strength (current + next 5 GWs)
3. **ICT Index** - FPL's influence/creativity/threat metrics
4. **Expected stats** - xG, xA, xGI, xGC
5. **Betting odds** - Goalscorer/clean sheet probabilities (optional)

**LSTM Neural Network** (Planned):
- **Temporal sequences**: 5-gameweek look-back window
- **Hidden state**: Maintains form/fatigue memory across gameweeks
- **Sequence prediction**: Predicts based on player's recent trajectory
- **See**: `LSTM_IMPLEMENTATION_PHASES.md` for implementation plan

### Decision Engine

- **Captain**: Highest predicted points (with differential option)
- **Lineup**: Formation optimization (3-5-2, 4-4-2, etc.)
- **Quick Transfers** (1-3): Points gain vs cost analysis, grouped suggestions
- **Wildcard** (4+): Coordinated multi-transfer optimization
  - Considers future fixtures (next 5 gameweeks)
  - Protects premium players only if performing well (form-based)
  - Enforces formation constraints strictly
- **Differentials**: Low ownership + high prediction

## 🔒 Security & Data Storage

- Uses **public FPL data** (no login) and runs locally.
- **Saved squads**: Stored server-side in PostgreSQL (on Render) or SQLite (local dev)
  - Syncs across devices
  - Persists across deployments (PostgreSQL on Render)
- **Draft squad**: Auto-save uses localStorage (local-only, temporary work-in-progress)
- **Environment variables**: API keys stored in `.env` (excluded from git)

## 🛣️ Development Roadmap

### ✅ Completed
- [x] Multi-method predictions (Heuristic, Form, Fixture, Combined)
- [x] Transfer suggestions with grouping and reasons
- [x] Wildcard engine (coordinated multi-transfer optimization)
- [x] Free Hit of the Week (saved squad management)
- [x] Betting odds integration
- [x] European rotation risk assessment
- [x] Server-side squad persistence (PostgreSQL)

### 🚧 In Progress
- [ ] LSTM neural network implementation (see `LSTM_IMPLEMENTATION_PHASES.md`)
  - Phase 1: Data processing & sequence generation
  - Phase 2: LSTM model architecture
  - Phase 3: Training pipeline
  - Phase 4: API integration

### 📋 Planned
- [ ] Integer programming optimizer (PuLP/HiGHS) for global optimization
- [ ] Multi-gameweek predictions (3-5 GWs ahead)
- [ ] Chip strategy (Bench Boost, Triple Captain)
- [ ] Price change prediction
- [ ] Minutes prediction model

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch
3. Make changes
4. Submit a PR

## 📚 Documentation

- **`LSTM_IMPLEMENTATION_PHASES.md`**: Complete LSTM implementation roadmap with phases
- **`LSTM_ARCHITECTURE_SEPARATION.md`**: Architecture separation strategy (zero dependencies)
- **`LSTM_IMPLEMENTATION_REVIEW.md`**: Detailed review of existing vs. needed code

## 🔧 Development Notes

### Model Architecture Separation
- **Statistics-based** (`backend/ml/`): XGBoost, Heuristic, Form, Fixture - point-in-time predictions
- **LSTM** (`backend/ml/lstm/`): Completely independent module with own feature extraction, data processing, and model
- **Zero coupling**: LSTM can be removed without affecting existing code
- **See**: `LSTM_ARCHITECTURE_SEPARATION.md` for complete separation strategy

### Key Design Decisions
- **Wildcard logic**: Considers future fixtures (next 5 GWs) over current fixture
- **Premium player protection**: Only protects if form ≥ 4.0 (must perform to be kept)
- **Transfer grouping**: Groups multiple options for same player to reduce clutter
- **Server-side persistence**: PostgreSQL on Render for saved squads

## 📄 License

MIT License

---

**Built with ❤️ by [Khaled Yousef](https://khaledyousef.io)**
