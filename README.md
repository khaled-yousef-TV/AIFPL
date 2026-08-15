# AIFPL backend

An auditable autonomous FPL backend with reproducible ingestion, calibrated
forecast infrastructure, constrained optimisation, deadline scheduling, and a
tool-calling Hermes manager.

## Current implementation

The API does **not** make transfers or store FPL login credentials. It downloads
the public `bootstrap-static`, `fixtures`, and gameweek `event/{id}/live` data,
validates a small useful subset, and writes the complete raw payload to disk
with a retrieval timestamp.

### Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

### Render deployment

The repository includes `render.yaml` with two services:

- `aifpl-api`: FastAPI backend with a persistent `/var/data` disk.
- `aifpl-dashboard`: static dashboard that receives the API URL at build time.

The backend build command is `pip install '.[dev]'` so the API server includes Uvicorn. A one-line `requirements.txt` compatibility shim is also included for Render services created with the default `pip install -r requirements.txt` command.

Set these variables in Render:

Backend required:

- `AIFPL_ADMIN_API_KEY`: generate a value in Render; required for mutating routes.
- `ODDS_API_KEY`: The Odds API key used by refresh and odds projections.
- `HERMES_API_KEY`: DeepSeek/OpenAI-compatible model key.
- `AIFPL_CORS_ORIGINS`: the exact dashboard URL, for example `https://fplai.nl` (pre-filled by the Blueprint).

Backend optional:

- `AIFPL_TELEGRAM_ENABLED`, `AIFPL_TELEGRAM_BOT_TOKEN`, and `AIFPL_TELEGRAM_CHAT_ID`: enable Telegram deadline notifications.
- `HERMES_BASE_URL` and `HERMES_MODEL`: defaults are `https://api.deepseek.com` and `deepseek-v4-flash`.
- `AIFPL_HERMES_AUTO_RUN`: keep `false` until the scheduler workflow is configured.
- `AIFPL_FETCH_EVENT_MARKETS=true`: fetch team-total/clean-sheet and player-prop markets during refresh (one Odds API call per fixture event; uses quota). Clean-sheet probabilities then adjust GK/DEF projections. Leave unset locally to keep dev runs cheap.

Frontend required:

- `AIFPL_API_URL`: the public API URL, for example `https://api.fplai.nl` (pre-filled by the Blueprint).

`AIFPL_DATA_DIR=/var/data` is already configured in the Blueprint. The persistent disk matters because snapshots, projections, Hermes state, and scorecards are not disposable deployment files.

The backend start command is `sh scripts/render-start.sh`. It launches `scripts/render-bootstrap.sh` in the background before Uvicorn listens, so the API comes online immediately while first-run data is being fetched. On a new persistent disk the bootstrap imports the previous season's results (for transfer awareness), downloads current data, builds projections, and creates the first Hermes decision automatically; it skips the work on later deploys once a decision exists for the deployed commit. During pre-season, a legacy opening state is rebuilt once with the horizon optimizer without deleting its audit history; when the committed opening decision predates the deployed code, the bootstrap regenerates it (a pre-season-only `--force` reinitialization), so optimizer fixes reach the live squad automatically. Set `AIFPL_RENDER_BOOTSTRAP=false` if you prefer to initialize manually. The dashboard polls for a few minutes while first-run initialization completes.

If your existing Render service still uses a custom start command, set **Start Command** to `sh scripts/render-start.sh` or use its Render Shell once:

```bash
aifpl refresh-current-data --start-gameweek 1 --end-gameweek 6
aifpl hermes-run
```

The refresh command consumes `ODDS_API_KEY`; `hermes-run` consumes `HERMES_API_KEY`. Until these commands complete, `/dashboard/current` correctly reports that no committed dashboard state exists.

### Tests

```bash
pytest
```

The current suite contains **121 passing tests**. Commands below assume the
repository root and exercise network or persistent operations.

### Operations and smoke tests

```bash
aifpl check-source-health
aifpl latest-source-health
aifpl team-aliases
aifpl refresh-current-data --start-gameweek 1 --end-gameweek 1
aifpl latest-refresh-job
aifpl scheduler-status
aifpl run-scheduler-tick
# Long-running process for a service manager:
aifpl run-deadline-scheduler
aifpl fetch-bootstrap
aifpl fetch-fixtures
aifpl fetch-event 1
aifpl latest-snapshot
aifpl snapshot-before 2026-08-07T19:00:00Z
aifpl import-season 2025-26 --end-gameweek 2
aifpl backtest-baseline 2025-26 --start-gameweek 2 --end-gameweek 2 \
  --data-cutoff 2025-08-31T23:59:59Z
aifpl validate-squad examples/valid_squad.json
aifpl pick-lineup examples/valid_squad.json
aifpl normalize-current-players
aifpl normalize-current-fixtures
aifpl current-players --limit 10
aifpl build-current-projections
aifpl current-projections --limit 10
aifpl build-xg-xa-projections
aifpl xg-xa-projections --limit 10
aifpl build-fixture-projections --start-gameweek 1 --end-gameweek 6
aifpl fixture-projections --limit 10
export ODDS_API_KEY='your-key'
export AIFPL_ADMIN_API_KEY='your-admin-key'
export HERMES_API_KEY='your-deepseek-key'
aifpl fetch-epl-odds
aifpl latest-epl-odds --limit 10
aifpl build-fixture-odds-consensus
aifpl fixture-odds-consensus --limit 10
aifpl build-odds-projections --start-gameweek 1 --end-gameweek 6
aifpl odds-projections --limit 10
aifpl plan-horizon examples/current_squad.json
aifpl build-player-evidence
aifpl player-evidence --limit 20
aifpl fetch-event-markets
aifpl build-market-signals
aifpl hermes-run
aifpl hermes-reinitialize-opening-squad
aifpl hermes-state
aifpl hermes-decision
aifpl score-decisions
aifpl send-scorecard
aifpl notify-telegram
aifpl compare-backtests data/backtests/2025-26/RUN_A/predictions.jsonl \
  data/backtests/2025-26/RUN_B/predictions.jsonl
aifpl calibrate-backtest data/backtests/2025-26/RUN/predictions.jsonl \
  --train-end-gameweek 19 --evaluation-start-gameweek 20
aifpl optimize-current-squad --projection-source odds
aifpl plan-transfers examples/current_squad.json --projection-source odds
uvicorn aifpl.api:app --reload
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/health/sources/check -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY"
curl http://127.0.0.1:8000/health/sources
curl http://127.0.0.1:8000/config/team-aliases
curl -X POST http://127.0.0.1:8000/jobs/refresh/current \
  -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"start_gameweek":1,"end_gameweek":1,"budget":1000}'
curl http://127.0.0.1:8000/jobs/refresh/current/latest
curl http://127.0.0.1:8000/scheduler/status
curl -X POST http://127.0.0.1:8000/scheduler/tick -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY"
curl http://127.0.0.1:8000/snapshots/fpl/bootstrap/latest
curl 'http://127.0.0.1:8000/snapshots/fpl/bootstrap/as-of?at=2026-08-07T19:00:00Z'
curl http://127.0.0.1:8000/historical/seasons/2025-26
curl http://127.0.0.1:8000/teams/current
curl http://127.0.0.1:8000/teams/1/logo.png --output arsenal.png
curl 'http://127.0.0.1:8000/players/current?limit=10'
curl 'http://127.0.0.1:8000/fixtures/current?limit=10'
curl 'http://127.0.0.1:8000/projections/current?limit=10'
curl 'http://127.0.0.1:8000/projections/xg-xa?limit=10'
curl http://127.0.0.1:8000/odds/epl/latest
curl http://127.0.0.1:8000/odds/epl/fixture-consensus
curl 'http://127.0.0.1:8000/projections/odds?start_gameweek=1&end_gameweek=6'
curl 'http://127.0.0.1:8000/squad/optimize/current?budget=1000&projection_source=odds'
curl 'http://127.0.0.1:8000/projections/fixtures?start_gameweek=1&end_gameweek=6'
curl -X POST http://127.0.0.1:8000/catalogs/current/players -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY"
curl -X POST http://127.0.0.1:8000/catalogs/current/fixtures -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY"
curl -X POST http://127.0.0.1:8000/projection-catalogs/odds \
  -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"start_gameweek":1,"end_gameweek":6}'
curl -X POST http://127.0.0.1:8000/squad/validate \
  -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY" \
  -H 'Content-Type: application/json' --data @examples/valid_squad.json
curl -X POST http://127.0.0.1:8000/squad/lineup \
  -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY" \
  -H 'Content-Type: application/json' --data @examples/valid_squad.json
curl -X POST 'http://127.0.0.1:8000/transfers/plan?projection_source=odds' \
  -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY" \
  -H 'Content-Type: application/json' --data @examples/current_squad.json
curl -X POST 'http://127.0.0.1:8000/transfers/plan/horizon?pre_season=true&decision_hit_penalty=6' \
  -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY" \
  -H 'Content-Type: application/json' --data @examples/current_squad.json
curl -X POST http://127.0.0.1:8000/hermes/run \
  -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY"
curl http://127.0.0.1:8000/hermes/state
curl http://127.0.0.1:8000/hermes/decisions/latest
curl -X POST http://127.0.0.1:8000/evidence/players/build \
  -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY"
curl -X POST http://127.0.0.1:8000/odds/epl/event-markets \
  -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY"
curl -X POST http://127.0.0.1:8000/odds/epl/market-signals \
  -H "X-AIFPL-Admin-Key: $AIFPL_ADMIN_API_KEY"
```

The data root defaults to `data/`, relative to the process working directory.
Bootstrap, fixture, and event snapshots are stored under
`data/raw/fpl/bootstrap/`, `data/raw/fpl/fixtures/`, and
`data/raw/fpl/events/{event}/`. Override the root with
`AIFPL_DATA_DIR=/absolute/path/to/data` when needed.

Derived catalogs are also immutable. Each new JSONL artifact has a neighboring
`.manifest.json` containing its methodology, parameters, exact source paths,
record count, and SHA-256 hashes. Legacy artifacts remain readable but are not
silently assigned provenance that was never recorded.

Every mutating FastAPI route requires `X-AIFPL-Admin-Key`, matched against
`AIFPL_ADMIN_API_KEY`. Provider errors and persisted failure records redact
credentials. Catalog readers verify recorded artifact and source hashes before
use; odds builds additionally enforce fixture/consensus lineage. Fixture and
odds optimization can pin an exact catalog with `--catalog-id` or `catalog_id`.

`check-source-health` validates the latest bootstrap, fixture, current-event,
odds, configured event-market, and configured player-evidence artifacts and
persists explicit `healthy`, `stale`, `missing`,
`invalid`, or `not_applicable` results under `data/health/sources/`. Freshness
limits are configured with the `AIFPL_*_MAX_AGE_HOURS` variables shown in
`.env.example`; health failures are reported rather than silently ignored.

Official FPL, odds, and historical-source requests retry transient network
errors, HTTP 429 responses, and HTTP 5xx responses with bounded exponential
backoff. Permanent HTTP errors and invalid response schemas fail immediately.
`AIFPL_HTTP_RETRY_ATTEMPTS` and `AIFPL_HTTP_RETRY_BASE_SECONDS` configure the
policy.

`refresh-current-data` runs the complete source-to-recommendation cycle for a
requested gameweek range. It refreshes official and odds data, fetches current
event results when applicable, normalizes catalogs, builds every projection
layer, requires healthy sources, and produces an odds-based squad. Every run,
including a failed run, is persisted under `data/jobs/refresh/` with completed
steps, artifact paths, health state, recommendation, and error details.

The deadline scheduler reads the next official FPL `deadline_time` and runs the
same audited refresh job at the configured lead time. `run-scheduler-tick` is a
safe one-shot operation; `run-deadline-scheduler` polls continuously for use
under a service manager. A successful event is marked complete so later polls
cannot spend odds quota or create duplicate recommendations. Scheduler timing,
horizon, polling, and budget use the `AIFPL_SCHEDULER_*` variables in
`.env.example`.

## Delivery plan

### Completed

1. Official FPL ingestion: timestamped, overwrite-protected bootstrap, fixture, and
   gameweek-result snapshots.
2. Historical completed-season outcome import, normalized player-gameweek data,
   and a leakage-safe baseline backtest.
3. Real current-player catalog: FPL IDs, prices, availability, minutes, starts,
   xG, xA, xGI, and xG conceded.
4. Projection catalogs: source baseline, xG/xA v2, fixture-aware projections,
   and a versioned composite of expected minutes, availability evidence,
   fixture difficulty, match odds, and strict market signals.
5. FPL rules referee, exact full-market squad optimizer, and single-gameweek
   hold-versus-transfer planner.
6. The Odds API ingestion, margin-adjusted bookmaker probabilities, and
   auditable FPL-fixture-to-odds-event matching.
7. FastAPI and CLI operations for every component above, with domain,
   persistence, API-surface, and CLI-surface automated tests.
8. Operational data cycle: health and freshness records, configurable aliases,
   bounded retries, and audited manually runnable refresh jobs.
9. Deadline-aware scheduler: official deadline detection, configurable lead
   time and horizon, duplicate-safe execution, audited ticks, and a continuous loop.
10. Backend hardening: secret-safe errors, authenticated mutation routes,
    season/concurrency-safe jobs, verified artifact lineage and hashes, odds
    coverage gates, finished-fixture filtering, and leakage-safe kickoff ordering.
11. Horizon transfer optimizer: legal squads and lineups, captaincy, transfers,
    hits, five-transfer rollover, and bank use across contiguous 1-6 GW catalogs.
12. Calibration infrastructure: common-population model comparison and
    chronological train/evaluation calibration over immutable archived forecasts.
13. Strict event-market signals: opponent team-total clean-sheet probabilities,
    player-prop capture, de-vigging only for complete outcome sets, and coverage records.
14. Player evidence: official FPL news, availability, historical start rates,
    and optional timestamped predicted-lineup feeds with categorical source quality.
15. Autonomous Hermes manager: model-owned strategy, backend tool calls,
    season-aware squad state, tracked purchase prices, audited decisions, and scheduler execution.
16. Telegram deadline notifications: squad, transfer changes, captain, and
    provisional chip advice delivered to a configured chat before each official
    deadline with deduplicated scheduling.
17. Decision scoring and Hermes outcome feedback: completed-gameweek actuals are
    compared against each committed decision's projections (XI, captain, bench,
    and transfer deltas), persisted as audited scorecards, and the recent
    history plus season summary is fed back into Hermes' context so strategy
    changes are evidence-based rather than blind.
18. Corrected preseason transfer accounting: unlimited transfers apply only to
    forming the opening squad, GW2 starts with one free transfer, later weeks
    charge real hits, and repeated same-gameweek Hermes runs are idempotent.
19. Committed-plan provenance: each decision persists the projection catalog and
    full horizon economics (weekly transfers, free transfers before/after,
    unlimited flag, hits, bank, net points, odds coverage, robustness), and the
    dashboard renders that plan instead of reprojecting against newer catalogs.
20. Honest uncertainty presentation: per-gameweek odds coverage and evidence
    cutoff from the catalog manifest, an explicit `uncalibrated` label, and
    per-player expected minutes, start probability, availability, value, and
    differential score.
21. Explainability: computed per-move horizon gains and hit allocation, captain
    options ranked with safety (minutes, start probability) and upside
    (projection) side by side, and the committed plan included in the Telegram digest.
22. Risk-aware optimization: a real transfer-churn penalty (env-configurable and
    scaled by Hermes risk tolerance), an objective-consistent hold fallback, a
    0-100 robustness score, and the playing-bench floor applied to bench players only.
23. Structured late-return evidence: per-gameweek start probability and minutes
    multiplier for tournament returners applied across the whole horizon, plus
    ownership preserved for every projection source.

### Next

1. **Calibrated projection intervals:** replace the `uncalibrated` label with
   real confidence intervals once enough chronological holdout forecasts have
   accumulated to estimate per-player variance honestly.
2. **Effective ownership:** FPL exposes only raw `selected_by_percent`; true
   effective ownership (including captain/TC ownership) needs a data source
   before the differential metrics can be upgraded beyond the documented proxy.
3. **Authenticated account integration:** exact selling prices and real transfer
   execution in place of the current unauthenticated price assumptions.

## Frontend

The deployed static dashboard is `mockups/cockpit.html` (`index.html` redirects
to it; Render publishes the whole `mockups/` directory and overwrites
`config.js` with `AIFPL_API_URL` at build time). It is a single-page cockpit
with five views, all read-only against the backend:

- **Squad:** the committed XI pitch, bench, decision explanation, confidence,
  planned horizon, transfers with computed gains, captain options, scorecard,
  and squad state.
- **Evidence:** player evidence records (`/evidence/players`).
- **Hermes:** strategy, squad state, latest run transcript, and decision history
  (`/hermes/state`, `/hermes/runs/latest`, `/hermes/decisions`).
- **Scheduler:** next deadline, latest refresh job, and recent ticks with
  notification status (`/scheduler/status`, `/jobs/refresh/current/latest`,
  `/scheduler/ticks`).
- **Backtests:** baseline backtest runs with metrics and comparability
  (`/calibration/backtests`).

No backend change is needed to serve the frontend: it is static and talks to
the existing API. `AIFPL_CORS_ORIGINS` must include the cockpit origin. The
older `mockup_*.html` files are retained as design artifacts and are not part
of the live cockpit.

## Historical results source

The public FPL API only exposes the active season. For completed-season
**outcomes**, `import-season` reads individual gameweek CSV files from the
community-maintained [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League)
repository. Every downloaded CSV is retained locally alongside a SHA-256 hash
and its source URL. The normalized records are useful to score a model's
predictions, but are **not** historical pre-deadline intelligence; we will add
historical pre-deadline sources only as they are archived. Current calibration
does not claim decision-faithful evaluation where those inputs are absent.

## Baseline backtest

`backtest-baseline` is intentionally not an AI model. For each player and
target gameweek, it predicts points as the average of that player's prior
`--window` completed gameweeks (default: 5). It never reads the target
gameweek's results while making a prediction. The output contains every
prediction/actual pair and aggregate MAE, RMSE, bias, and coverage. It is an
honest benchmark for the implemented fixture, news, odds, and Hermes layers.

## FPL rules referee

`validate-squad` applies the core 15-player constraints: £100m budget (stored
as `1000` tenths of a million), 2 GK / 5 DEF / 5 MID / 3 FWD, and a maximum of
three players per club. `pick-lineup` tests every legal formation from that
squad and chooses the XI with the greatest sum of projected points, then assigns
captain and vice-captain from that XI. It does not make transfers.

## Current-season player pool

`normalize-current-players` converts a saved official FPL bootstrap snapshot
into a versioned catalog of real, current-season players. It preserves each
player's FPL ID, name, position, club, current price, availability status,
availability probability, form, and season points. This is a data catalog, not
a projection; the available projection models are described below.

## Hermes architecture

Hermes is the tool-calling strategist, not the source of FPL calculations. It
calls narrow backend tools for projections, validated squad adoption, horizon
planning, holding, and decision recording. Hermes creates its own risk tolerance,
hit aversion, differential appetite, planning horizon, and soft player preferences.
Those settings alter backend objective penalties and tie-breakers, but cannot
bypass FPL constraints or invent projections.

Hermes state and decisions are append-only, season/gameweek-bound, concurrency
locked, and include purchase prices for legal future selling values. The deadline
scheduler can run Hermes automatically with `AIFPL_HERMES_AUTO_RUN=true`; a failed
model call is retried independently without repeating the successful data refresh.
Each run's context includes `decision_history`: the most recent scored decisions
(predicted vs actual points, transfer deltas, captain outcomes) and a current-season
summary, so strategy adjustments respond to measured performance. Scorecards are
produced by `score-decisions` after each completed gameweek.

The model client uses the OpenAI-compatible chat-completions and tool-call format.
The default is `deepseek-v4-flash` at `https://api.deepseek.com`. Set
`HERMES_BASE_URL`, `HERMES_MODEL`, and `HERMES_API_KEY` to switch providers or
models without code changes. Hermes currently manages and audits its internal
team autonomously. Recommendations reach a configured Telegram chat before each
deadline; direct submission to a real FPL account is out of scope.

The first live `deepseek-v4-flash` run completed for season `2026-27`, created a
six-gameweek strategy, adopted a legal GW1 squad, selected B. Fernandes as
captain, and persisted versioned state and decision artifacts under
`data/hermes/`. The initial squad is horizon-aware: it aggregates the strategy's
planning-horizon projection catalog (GW1-N) and optimizes the legal squad with
the exact solver, so GW1 ownership is chosen for the coming weeks rather than
one gameweek of greed. Backend-derived formation and projected-points fields
remain authoritative when model prose is imprecise.

Each committed decision records the projection catalog used plus a snapshot of
the full horizon plan: per-gameweek transfers, free transfers before, hits,
bank, gross/net points, odds coverage, captain, and squad IDs. The dashboard
renders that persisted plan instead of recomputing it against newer catalogs,
so committed economics stay auditable and drift-free.

The dashboard reports projection confidence honestly rather than as a binary
"catalog loaded" signal: per-gameweek odds coverage and status from the catalog
manifest, evidence cutoff/age when lineup evidence was consumed, and an explicit
`uncalibrated` label until chronological holdout calibration produces intervals.
Player rows expose expected minutes, start probability, and availability
multiplier when the projection catalog carries them.

Each planned transfer is rendered with its computed explanation against the
pinned catalog: horizon projected gain (in minus out across the planned
gameweeks), allocated hit cost, net gain, per-gameweek odds coverage, and
ownership change. The captain box ranks the squad's top projected captain
options from the committed catalog and annotates each with expected minutes and
start probability, so safety (minutes) and upside (projection) are visible side
by side instead of presenting a single unqualified pick.

Player rows also expose a transparent `value` (projected points per £m) and a
`differential_score` (projected points times one minus ownership) so the
interface distinguishes price efficiency and under-ownership from raw
projections. Ownership is preserved for every projection source: xG/xA and
fixture catalogs fill `selected_by_percent` from the latest player catalog when
their rows do not carry it, so the differential tie-break is never silently
zeroed for non-odds sources.

## Current-player projection baseline

`build-current-projections` creates a transparent real-player baseline from
official FPL `points_per_game`, `form`, and availability probability. It is
versioned as `fpl_source_baseline_v1` and explicitly has no fixture, news, odds,
or expected-minutes adjustment. It exists to establish a testable real-player
interface; it is not the final forecasting model.

## FPL xG/xA projection baseline

`build-xg-xa-projections` retains official FPL xG, xA, xGI, xG conceded,
minutes, and starts. Expected starts are scaled against elapsed opportunities,
not the full 38-gameweek season, and source points-per-game is also scaled by
expected participation. It creates a second comparison model,
`fpl_xg_xa_blend_v2`: 60% source baseline plus 40% an expected-minutes-adjusted
attacking-points estimate from xG/xA. It does not discard the original model,
while the composite odds model adds strict market-derived clean-sheet signals
when complete team-total markets are available.

## Betting odds

The Odds API key is read only from `ODDS_API_KEY`; it is never stored in this
repository. `fetch-epl-odds` retrieves the EPL `h2h` market from the UK region
and stores both raw data and quota headers. Each bookmaker's decimal odds are
converted to implied probabilities and normalized across home/draw/away outcomes
to remove that bookmaker's margin. Event-specific team totals, anytime-scorer,
and assist markets are also supported. Complete over/under pairs are de-vigged;
one-sided scorer prices remain evidence rather than invented probabilities.

## Fixture odds consensus

`build-fixture-odds-consensus` matches official FPL fixtures to odds events by
normalized home/away names and kickoff time (within five minutes). It averages
the already margin-adjusted bookmaker probabilities for each event, retaining
the bookmaker count and matching time delta. Unmatched fixtures are omitted;
they never receive invented odds.

Built-in aliases cover common EPL naming differences. Optional overrides are
loaded from `data/config/team_aliases.json`, or from the path set in
`AIFPL_TEAM_ALIASES_FILE`. The file must be a JSON object mapping source names
to canonical names; `config/team_aliases.example.json` shows the format.
`team-aliases` validates and displays the effective mapping. Every consensus
manifest records that mapping and hashes the override file when one is used.

## Odds-adjusted xG/xA projections

`build-odds-projections` combines the xG/xA baseline with the official fixture
difficulty multiplier and matched odds consensus. For each matched fixture, a
team's win probability adjusts the projection by `1 + 0.4 × (win_probability −
0.5)`. The adjustment is explicitly provisional and versioned; unmatched
fixtures receive no odds adjustment. When strict event-market signals exist,
the model adds clean-sheet expectation and records complete assist probabilities.

## Calibration and evidence

Calibration uses immutable forecast rows only. Training gameweeks must precede
evaluation gameweeks, and model comparisons use the common player-gameweek
population. Existing outcome-only history can calibrate the rolling baseline;
advanced xG, odds, clean-sheet, and lineup models can only be evaluated after
their own pre-deadline forecasts have accumulated. The backend never recreates
historical forecasts from today's API.

`build-player-evidence` preserves official FPL news/status and historical start
rates. Optional structured feeds come from `AIFPL_PLAYER_EVIDENCE_FILE` or
`AIFPL_PLAYER_EVIDENCE_URLS`; records require exact FPL IDs, publication times,
source classes, and explicit provider probabilities. `config/player_evidence.example.json`
documents the schema. Unscored text and ambiguous names never become projection
probabilities.

`fetch-event-markets` requests EPL `team_totals`, anytime-scorer, and assist
markets one event at a time. Complete over/under pairs are de-vigged; one-sided
scorer prices remain evidence only. Opponent under-0.5 team totals contribute a
versioned clean-sheet component. Player-prop projection weight defaults to zero
until chronological holdout calibration supports a non-zero `AIFPL_PLAYER_PROP_WEIGHT`.
Automatic event-market fetching is quota-sensitive and remains disabled unless
`AIFPL_FETCH_EVENT_MARKETS=true`.

External evidence can also carry `late_return` records for players returning
late from tournaments: a per-gameweek start probability plus an optional
`minutes_multiplier` (within 0..1.5). These adjustments apply across the whole
planning horizon, not just the opening gameweek, so World Cup returners are
scored as `Base × P(Start) × MinutesFactor` for every affected gameweek until
their own evidence expires or newer sources replace it.

## Full-market squad optimization

`optimize-current-squad` uses an exact constraint solver over every player in
the selected projection catalog. Use `--projection-source current`, `xg-xa`,
`fixture`, or `odds`; fixture and odds rows are summed across their stored
gameweek horizon. It maximizes total projected points subject to
the £100m budget, exact FPL position quotas, and the three-player club cap. The
objective is a legal XI plus the captain's second score; bench players satisfy
squad legality but do not incorrectly count as guaranteed gameweek points.
It considers all affordable candidates; it has no similarly-priced-player rule.

## Transfer planning

`plan-transfers` compares holding with every legal plan containing up to a
configured number of transfers. For each plan it jointly selects a legal XI and
captain, then maximizes XI points plus the captain's second score minus FPL hit
costs. It accepts the same `--projection-source` values as squad optimization
and enforces the money released by outgoing players plus the bank. The current
manual input uses current player prices as selling values; a
future authenticated FPL integration will replace those with each manager's
exact selling prices.

`plan-horizon` extends that calculation across a contiguous 1-6 gameweek odds
catalog. It jointly chooses each week's legal squad, XI, captain, transfers,
hits, free-transfer rollover up to five, and bank balance. The solver is seeded
with a legal hold strategy and uses `AIFPL_HORIZON_SOLVER_MAX_SECONDS`; the
result reports `OPTIMAL` when proven or `FEASIBLE` when it is the best plan
found before the configured limit. Each planned transfer also costs
`AIFPL_TRANSFER_PENALTY` projected points (default 1.0) so needless churn is
discouraged without blocking worthwhile moves; Hermes scales that baseline by
its own risk tolerance (`+ 2 × (1 − risk_tolerance)`) so cautious strategies
churn less, and the API/CLI accept a `churn_penalty` override. The hold
fallback compares plans with the same full objective (hits, churn, bank
shortfall, and dead-bench penalties) so it only fires when holding genuinely
beats the optimizer. Every week reports a `robustness_score` (0-100) combining
expected minutes, bench strength, bank flexibility, rotation risk, and planned
transfers. As with the single-week planner, unauthenticated inputs use current
prices as selling values until account integration is added.

## Fixture-aware projections

`normalize-current-fixtures` preserves the official current-season fixture
schedule. `build-fixture-projections` applies a clearly stated temporary
difficulty multiplier to the source baseline for every player and gameweek:
difficulty 1/2/3/4/5 maps to 1.15/1.075/1.0/0.925/0.85. Multiple fixtures are
summed for double gameweeks and no fixture yields zero. This layer remains
available independently; the composite odds model adds expected minutes,
availability evidence, match odds, and strict market signals over it.

The backtest never calls today's API to answer a past-gameweek question. Each
run requires `--data-cutoff`, excludes fixtures with later kickoff times, and
persists the cutoff, exact source files, hashes, model parameters, coverage,
and metrics in a run manifest. This is an event-time cutoff, not proof of when
the retrospective community source first published each row.
