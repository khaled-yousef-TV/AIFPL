# AIFPL backend

Transparent building blocks for an FPL decision-support agent. The project begins
with reproducible data ingestion, then adds projections, constrained optimisation,
and historical backtesting.

## Phases 1–2: FPL data snapshots and results

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

### Test locally

```bash
pytest
aifpl fetch-bootstrap
aifpl fetch-fixtures
aifpl fetch-event 1
aifpl latest-snapshot
 aifpl snapshot-before 2026-08-07T19:00:00Z
 aifpl import-season 2025-26 --end-gameweek 2
 aifpl backtest-baseline 2025-26 --start-gameweek 2 --end-gameweek 2
 aifpl validate-squad examples/valid_squad.json
 aifpl pick-lineup examples/valid_squad.json
 aifpl normalize-current-players
 aifpl current-players --limit 10
 aifpl build-current-projections
 aifpl current-projections --limit 10
 aifpl build-xg-xa-projections
 aifpl xg-xa-projections --limit 10
 export ODDS_API_KEY='your-key'
 aifpl fetch-epl-odds
 aifpl build-fixture-odds-consensus
 aifpl fixture-odds-consensus --limit 10
 aifpl build-odds-projections --start-gameweek 1 --end-gameweek 6
 aifpl optimize-current-squad
 aifpl plan-transfers examples/current_squad.json
 aifpl normalize-current-fixtures
 aifpl build-fixture-projections --start-gameweek 1 --end-gameweek 6
uvicorn aifpl.api:app --reload
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/snapshots/latest
curl 'http://127.0.0.1:8000/snapshots/as-of?at=2026-08-07T19:00:00Z'
curl http://127.0.0.1:8000/historical/seasons/2025-26
curl 'http://127.0.0.1:8000/players/current?limit=10'
curl 'http://127.0.0.1:8000/projections/current?limit=10'
curl 'http://127.0.0.1:8000/projections/xg-xa?limit=10'
curl http://127.0.0.1:8000/odds/epl/latest
curl http://127.0.0.1:8000/odds/epl/fixture-consensus
curl 'http://127.0.0.1:8000/projections/odds?start_gameweek=1&end_gameweek=6'
curl 'http://127.0.0.1:8000/squad/optimize/current?budget=1000'
curl 'http://127.0.0.1:8000/projections/fixtures?start_gameweek=1&end_gameweek=6'
```

Snapshot files default to `data/raw/fpl/bootstrap/`. Override that location with
`AIFPL_DATA_DIR=/path/to/data` when needed.

## Delivery plan

1. **Current:** source adapter, immutable raw snapshots, CLI/API, tests.
2. **Current:** fixture and completed-gameweek-result ingestion, deadline-aware
   snapshot selection.
3. **Current:** reproducible completed-season gameweek import and normalized
   player-gameweek records. This source is community-maintained and is never
   presented as an official pre-deadline snapshot.
4. **Current:** transparent rolling-average expected-points baseline and a
   leakage-safe outcome backtest.
5. FPL-rule validator and transfer/squad optimizer.
6. Decision-faithful backtest with pre-deadline features.
7. News and odds adapters, Hermes orchestration, then the frontend.

## Historical results source

The public FPL API only exposes the active season. For completed-season
**outcomes**, `import-season` reads individual gameweek CSV files from the
community-maintained [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League)
repository. Every downloaded CSV is retained locally alongside a SHA-256 hash
and its source URL. The normalized records are useful to score a model's
predictions, but are **not** historical pre-deadline intelligence; we will add
that separately before claiming a fully decision-faithful backtest.

## First projection/backtest

`backtest-baseline` is intentionally not an AI model. For each player and
target gameweek, it predicts points as the average of that player's prior
`--window` completed gameweeks (default: 5). It never reads the target
gameweek's results while making a prediction. The output contains every
prediction/actual pair and aggregate MAE, RMSE, bias, and coverage. It is an
honest baseline to beat before adding fixtures, player news, odds, or Hermes.

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
a projection: the first projection model is the next component.

## Hermes architecture

Hermes is the tool-calling strategist, not the source of FPL calculations. It
will call narrow backend tools for data retrieval, projections, rule validation,
plan comparison, and decision recording. The backend remains authoritative for
all numbers, FPL legality, and backtest evaluation. Hermes memory stores user
preferences and prior decision context; model/projection updates remain explicit,
versioned, and backtested.

## Current-player projection baseline

`build-current-projections` creates a transparent real-player baseline from
official FPL `points_per_game`, `form`, and availability probability. It is
versioned as `fpl_source_baseline_v1` and explicitly has no fixture, news, odds,
or expected-minutes adjustment. It exists to establish a testable real-player
interface; it is not the final forecasting model.

## FPL xG/xA projection baseline

`build-xg-xa-projections` retains official FPL xG, xA, xGI, xG conceded,
minutes, and starts. It creates a second comparison model,
`fpl_xg_xa_blend_v1`: 60% source baseline plus 40% an expected-minutes-adjusted
attacking-points estimate from xG/xA. It does not discard the original model,
and it does not yet estimate team clean-sheet probability.

## Betting odds

The Odds API key is read only from `ODDS_API_KEY`; it is never stored in this
repository. `fetch-epl-odds` retrieves the EPL `h2h` market from the UK region
and stores both raw data and quota headers. Each bookmaker's decimal odds are
converted to implied probabilities and normalized across home/draw/away outcomes
to remove that bookmaker's margin. Player props are event-specific and will be
added after FPL fixture-to-event matching.

## Fixture odds consensus

`build-fixture-odds-consensus` matches official FPL fixtures to odds events by
normalized home/away names and kickoff time (within five minutes). It averages
the already margin-adjusted bookmaker probabilities for each event, retaining
the bookmaker count and matching time delta. Unmatched fixtures are omitted;
they never receive invented odds.

## Odds-adjusted xG/xA projections

`build-odds-projections` combines the xG/xA baseline with the official fixture
difficulty multiplier and matched odds consensus. For each matched fixture, a
team's win probability adjusts the projection by `1 + 0.4 × (win_probability −
0.5)`. The adjustment is explicitly provisional and versioned; unmatched
fixtures receive no odds adjustment. This is not yet a clean-sheet or player-
prop model.

## Full-market squad optimization

`optimize-current-squad` uses an exact constraint solver over every player in
the current projection catalog. It maximizes total projected points subject to
the £100m budget, exact FPL position quotas, and the three-player club cap.
It considers all affordable candidates; it has no similarly-priced-player rule.

## Transfer planning

`plan-transfers` compares holding with every legal plan containing up to a
configured number of transfers. It maximizes projected squad points minus FPL
hit costs, while enforcing the money released by outgoing players plus the
bank. The current manual input uses current player prices as selling values; a
future authenticated FPL integration will replace those with each manager's
exact selling prices.

## Fixture-aware projections

`normalize-current-fixtures` preserves the official current-season fixture
schedule. `build-fixture-projections` applies a clearly stated temporary
difficulty multiplier to the source baseline for every player and gameweek:
difficulty 1/2/3/4/5 maps to 1.15/1.075/1.0/0.925/0.85. Multiple fixtures are
summed for double gameweeks and no fixture yields zero. This is the first
forward-looking layer; it will be superseded by calibrated fixture, minutes,
news, and odds models.

The backtest will never call today's API to answer a past-gameweek question;
each run will state its data cutoff and source coverage.
