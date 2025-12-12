# 🤖 FPL AI Agent

An AI-powered Fantasy Premier League agent that automatically manages your FPL team using machine learning predictions and intelligent decision-making.

![Python](https://img.shields.io/badge/Python-3.9+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-green)
![React](https://img.shields.io/badge/React-Dashboard-61dafb)

## ✨ Features

- **🎯 Points Prediction** - ML-powered player points predictions
- **👑 Captain Selection** - Intelligent captain and vice-captain picks
- **📊 Lineup Optimization** - Optimal starting XI and bench order
- **🔄 Transfer Suggestions** - Smart transfer recommendations
- **🎲 Differential Finder** - Low-ownership high-potential picks
- **⚡ Auto-Execution** - Automatically apply changes before deadline
- **📱 Web Dashboard** - Beautiful UI to monitor your agent

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

## 🚀 Quick Start

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

### 2. Configure

Create a `.env` file:

```env
FPL_EMAIL=your-fpl-email@example.com
FPL_PASSWORD=your-fpl-password
```

### 3. Start Backend

```bash
cd backend
python -m uvicorn api.main:app --reload --port 8000
```

### 4. Start Frontend (Optional)

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

### Authentication
- `POST /api/auth/login` - Login to FPL
- `GET /api/auth/status` - Check auth status
- `POST /api/auth/logout` - Logout

### Team
- `GET /api/team/current` - Get current team
- `GET /api/team/info` - Get team stats

### Predictions
- `GET /api/predictions` - Get player predictions

### Recommendations
- `GET /api/recommendations/captain` - Captain pick
- `GET /api/recommendations/transfers` - Transfer suggestions
- `GET /api/recommendations/differentials` - Differential picks

### Actions
- `POST /api/actions/set-lineup` - Set team lineup

## ⚙️ Configuration

### Settings

| Setting | Description |
|---------|-------------|
| `auto_execute` | Auto-apply decisions before deadline |
| `differential_mode` | Prefer low-ownership picks |
| `notification_email` | Email for notifications |

### Scheduler

The scheduler runs:
- **Daily at 8 AM** - Update predictions
- **1 hour before deadline** - Execute decisions (if enabled)

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

- Credentials stored locally in `.env`
- Session cookies encrypted
- No data sent to external servers

## 🛣️ Roadmap

- [ ] XGBoost model training on historical data
- [ ] Chip strategy (Wildcard, Bench Boost, Triple Captain)
- [ ] Mini-league tracking
- [ ] Mobile app
- [ ] Discord/Slack notifications

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch
3. Make changes
4. Submit a PR

## 📄 License

MIT License

---

**Built with ❤️ by [Khaled Yousef](https://khaledyousef.io)**
