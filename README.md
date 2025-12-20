# 🤖 FPL AI Squad Suggester

An AI-powered Fantasy Premier League **squad + transfers suggester** with a fast dashboard UI.

Built for **manual decision support** (no login required): it uses public FPL data and explains its picks.

![Python](https://img.shields.io/badge/Python-3.9+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-green)
![React](https://img.shields.io/badge/React-Dashboard-61dafb)

## ✨ Features

- **🧠 Multi-method predictions**: Heuristic / Form-focused / Fixture-focused + **Combined average**
- **👥 Suggested Squad**: full 15-man squad + best XI + formation
- **🧢 Captain & Vice**: picked from the suggested XI
- **🔁 My Transfers**: enter your squad and get transfer ideas with reasons
  - **FPL rules enforced** (e.g. **max 3 players per club**)
  - **Hold / Save transfer** suggestion when the best move is marginal
  - **“Why this player over teammates?”** comparisons (same club + position)
  - Supports **more suggestions** via `suggestions_limit` (UI uses your Free Transfers as the default)
- **🛫 European rotation risk**: UCL/UEL/UECL congestion affects scores + displayed badges
- **📈 Trend reversal signal**: "bounce-back spots" for strong teams underperforming recently
- **💰 Betting odds integration**: Incorporate bookmaker odds (goalscorer, clean sheets) to enhance predictions
- **💾 Saved squads**: save/load/edit squads locally so you don't re-enter weekly
- **💷 Selling price editing**: use your **selling price** (can differ from current price)
- **🔎 Player search**: search by player name or team (e.g. `Spurs`, `TOT`) + cheap bench fodder lists

## 🏗️ Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   React UI      │────▶│   FastAPI        │────▶│   FPL API       │
│   Dashboard     │◀────│   Backend        │◀────│   (Official)    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │   ML Predictor   │
                        │   + Decision     │
                        │   Engine         │
                        └──────────────────┘
```

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
│   │   └── main.py
│   ├── fpl/                 # FPL API client
│   │   ├── client.py
│   │   ├── auth.py
│   │   └── models.py
│   ├── ml/                  # ML predictions
│   │   ├── features.py
│   │   └── predictor.py
│   ├── engine/              # Decision logic
│   │   ├── captain.py
│   │   ├── lineup.py
│   │   ├── transfers.py
│   │   └── differentials.py
│   ├── scheduler/           # Automation
│   │   └── jobs.py
│   └── database/            # Data storage
│       ├── models.py
│       └── crud.py
├── frontend/
│   └── src/
│       ├── App.tsx
│       └── api/client.ts
├── requirements.txt
└── README.md
```

## 🔌 API Endpoints
- `GET /api/gameweek` – current/next gameweek info
- `GET /api/suggested-squad?method=combined|heuristic|form|fixture`
- `GET /api/top-picks`
- `GET /api/differentials`
- `POST /api/transfer-suggestions` – transfer ideas (supports `suggestions_limit`)
- `GET /api/players/search?q=&position=&limit=` – search by player or team; includes EU badges
- `GET /api/team-trends` – debug trend reversal scores

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

Uses a hybrid approach:
1. **Form-based** - Recent performance weighted
2. **Fixture difficulty** - Opponent strength
3. **ICT Index** - FPL's influence/creativity/threat metrics
4. **Expected stats** - xG, xA, xGI

### Decision Engine

- **Captain**: Highest predicted points (with differential option)
- **Lineup**: Formation optimization (3-5-2, 4-4-2, etc.)
- **Transfers**: Points gain vs cost analysis
- **Differentials**: Low ownership + high prediction

## 🔒 Security

- Uses **public FPL data** (no login) and runs locally.
- Saved squads are stored in your browser via **localStorage**.

## 🛣️ Next Ideas

- [ ] Better long-term planning (price changes, fixture runs, minutes prediction)
- [ ] Chip strategy (Wildcard, Bench Boost, Triple Captain)
- [ ] Hosted deployment + user accounts (optional)

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch
3. Make changes
4. Submit a PR

## 📄 License

MIT License

---

**Built with ❤️ by [Khaled Yousef](https://khaledyousef.io)**
