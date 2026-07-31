# Tracked Hermes Squad — implementation plan

A persistent 15-man squad that Hermes drives week-to-week: auto-applies its
recommended transfer each GW deadline, banks the actual points, and lets you
watch cumulative performance over the season. Meant as a benchmark of "pure
Hermes" strategy, comparable against the user's real FPL team and the
template.

## Design decisions locked in

- **Auto-apply mode is the default and only mode for the MVP.** The whole
  point is to measure pure Hermes performance without user interference.
  A manual "advisor" mode can come later behind a toggle if useful.
- **One tracked squad per install**, seeded from a Best Squad run at
  season start. No multi-squad support in the MVP — over-abstracts too
  early.
- **Follows FPL rules faithfully**: £100m start bank, 1 free transfer per
  GW (rolls up to 2), -4pts per extra transfer, chip usage.
- **All state in the existing SQLite/Postgres**, no new external services.

## New DB tables

Both indexed on `gameweek` for range queries.

```sql
CREATE TABLE tracked_squad_state (
    gameweek       INTEGER PRIMARY KEY,       -- state entering this GW
    players        TEXT NOT NULL,             -- json array of 15 player ids
    captain_id     INTEGER NOT NULL,
    vice_id        INTEGER,
    bank           REAL NOT NULL DEFAULT 0.0, -- millions
    free_transfers INTEGER NOT NULL DEFAULT 1,
    chips_used     TEXT NOT NULL DEFAULT '[]',-- json: ['wildcard', ...]
    chip_active    TEXT,                      -- 'wildcard'|'free_hit'|... for THIS GW
    created_at     TEXT NOT NULL
);

CREATE TABLE tracked_squad_gw (
    gameweek         INTEGER PRIMARY KEY,
    points_scored    INTEGER,                 -- null until GW finishes
    captain_points   INTEGER,
    transfer_cost    INTEGER NOT NULL DEFAULT 0,
    transfers_made   TEXT NOT NULL DEFAULT '[]', -- json: [{out_id,in_id}, ...]
    average_score    INTEGER,                 -- template baseline from FPL
    hermes_run_id    TEXT,                    -- which run drove this GW
    scored_at        TEXT
);
```

## Phase 1 — persistent squad + auto-apply loop (~1 day)

Smallest complete loop: squad exists, Hermes advises, transfer commits.

### Backend

1. **Migrations** in `backend/database/models.py` for the two tables above.
2. **`services/tracked_squad_service.py`** (new):
   - `seed_from_best_squad()` — reads the latest `squad` run, writes GW1
     row to `tracked_squad_state`. Idempotent; refuses if a state already
     exists for GW1+.
   - `current_state()` → dict.
   - `apply_transfer(hermes_run: HermesRun) -> TrackedSquadState` — reads
     `run.result.transfer_priorities`, picks the top one (or none),
     computes the new bank, deducts transfer cost, advances free_transfers,
     writes the next GW's row. Also updates captain from
     `run.result.squad.captain.id` if it's now in the tracked squad
     (otherwise pick the top predicted player in the squad — never leave
     captain unset).
   - `score_finished_gw(gameweek)` — pulls each squad player's actual
     points for that GW via the FPL element-summary endpoint, applies
     captain doubling, chip effects (bench boost sums bench, etc.),
     stores in `tracked_squad_gw`. Skips if `gameweek` isn't finished
     yet according to FPL's `events` data.
3. **API routes** in `backend/api/routes/tracked_squad.py`:
   - `GET /api/tracked-squad` → current state + history.
   - `POST /api/tracked-squad/seed` → run `seed_from_best_squad`.
   - `POST /api/tracked-squad/reset` → wipe both tables (dev use).
4. **Scheduler hook** in `backend/api/main.py`:
   - New job triggered ~30min AFTER each GW deadline: run a fresh
     `my_team` briefing with `user_player_ids=tracked squad`, then
     `apply_transfer(run)`. Deadline is already computed for the team
     save + Telegram jobs — reuse that pattern.
   - Second job at midnight after each GW's fixtures finish (Monday
     evening UK time typically): `score_finished_gw(next_finished_gw)`.
     Idempotent via the `scored_at` column.
5. **Include tracked squad in run context**: when the auto-apply job
   builds its `my_team` run, it needs `user_player_ids` from the tracked
   state. `services/hermes_service.start_hermes_run` already accepts
   `user_player_ids` — just pass them from the service.

### Frontend

1. **New tab in the sidebar: "Tracked Squad"** — third entry after
   Track Record. Route `#tracked`.
2. **`frontend/src/tabs/TrackedSquadTab.tsx`** (new): if no state exists,
   show "Seed from best squad" button; otherwise show:
   - Current squad on the pitch (reuse `PitchView` from ThisWeekTab).
   - Next-GW plan card: "GW3 will run at deadline − 30min. Currently
     planning to transfer Rice → Enzo (-4 hit), captain B.Fernandes."
     — pulled from the latest my_team run against the squad.
   - Season ledger (below): GW-by-GW table with points, captain,
     transfers, hits, cumulative total.
3. **API client** in `frontend/src/api/tracked-squad.ts`.

### Tests

- `test_tracked_squad_service.py`: seed / apply / score paths, transfer
  cost accounting, free-transfer roll-up (1 → 2 → capped at 2), chip
  scoring math.
- Playwright: seed flow, ledger renders.

### Definition of done for Phase 1

- After GW1 finishes, `/api/tracked-squad` returns a GW1 row with
  `points_scored` populated, and a GW2 row in `tracked_squad_state`
  reflecting Hermes's applied transfer.
- The Tracked Squad tab shows both.

## Phase 2 — season ledger + template comparison (~day)

1. **Baseline scoring**: FPL publishes `average_entry_score` per event
   in bootstrap. Store in the `average_score` column at scoring time.
2. **Cumulative points chart** in the ledger view: line for Hermes,
   dashed line for template average. Recharts (or a small inline SVG
   to avoid the dep).
3. **Summary strip**: total points, rank vs template ("+42 above
   average"), transfer efficiency (points gained per -4 hit taken).
4. **Ledger row details**: click a GW row to expand — shows that GW's
   full run: which agents flagged what, why Hermes made the transfer,
   the narrative extract.

## Phase 3 — surface planning ahead (~day)

The `season_plan` run type already exists and outputs a rolling
strategy over remaining GWs (chip target dates, fixture swings). It's
never been in the UI.

1. **Nightly sweep**: add `season_plan` to `NIGHTLY_RUN_TYPES`. Runs
   weekly rather than daily to save tokens — gate on
   `datetime.utcnow().weekday() == 0` (Mondays).
2. **Season plan panel** on the Tracked Squad tab: chip target GWs,
   fixture windows to buy into, planned transfers over the next 5 GWs.
   Pulled from the latest `season_plan` run.
3. **Deadline-relative chip triggers**: when the season plan says
   "wildcard GW9" and it's currently GW9, the auto-apply job should
   set `chip_active='wildcard'` for that GW's transfer round. Extend
   `apply_transfer` to honor `chip_advice.wildcard_now`, `free_hit_now`,
   `bench_boost_now` from the `my_team` run.

## Phase 4 — real-team comparison (optional, ~half day)

Requires My Team already connected.

1. **Second line on the cumulative chart**: fetch the user's real FPL
   entry's per-GW history via `entry/{id}/history/` and plot alongside
   Hermes. Refresh weekly after each GW settles.
2. **Head-to-head summary**: "Hermes is beating you by 87 points" (or
   losing). This is the punchline.

## Risk / open questions

- **FPL API rate limits under auto-apply**: the deadline+30 job hits the
  API for element histories to score. Add the same 6-hour cache used
  elsewhere; batch is small (15 players).
- **Chip math** (bench boost, triple captain) has edge cases with
  bench players who didn't play — must respect FPL's autosub rules
  when scoring. Reuse whatever autosub logic exists elsewhere in the
  codebase; if none, this is a good excuse to add a shared helper.
- **Off-season resilience**: after GW38 the tracked squad should just
  freeze until next season's seed. Both auto-apply and scoring jobs
  should no-op cleanly when there's no next GW.
- **What if Hermes suggests no transfer**: the `apply_transfer` function
  still advances the GW and increments free_transfers up to 2 — a
  common FPL pattern that should not be forgotten.
- **Reset on model change**: if the user switches LLM providers
  mid-season and wants to compare, they may want a second tracked
  squad. Punting to post-MVP; single-squad model is enough for now.

## Rough dependency graph

Phase 1 is standalone and complete. Phase 2 requires Phase 1's scoring.
Phase 3 requires Phase 1's auto-apply for chip triggers. Phase 4 requires
My Team to be connected AND Phase 2's chart. Build in order.

## Files that will change

New:
- `backend/services/tracked_squad_service.py`
- `backend/api/routes/tracked_squad.py`
- `backend/tests/test_tracked_squad_service.py`
- `frontend/src/tabs/TrackedSquadTab.tsx`
- `frontend/src/api/tracked-squad.ts`

Modified:
- `backend/database/models.py` — two new tables
- `backend/database/crud.py` — CRUD for the tables
- `backend/api/main.py` — two new scheduled jobs, register route
- `backend/services/hermes_service.py` — nightly sweep includes
  `season_plan` (Phase 3)
- `frontend/src/App.tsx` — third nav entry
- `frontend/e2e/app.spec.ts` — Playwright coverage
