# Hermes — AI Orchestrator for the FPL Predictor

Hermes is an LLM "brain" that synthesizes signals from seven domain agents into
squad, captaincy, chip, transfer and differential recommendations — while a
MILP optimizer retains final authority over squad construction.

```
                 ┌─────────────────────────────────────────────┐
                 │                 AGENT LAYER                  │
  FPL API ──────►│ data · mechanics · availability · form ·     │
  Odds API ─────►│ variability · betting · news (LLM+search)    │
  Tavily ───────►└──────────────────┬──────────────────────────┘
                                    │  typed AgentReports
                                    ▼
                 ┌─────────────────────────────────────────────┐
   lessons ─────►│            HERMES (Nous/DeepSeek LLM)        │
   calibration ─►│  bounded adjustments (0.5–1.5x, lock/excl.)  │
                 └──────────────────┬──────────────────────────┘
                                    │  trust-weighted multipliers
                                    ▼
                 ┌─────────────────────────────────────────────┐
                 │        MILP OPTIMIZER (PuLP) — firewall      │
                 │  budget / 2-5-5-3 / max-3-per-club enforced  │
                 └──────────────────┬──────────────────────────┘
                                    ▼
                     API · Hermes tab · Telegram briefings
```

## The MILP firewall
The LLM never picks squads. It emits per-player adjustments (multiplier
clamped to 0.5–1.5, or exclude/lock), validated against real player IDs.
Adjusted predictions feed the existing PuLP optimizer, which alone builds
budget-valid squads. Hallucinations cannot produce an illegal squad.

## The learning loop (over 38 GWs)
1. Daily cron (`06:00 UTC`) evaluates every Hermes run of a finished GW
   against actual points: captain regret, boost/fade hit-rates, transfer
   deltas, per-agent calibration (availability flag accuracy, variability
   band coverage, hot-vs-cold form).
2. A calibration profile (trailing 16 scored runs) + distilled lessons
   (LLM pass, decayed weekly, capped) are injected into every prompt.
3. **Trust weights**: each action type's historical hit-rate shrinks the
   LLM's multipliers toward 1.0 (`1 + (m−1)·trust`, trust ∈ [0.3, 1.0]) —
   a badly calibrated Hermes automatically loses influence.

## Pre-GW1 runbook (cold start — IMPORTANT)
Player GW history resets when the new season opens. **Before that:**

```bash
# 1. Archive the finished season (one-time, ~3 min)
curl -X POST localhost:8001/api/hermes/archive-season
# 2. Confirm
curl localhost:8001/api/hermes/archive-status
```

The archive becomes:
- the **cold-start prior**: during preseason and GWs 1–4, the data agent
  attaches last-season PPG to candidates, the variability agent falls back
  to archived volatility stats, and Hermes' prompt switches to
  preseason/early guidance (don't trust tiny samples, beware promoted
  teams/new signings, lower confidence);
- the **backtest data source** (see below).

Cross-season player matching is by name (FPL re-assigns ids each season).

## Backtesting ("try it on older stuff")
```bash
curl "localhost:8001/api/hermes/backtest?season=2025-26&start_gw=6&end_gw=38"
```
Replays each GW using only data knowable beforehand and scores the core
heuristics: captaincy-by-ceiling vs by-mean vs best-possible, hot-form
top-10 vs league average, consistency core vs league average.
Not covered (no historical data exists): ownership, odds, news, the LLM.

## Endpoints
| Endpoint | Purpose |
|---|---|
| `GET /api/hermes/signals?agents=…&top_n=…` | Run agents (individually testable), no LLM needed |
| `POST /api/hermes/run` `{run_type, fpl_team_id?, force?}` | Start a run (background; poll the task) |
| `GET /api/hermes/runs/{run_id}` / `GET /api/hermes/latest` | Fetch results |
| `GET /api/hermes/status` | Config status |
| `POST /api/hermes/archive-season` / `GET /api/hermes/archive-status` | Season archive |
| `GET /api/hermes/backtest?season=…` | Historical validation |
| `POST /api/hermes/learning-cycle` | Manually trigger evaluation |
| `POST /api/notifications/test` | Telegram test |

Run types: `briefing`, `squad`, `wildcard`, `free_hit`, `triple_captain`,
`differentials`, `my_team` (needs `fpl_team_id`), `season_plan` (rolling
chip strategy; subsequent runs diff against the stored plan).

## Scheduled jobs
| Job | When | Purpose |
|---|---|---|
| Daily briefing | 03:30 UTC (opt-in) | `HERMES_DAILY_BRIEFING=true` |
| Learning cycle | 06:00 UTC | Evaluate finished GWs, update lessons |
| Telegram squad | 60 min before deadline | XI, captain, transfers + Hermes digest |
| (existing) snapshot/save | 00:00 UTC / 30 min pre-deadline | unchanged |

## Environment variables
```
HERMES_ENABLED=true
LLM_BASE_URL=https://api.deepseek.com/v1   # any OpenAI-compatible endpoint
LLM_MODEL=deepseek-chat
LLM_API_KEY=...
LLM_MAX_OUTPUT_TOKENS=2000   LLM_TIMEOUT_SECONDS=120
HERMES_TWO_PASS=false        HERMES_DAILY_BRIEFING=false
HERMES_VARIABILITY_POOL=120
SEARCH_PROVIDER=tavily       TAVILY_API_KEY=...
TELEGRAM_ENABLED=true        TELEGRAM_BOT_TOKEN=...  TELEGRAM_CHAT_ID=...
BETTING_ODDS_ENABLED=true    THE_ODDS_API_KEY=...
```
Everything degrades gracefully: with zero keys set the deterministic
agents, signals endpoint and all pre-existing features still work.

## Testing
```bash
python3 -m pytest backend/tests/ -v
```
Covers: variability math, DGW/BGW detection, schemas, news keywords,
LLM-output validation (hallucinated ids, clamping, truncated-JSON repair),
Telegram formatting, evaluation/trust math, backtest reconstruction.

## Database schema
Hermes adds three tables — `hermes_runs`, `season_archive`, `hermes_lessons`
(see `backend/database/models.py`). They are created by SQLAlchemy
`init_db()` / `create_all` on startup, which only *adds* missing tables and
never alters existing ones. There is no migration framework (e.g. Alembic)
in this project, so if a Hermes table's columns change later, that change
must be applied to existing databases by hand (or by dropping the affected
Hermes table and letting `create_all` recreate it — the data is
regenerable from runs/archives).
