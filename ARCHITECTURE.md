# AIFPL Architecture Guide

This document describes the codebase architecture for AI-assisted development and future maintenance.

## Overview

AIFPL is a Fantasy Premier League (FPL) squad suggester with:
- **Backend**: Python FastAPI with ML predictions
- **Frontend**: React TypeScript dashboard

---

## Backend Architecture

### Directory Structure

```
backend/
├── api/
│   ├── main.py              # App entry point, lifecycle, scheduler
│   ├── models.py            # Shared Pydantic request models
│   ├── response_models.py   # Shared Pydantic response models
│   ├── config.py            # Environment validation
│   └── routes/              # HTTP route handlers (thin layer)
│       ├── predictions.py   # /api/predictions, /api/top-picks, etc.
│       ├── transfers.py     # /api/transfer-suggestions, /api/wildcard
│       ├── suggested_squad.py # /api/suggested-squad
│       ├── fpl_teams.py     # /api/fpl-teams/*, /api/fpl-teams/import/*
│       ├── selected_teams.py # /api/selected-teams/*
│       ├── players.py       # /api/players/search
│       ├── tasks.py         # /api/tasks/*
│       ├── squads.py        # /api/saved-squads/*
│       ├── chips.py         # /api/chips/* (triple captain, etc.)
│       ├── health.py        # /api/health, /api/betting-odds-status
│       ├── gameweek.py      # /api/gameweek
│       └── top_picks.py     # Additional top picks routes
├── services/                # Business logic layer
│   ├── dependencies.py      # Centralized dependency injection
│   ├── cache.py             # Caching utilities
│   ├── prediction_service.py    # Player prediction logic
│   ├── squad_service.py         # Squad optimization (MILP)
│   ├── transfer_service.py      # Transfer suggestions engine
│   ├── wildcard_service.py      # Wildcard planning
│   ├── fpl_import_service.py    # FPL team import
│   └── scheduler_service.py     # Background job definitions
├── ml/                      # Machine learning models
│   ├── predictor.py         # HeuristicPredictor, FormPredictor, etc.
│   ├── features.py          # FeatureEngineer
│   └── chips.py             # TripleCaptainOptimizer
├── fpl/                     # FPL API client
│   └── client.py            # FPLClient wrapper
├── data/                    # Data utilities
│   ├── european_teams.py    # Rotation risk assessment
│   ├── trends.py            # Team trend analysis
│   └── betting_odds.py      # Betting odds integration
├── database/                # Database layer
│   ├── models.py            # SQLAlchemy models
│   └── crud.py              # DatabaseManager CRUD operations
├── engine/                  # Optimization engines
│   └── mini_rebuild.py      # WildcardEngine
└── constants.py             # PlayerStatus, PlayerPosition enums
```

### Key Principles

1. **Thin Routes**: Route handlers only do HTTP processing, delegate to services
2. **Services Layer**: All business logic lives in `services/`
3. **Dependency Injection**: Use `get_dependencies()` from `services/dependencies.py`
4. **Caching**: Use `cache.get/set` from `services/cache.py`

### Adding New Endpoints

1. **Create a service** in `services/` with the business logic
2. **Create a route** in `api/routes/` that calls the service
3. **Register the route** in `api/routes/__init__.py`
4. **Include the router** in `api/main.py`

Example:
```python
# services/my_service.py
async def do_something():
    deps = get_dependencies()
    # ... logic using deps.fpl_client, deps.db_manager, etc.

# api/routes/my_route.py
@router.get("/my-endpoint")
async def my_endpoint():
    return await my_service.do_something()
```

---

## Frontend Architecture

### Directory Structure

```
frontend/src/
├── App.tsx                  # Main app component (state, tabs)
├── main.tsx                 # Entry point
├── index.css                # Global styles
├── types/                   # TypeScript interfaces
│   ├── player.ts            # Player interface
│   ├── squad.ts             # SuggestedSquad, SquadPlayer, SelectedTeam
│   ├── transfer.ts          # TransferSuggestion
│   ├── task.ts              # Task, TaskStatus, Notification
│   ├── fpl.ts               # GameWeekInfo, SavedFplTeam
│   └── index.ts             # Re-exports all types
├── api/                     # API client functions
│   ├── client.ts            # Base apiRequest, apiFetch
│   ├── gameweek.ts          # fetchGameweek
│   ├── predictions.ts       # fetchPredictions, fetchTopPicks, etc.
│   ├── transfers.ts         # fetchTransferSuggestions, fetchWildcard
│   ├── tasks.ts             # fetchTasks, createTask, updateTask
│   ├── chips.ts             # fetchTripleCaptain
│   ├── fpl.ts               # fetchSavedFplTeams, importFplTeam
│   ├── players.ts           # searchPlayers
│   └── index.ts             # Re-exports all API functions
├── components/              # Reusable UI components
│   ├── FPLLogo.tsx          # Logo SVG component
│   ├── PlayerCard.tsx       # Player display card
│   ├── LoadingSpinner.tsx   # Loading indicator
│   └── index.ts             # Re-exports all components
├── tabs/                    # Tab content components
│   ├── HomeTab.tsx          # Home/navigation tab
│   └── index.ts             # Re-exports all tabs
└── constants.ts             # API_BASE, localStorage keys
```

### Key Principles

1. **Types in `types/`**: All interfaces defined separately
2. **API calls in `api/`**: No direct fetch() in components
3. **Reusable components in `components/`**: Shared UI elements
4. **Tab content in `tabs/`**: Each major tab as a component

### Adding New Features

1. **Define types** in `types/` if needed
2. **Add API function** in `api/` for backend calls
3. **Create component** in `components/` for reusable UI
4. **Create tab** in `tabs/` for major new sections
5. **Update App.tsx** to include the new tab

---

## API Endpoints Reference

### Predictions
- `GET /api/predictions` - Player predictions
- `GET /api/top-picks` - Top 5 per position
- `GET /api/differentials` - Low-ownership picks
- `GET /api/team-trends` - Team momentum data

### Squad Building
- `GET /api/suggested-squad` - AI-optimized squad
- `POST /api/transfer-suggestions` - Transfer recommendations
- `POST /api/wildcard` - Wildcard planning

### FPL Data
- `GET /api/fpl-teams` - Saved team IDs
- `POST /api/fpl-teams` - Save team ID
- `GET /api/fpl-teams/import/{team_id}` - Import team
- `GET /api/gameweek` - Current/next gameweek
- `GET /api/players/search` - Player search

### Saved Data
- `GET/POST /api/saved-squads` - User-saved squads
- `GET/POST /api/selected-teams` - Selected teams by GW
- `GET/POST/PUT/DELETE /api/tasks` - Background tasks

### Chips
- `GET /api/chips/triple-captain` - TC recommendations

### System
- `GET /api/health` - Health check
- `POST /api/wake-up` - Wake up Render free tier

---

## Development Guidelines

### Backend Changes
```bash
# Verify backend imports
cd /Users/khaledyousef/AIFPL
source .venv/bin/activate
PYTHONPATH=backend python -c "from backend.api.main import app; print('OK')"
```

### Frontend Changes
```bash
# Verify frontend build
cd frontend && npm run build
```

### Before Committing
1. Run backend import check
2. Run frontend build
3. Commit and push immediately (hobby project rule)

---

## File Size Guidelines

| File Type | Target Size |
|-----------|-------------|
| Route files | < 150 lines |
| Service files | < 500 lines |
| React tabs | < 300 lines |
| Components | < 100 lines |

If a file exceeds these limits, consider splitting it.

---

## Dependencies Graph

```
main.py
  └── api/routes/* (HTTP handlers)
        └── services/* (business logic)
              └── fpl/client (FPL API)
              └── ml/* (predictions)
              └── database/crud (persistence)
              └── data/* (utilities)
```

---

Last updated: January 2026

