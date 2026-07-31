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
- **A loaded squad renders as a full briefing.** Whenever a known 15 is in
  play — the connected My Team squad or the tracked squad — the view is the
  weekly-briefing shape: that squad on the pitch, transfer advice (including
  an explicit "hold" verdict), captaincy, and chip timing computed *for that
  squad*. No bespoke second layout. See Phase 0.
- **Every suggestion comes from a full Hermes run.** Transfers, the hold
  verdict, captaincy and chip dates are all fields of `HermesAdjustments`,
  produced by the LLM synthesis step in
  [orchestrator.py:110](backend/hermes/orchestrator.py:110) after all seven
  agents have reported. No shortcut path, no heuristic fallback that quietly
  answers instead — a run with no LLM is `degraded` and must be labelled as
  such in the UI, not passed off as advice. Concretely this bans: deriving
  transfers from raw `predicted_points` deltas, picking the captain as
  "highest xPts in the squad", or computing chip dates from a fixture-
  difficulty formula. Those are inputs the agents already supply *to*
  Hermes; they are not substitutes for it.
- **And all seven agents must actually see the squad.** Today only `data`,
  `availability` and `variability` read `ctx.user_player_ids` — `form`,
  `betting`, `news` and `mechanics` scope to global top-N, so a mid-owned
  player in your 15 can be invisible to the news search. Fixed in Phase 0.1;
  without it "Hermes analysed your squad" is only two-thirds true.

## New DB tables — DONE (uncommitted)

Both tables live in [models.py](backend/database/models.py:372); CRUD is in
[crud.py](backend/database/crud.py:1302). Two additions the original sketch
missed, both load-bearing:

- `tracked_squad_state.purchase_prices` — FPL selling price is the buy price
  plus half the rounded-down rise, so the buy price must be kept per player.
- `tracked_squad_gw.bench_points` / `autosubs` — needed to score bench boost
  and to show *why* a GW scored what it did.

`players` is stored in FPL pick order (XI first GK→FWD, then the 4 bench in
substitution order) because autosub scoring depends on that ordering.

[client.py:243](backend/fpl/client.py:243) also already has
`get_event_live_stats()` — same one API call as `get_event_live`, but keeps
`minutes` so autosubs and the captain/vice rule can be applied.

Nothing above is wired to anything yet.

## Phase 0 — the squad-shaped briefing (~1 day)

Prerequisite for Phase 1, and it fixes My Team on its own. Today `my_team` is
in `SQUAD_RUN_TYPES`, so [orchestrator.py:227](backend/hermes/orchestrator.py:227)
throws away your actual 15 and MILP-builds a fresh £100m squad for the pitch.
Your squad only reaches the LLM as `in_user_team` signals. So the one view
that should show *your* team shows a rebuild instead.

### 1. Squad awareness across all seven agents

`run_agents(ctx)` already runs the full registry — `data`, `mechanics`,
`availability`, `form`, `variability`, `betting`, `news` — on every run, and
`ctx.user_player_ids` is already populated for `my_team`
([orchestrator.py:97](backend/hermes/orchestrator.py:97)). The problem is
that four agents ignore it:

| agent | squad-aware today | change needed |
|---|---|---|
| `data` | yes — sets `in_user_team` | none |
| `availability` | yes — flags user players | none |
| `variability` | yes — extends the pool by the squad | none |
| `form` | no — global hot/cold lists | add a `squad_form` block: form delta for all 15, so a cold owned player surfaces even outside the global top-10 |
| `betting` | no — `compute_predictions()[:top_n]` | union the squad ids into the candidate pool before slicing |
| `news` | no — top-10 by form + flagged | union squad names into `top_names` so `build_search_queries` covers owned players; cap total queries to hold token cost |
| `mechanics` | no — global fixture/DGW context | add per-squad fixture counts (how many of your 15 blank/double in the horizon) — this is what makes chip projection concrete |

Each change is small and local to its agent, and each one is what makes the
corresponding *output* trustworthy: news coverage of your bench, odds edges
on your own attackers, and the blank/double counts the chip projection in
§4 depends on. Do these first — the LLM cannot reason about signals it was
never given.

Agent payloads are already trimmed per-agent in `assemble_user_prompt`
([prompts.py:117](backend/hermes/prompts.py:117)); the new squad blocks go
in as their own sections so they survive trimming rather than competing
with the global lists for space.

### 2. Render a known 15 on the pitch

New `assemble_fixed_squad(player_ids, predictions, gameweek)` in
`services/squad_service.py`: skips `build_optimal_squad`, feeds the given 15
straight to the existing `optimize_lineup()`, and returns the same result
shape `assemble_squad_result` does (XI, bench, formation, captain, vice,
value). The optimizer picks the best legal XI and bench order from the fixed
15 — which is exactly the lineup decision a manager makes.

In `_apply`, branch on run type: `my_team` (and `tracked`) with known player
ids → `assemble_fixed_squad`; everything else keeps the MILP path. Keep the
MILP output alongside it as `result["optimal_squad"]` — it becomes the
"what a free rebuild would look like" reference the wildcard advice needs.

### 3. Transfer verdict, including "hold"

Today an empty `transfer_priorities` renders as nothing, which reads as a
broken run rather than a recommendation. Rolling the transfer is a real and
common answer and has to be stated.

Add to `hermes/schemas.py`:

```python
class TransferPlan(BaseModel):
    recommendation: Literal["transfer", "hold"] = "hold"
    reason: str = ""
    # points the moves are expected to gain, net of any hit
    expected_gain: Optional[float] = None
    hit_cost: int = 0
```

Hang it off `HermesAdjustments` as `transfer_plan`, add the same object to
the JSON contract in [prompts.py:48](backend/hermes/prompts.py:48), and
extend the `my_team` instruction: state explicitly whether to transfer or
roll, and if rolling, say what it's being saved for. `validation.py` should
force `recommendation="hold"` when the transfer list comes back empty, so a
lazy run can't produce a contradiction.

### 4. Chip timing computed from *this* squad

`chip_advice.target_gameweeks` already exists but is generic — it isn't
conditioned on the squad you actually own, which is the only thing that
makes a bench-boost or triple-captain date meaningful.

Extend `ChipAdvice` with a per-chip projection:

```python
class ChipProjection(BaseModel):
    gameweek: Optional[int] = None
    confidence: Literal["low", "medium", "high"] = "low"
    reason: str = ""
    # true when the date only works if the suggested transfers happen first
    requires_transfers: bool = False
```

`chip_advice.projection: dict[str, ChipProjection]` — keys `wildcard`,
`free_hit`, `bench_boost`, `triple_captain`. `target_gameweeks` stays for
back-compat; populate both. The prompt for squad-loaded runs gets the squad
inline (it already does, via `in_user_team`) plus this instruction: project
each chip's best GW **for this specific 15**, and where the suggested
transfers change the answer, give the post-transfer date with
`requires_transfers: true`. Fixture-difficulty and DGW/BGW signals for the
horizon already reach the LLM through the existing agents.

### 5. Frontend

All in [ThisWeekTab.tsx](frontend/src/tabs/ThisWeekTab.tsx) — no new layout:

- `PitchView` needs no change; it just receives the real squad now.
- New `TransferVerdict` card above the priorities list: "Hold — roll to 2 FTs
  for the GW9 fixture swing", or "Transfer: Rice → Enzo, +3.2 pts net of the
  -4". Renders from `transfer_plan`, so a hold is a visible verdict.
- `ChipsPanel` ([:499](frontend/src/tabs/ThisWeekTab.tsx:499)) gains the
  projected GW + confidence per chip, with a "needs transfers first" marker
  when `requires_transfers` is set.
- **Provenance**: the run's `signals` block already carries every agent's
  report and is already rendered. Make sure the squad-loaded views keep it
  and that a `degraded` run (LLM unavailable → deterministic-only) says so
  in the verdict card rather than presenting optimizer output as advice.

### Definition of done for Phase 0

Running My Team on a connected squad shows *your* 15 on the pitch, an
explicit transfer-or-hold verdict, and four chip dates justified against
that squad — each traceable to agent signals that actually covered your
players. Spot-check: pick the lowest-owned player in the squad and confirm
he appears in the news, betting and form sections of the run.

## Phase 1 — persistent squad + auto-apply loop (~1 day)

Smallest complete loop: squad exists, Hermes advises, transfer commits.

### Backend

1. ~~**Migrations** in `backend/database/models.py`~~ — done, see above.
2. **`services/tracked_squad_service.py`** (new):
   - `seed_from_best_squad()` — reads the latest `squad` run, writes GW1
     row to `tracked_squad_state`. Idempotent; refuses if a state already
     exists for GW1+.
   - `current_state()` → dict.
   - `apply_transfer(hermes_run: HermesRun) -> TrackedSquadState` — a pure
     executor of what Hermes decided. It reads `transfer_plan` first: on
     `hold` it makes no moves and rolls the free transfer, full stop. On
     `transfer` it takes the `this_week` entries from
     `transfer_priorities` in order, computes the new bank, deducts the
     hit, advances free_transfers, writes the next GW's row. Captain comes
     from `adjustments.captain_ranking` — the first ranked player who is in
     the squad and not flagged by the availability agent.

     It must **never invent a move Hermes didn't recommend**. If the run is
     `degraded` (no LLM) or the recommended transfer is illegal (bank,
     3-per-club, formation), the correct behaviour is to hold and record
     why in `transfers_made`, not to fall back on a heuristic pick. A
     tracked squad that quietly makes optimizer-driven transfers stops
     measuring Hermes and starts measuring the MILP — which destroys the
     entire point of the benchmark.
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

   This must be a **fresh, full run with `force=True`**, not a reuse of the
   nightly briefing. `start_hermes_run` caches on `(run_type, gameweek,
   day)` ([hermes_service.py:59](backend/services/hermes_service.py:59)),
   and the nightly `my_team` run is keyed to the *user's real* squad — the
   tracked squad needs all seven agents re-run against its own 15, with its
   own `hermes_run_id` recorded on the ledger row so any GW's decision can
   be traced back to the exact agent reports that produced it. Budget the
   token cost accordingly: one extra full run per gameweek, not per day.
   The single-run-per-type lock ([:83](backend/services/hermes_service.py:83))
   means the auto-apply job can collide with a user-triggered My Team run —
   retry with backoff rather than skipping the gameweek.

### Frontend

The nav is no longer a sidebar of tabs — [App.tsx](frontend/src/App.tsx:14)
renders one `ThisWeekTab` whose `view` is a Hermes run type, hash-routed.
So the tracked squad is an **eighth view**, not a new tab component, and it
inherits the whole Phase 0 briefing layout for free.

1. **`HERMES_RUN_TYPES`** ([useHermes.ts:32](frontend/src/hooks/useHermes.ts:32))
   gains `{ value: 'tracked', label: 'Tracked Squad', shortLabel: 'Tracked' }`.
   It's a pseudo-run-type: the view reads `/api/tracked-squad`, and the run it
   displays is the `my_team` run the auto-apply job fired against the squad.
   `RUN_TYPE_IDS` picks up `#tracked` routing with no other change.
2. **Empty state**: no state row → "Seed from best squad" button, the same
   shape as the existing My Team setup gate
   ([:783](frontend/src/tabs/ThisWeekTab.tsx:783)).
3. **Loaded state**: exactly the Phase 0 briefing — tracked 15 on the pitch,
   transfer-or-hold verdict, captaincy, squad-conditional chip dates — with
   two additions:
   - A **next-GW plan strip** at the top: "GW3 locks in 2d 14:22. Planning:
     Rice → Enzo (-4), captain B.Fernandes." Reuses the masthead countdown
     already computed in [App.tsx:71](frontend/src/App.tsx:71).
   - The **season ledger** below the briefing: GW-by-GW points, captain,
     transfers, hits, cumulative total.
4. **API client** in `frontend/src/api/tracked-squad.ts`.

### Tests

- `test_squad_service.py`: `assemble_fixed_squad` returns the given 15
  unchanged, picks a legal formation, and never silently substitutes a
  player the MILP preferred.
- `test_tracked_squad_service.py`: seed / apply / score paths, transfer
  cost accounting, free-transfer roll-up (1 → 2 → capped at 2), chip
  scoring math, selling-price math off `purchase_prices`.
- `test_validation.py`: empty `transfer_priorities` forces
  `transfer_plan.recommendation == "hold"`.
- `test_agents.py`: with `user_player_ids` set, every squad player appears
  in the `form`, `betting` and `news` payloads — the regression guard for
  Phase 0.1. Parametrize over a squad containing a deliberately low-owned,
  low-form player, since that's the case the global top-N scoping drops.
- `test_tracked_squad_service.py` (cont.): a `degraded` run produces a hold,
  not a transfer; an illegal recommended transfer produces a hold with a
  recorded reason; `apply_transfer` never emits a move absent from
  `transfer_priorities`.
- Playwright: seed flow, ledger renders, hold verdict renders when Hermes
  recommends no transfer.

### Definition of done for Phase 1

- After GW1 finishes, `/api/tracked-squad` returns a GW1 row with
  `points_scored` populated, and a GW2 row in `tracked_squad_state`
  reflecting Hermes's applied transfer.
- `#tracked` shows the tracked 15 on the pitch with next-GW plan and
  ledger, and My Team shows the same briefing for the real squad.

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
2. **Season plan panel** on the tracked view: fixture windows to buy into
   and planned transfers over the next 5 GWs, from the latest `season_plan`
   run. Chip dates are *not* duplicated here — Phase 0's per-squad
   `chip_advice.projection` is the more specific answer and already renders
   in `ChipsPanel`. Where the two disagree, show the season plan's date as a
   muted "long-range" annotation under the projection.
3. **Deadline-relative chip triggers**: when the projection for a chip
   names the current GW with `confidence != "low"`, the auto-apply job sets
   `chip_active` for that GW's transfer round. Extend `apply_transfer` to
   honor `chip_advice.wildcard_now` / `free_hit_now` / `bench_boost_now`
   plus `projection[chip].gameweek == current_gw`, and to refuse a chip
   already in `chips_used`.

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
- **Token cost of squad-aware agents.** Widening `news` and `betting` to
  cover all 15 adds search queries and prompt tokens to every squad-loaded
  run, and Phase 1 adds a forced full run per gameweek on top. Cap the news
  agent's total queries rather than letting the squad union grow them
  unbounded, and keep the squad blocks compact (one line per player, the
  `render_players` format) so the prompt grows by tens of lines, not
  hundreds.
- **Degradation must be visible, never silent.** Hermes degrades to
  deterministic-only output whenever the LLM fails
  ([orchestrator.py:113](backend/hermes/orchestrator.py:113)). For a
  benchmark that claims to measure Hermes, a degraded GW is a hole in the
  data — surface it on the ledger row and exclude those GWs from any
  "Hermes vs template" claim, or the number silently becomes a mix of LLM
  and MILP performance.
- **Chip dates are the softest output in the whole system.** Asking the LLM
  for "best bench boost GW" invites it to invent a double gameweek that
  hasn't been announced. The projection must be grounded in the fixture
  signals the agents actually supply; `confidence: "low"` should be the
  default whenever no DGW/BGW is confirmed, and the UI should render low
  confidence as provisional rather than as a date.
- **Fixed-15 rendering can still surprise**: `optimize_lineup` picks the XI,
  so the pitch may bench a player the user starts. That's a feature (it's
  Hermes's lineup advice) but it must be labelled as such, not presented as
  a readback of the user's own team.
- **Reset on model change**: if the user switches LLM providers
  mid-season and wants to compare, they may want a second tracked
  squad. Punting to post-MVP; single-squad model is enough for now.

## Rough dependency graph

Within Phase 0, 0.1 (agent squad-awareness) comes before everything else —
the transfer verdict and chip projection are only as good as the signals
feeding them, and shipping the UI first would just render confident advice
built on a squad three of seven agents never looked at.

Phase 0 stands alone and ships value on its own (it fixes My Team). Phase 1
needs Phase 0's fixed-15 rendering — without it the tracked squad has no way
to display itself. Phase 2 requires Phase 1's scoring. Phase 3 requires
Phase 1's auto-apply for chip triggers. Phase 4 requires My Team connected
AND Phase 2's chart. Build in order.

## Files that will change

Already modified (uncommitted):
- `backend/database/models.py` — both tables
- `backend/database/crud.py` — CRUD for both tables
- `backend/fpl/client.py` — `get_event_live_stats()`

New:
- `backend/services/tracked_squad_service.py`
- `backend/api/routes/tracked_squad.py`
- `backend/tests/test_tracked_squad_service.py`
- `frontend/src/api/tracked-squad.ts`

Modified — Phase 0:
- `backend/agents/form_agent.py` — `squad_form` block
- `backend/agents/betting_agent.py` — union squad into the candidate pool
- `backend/agents/news_agent.py` — squad names in the search queries
- `backend/agents/mechanics_agent.py` — per-squad blank/double counts
- `backend/hermes/schemas.py` — `TransferPlan`, `ChipProjection`
- `backend/hermes/prompts.py` — JSON contract + squad-loaded instructions
- `backend/hermes/validation.py` — hold/transfer consistency
- `backend/hermes/orchestrator.py` — fixed-15 branch in `_apply`
- `backend/services/squad_service.py` — `assemble_fixed_squad()`
- `frontend/src/tabs/ThisWeekTab.tsx` — `TransferVerdict`, chip projections

Modified — Phase 1+:
- `backend/api/main.py` — two new scheduled jobs, register route
- `backend/services/hermes_service.py` — nightly sweep includes
  `season_plan` (Phase 3)
- `frontend/src/hooks/useHermes.ts` — `tracked` view entry
- `frontend/src/tabs/ThisWeekTab.tsx` — tracked empty state, plan strip,
  ledger
- `frontend/e2e/app.spec.ts` — Playwright coverage
