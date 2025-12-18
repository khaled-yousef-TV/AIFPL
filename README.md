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
- **📈 Trend reversal signal**: “bounce-back spots” for strong teams underperforming recently
- **💾 Saved squads**: save/load/edit squads locally so you don’t re-enter weekly
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
