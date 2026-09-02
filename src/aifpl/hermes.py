from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock

import httpx
from pydantic import BaseModel, Field

from aifpl.artifacts import json_bytes, sha256_path, write_immutable
from aifpl.config import HermesSettings, hermes_settings
from aifpl.health import SourceHealthChecker
from aifpl.horizon_transfers import (
    HorizonGameweekPlan,
    HorizonSquadState,
    HorizonTransferPlan,
    PLANNER_VERSION,
    plan_horizon_transfers,
    plan_hold_horizon_transfers,
)
from aifpl.live_calibration import calibrated_odds_catalog
from aifpl.odds_projections import OddsProjectionStore
from aifpl.optimizer import OptimizedSquad
from aifpl.player_evidence import PlayerEvidenceStore
from aifpl.current_projections import CurrentPlayerProjection
from aifpl.rules import SquadPlayer, SquadRequest, select_best_lineup
from aifpl.retry import retry_sync
from aifpl.security import redact_secrets


class HermesModel(Protocol):
    model_name: str

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]: ...


class HermesStrategy(BaseModel):
    risk_tolerance: float = Field(ge=0, le=1)
    hit_aversion: float = Field(ge=0, le=1)
    differential_appetite: float = Field(ge=0, le=1)
    planning_horizon: int = Field(ge=3, le=6)
    preferred_players: list[str] = Field(default_factory=list)
    rationale: str


class HermesSquadState(BaseModel):
    player_ids: list[int] = Field(min_length=15, max_length=15)
    bank: int = Field(ge=0)
    free_transfers: int = Field(ge=0, le=5)
    purchase_prices: dict[int, int] = Field(default_factory=dict)


class HermesState(BaseModel):
    strategy: HermesStrategy
    squad: HermesSquadState
    captain_id: int
    starting_xi_ids: list[int]
    model: str
    updated_at: datetime
    version: int
    gameweek: int = 0
    decision_path: str = ""
    season_id: str = ""
    initialization_method: str = ""
    supersedes_state_path: str | None = None
    vice_captain_id: int | None = None
    bench_ids: list[int] = Field(default_factory=list)
    active_chip: str | None = None
    active_chip_set: Literal[1, 2] | None = None


class HermesDecision(BaseModel):
    action: str
    gameweek: int
    squad: HermesSquadState
    captain_id: int
    starting_xi_ids: list[int]
    transfers_out: list[int]
    transfers_in: list[int]
    explanation: str
    strategy: HermesStrategy
    model: str
    created_at: datetime
    backend_methodology: str
    decision_path: str
    state_path: str = ""
    season_id: str = ""
    horizon_plan: HorizonPlanSnapshot | None = None
    base_state_path: str | None = None
    supersedes_decision_path: str | None = None
    correction_reason: str | None = None
    vice_captain_id: int | None = None
    bench_ids: list[int] = Field(default_factory=list)
    active_chip: str | None = None
    active_chip_set: Literal[1, 2] | None = None


class HermesRunResult(BaseModel):
    decision: HermesDecision
    state_path: str
    tool_steps: int


class HermesSupersessionResult(BaseModel):
    decision: HermesDecision
    state_path: str
    correction_path: str


class HermesRunTranscript(BaseModel):
    created_at: datetime
    gameweek: int | None = None
    season_id: str | None = None
    outcome: Literal["succeeded", "failed", "unknown"] = "unknown"
    decision_path: str | None = None
    error: str | None = None
    tool_steps: int = 0
    messages: list[dict[str, Any]] = Field(default_factory=list)


class HorizonPlanWeekSnapshot(BaseModel):
    gameweek: int
    transfers_made: int
    free_transfers_before: int
    hit_cost: int
    bank_after: int
    projected_points: float
    net_projected_points: float
    odds_coverage: float
    robustness_score: float = 0.0
    unlimited_transfers: bool = False
    free_transfers_after: int | None = None
    outgoing_ids: list[int] = Field(default_factory=list)
    incoming_ids: list[int] = Field(default_factory=list)
    captain_id: int | None = None
    vice_captain_id: int | None = None
    starting_xi_ids: list[int] = Field(default_factory=list)
    squad_ids: list[int] = Field(default_factory=list)
    bank_before: int = 0
    purchase_value: int = 0
    sale_value: int = 0
    objective_net_points: float = 0.0
    objective_components: dict[str, float] = Field(default_factory=dict)


class HorizonPlanSnapshot(BaseModel):
    projection_catalog: str = ""
    pre_season: bool = False
    solver_status: str = ""
    methodology: str = ""
    planner_version: str = ""
    total_projected_points: float = 0.0
    total_hit_cost: int = 0
    total_net_projected_points: float = 0.0
    robustness_score: float = 0.0
    objective_value: float = 0.0
    objective_components: dict[str, float] = Field(default_factory=dict)
    weeks: list[HorizonPlanWeekSnapshot] = Field(default_factory=list)


class OpenAICompatibleHermesModel:
    def __init__(self, settings: HermesSettings | None = None) -> None:
        self.settings = settings or hermes_settings()
        self.model_name = self.settings.model

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        def request() -> httpx.Response:
            response = httpx.post(
                f"{self.settings.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"},
                json={"model": self.settings.model, "messages": messages, "tools": tools, "tool_choice": "auto", "temperature": 0.2},
                timeout=self.settings.timeout_seconds,
            )
            response.raise_for_status()
            return response

        try:
            payload = retry_sync(request, __import__("aifpl.config", fromlist=["http_retry_settings"]).http_retry_settings()).json()
            return payload["choices"][0]["message"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise RuntimeError(redact_secrets(f"Hermes model request failed: {type(exc).__name__}")) from exc


class HermesDecisionBackend:
    def __init__(self, root: Path) -> None:
        self.root = root

    def context(self) -> dict[str, Any]:
        try:
            health = SourceHealthChecker(self.root).latest().model_dump(mode="json")
        except FileNotFoundError:
            health = {"overall_status": "unknown"}
        try:
            evidence_count = len(PlayerEvidenceStore(self.root).latest())
        except FileNotFoundError:
            evidence_count = 0
        return {
            "source_health": health,
            "player_evidence_records": evidence_count,
            "decision_history": self._decision_history(),
            "odds_projection_coverage": self._odds_coverage(),
            "chip_advice": self._chip_advice(),
        }

    def _chip_advice(self) -> dict[str, object] | None:
        try:
            from aifpl.chips import ChipAdviceStore

            advice = ChipAdviceStore(self.root).latest()
        except FileNotFoundError:
            return None
        return {
            "gameweek": advice.gameweek,
            "recommendations": [
                {
                    "chip": item.chip, "set": item.set, "status": item.status,
                    "gameweek": item.gameweek, "rationale": item.rationale,
                    "confidence": item.confidence,
                }
                for item in advice.recommendations
            ],
            "intel_stale": advice.intel.stale,
        }

    def _odds_coverage(self) -> dict[str, Any]:
        try:
            from aifpl.odds_projections import OddsProjectionStore

            catalog_id = self._latest_catalog_id(1, 38)
            path = self.root / "normalized" / "current" / "odds_projections" / catalog_id
            manifest = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
            parameters = manifest.get("parameters", {})
            return {
                "status": parameters.get("odds_coverage_status", "unknown"),
                "by_gameweek": parameters.get("odds_coverage_by_gameweek"),
            }
        except (FileNotFoundError, ValueError):
            return {"status": "unknown", "by_gameweek": None}

    def _decision_history(self) -> dict[str, Any]:
        try:
            from aifpl.scoring import DecisionScorer

            records_by_gameweek: dict[tuple[str, int], Any] = {}
            for record in DecisionScorer(self.root).recent(100):
                key = (record.season_id, record.gameweek)
                current = records_by_gameweek.get(key)
                if current is None or _prefer_scorecard(record, current):
                    records_by_gameweek[key] = record
            records = sorted(
                records_by_gameweek.values(),
                key=lambda record: record.scoring_at,
                reverse=True,
            )[:5]
        except FileNotFoundError:
            return {"rows": [], "summary": None}
        rows = [
            {
                "gameweek": record.gameweek, "season_id": record.season_id, "action": record.action,
                "projected": record.total_projected, "actual": record.total_actual,
                "xi_actual": record.xi_actual, "bench_actual": record.bench_actual,
                "transfer_delta": round(sum(transfer.delta for transfer in record.transfers), 4),
                "captain_actual": record.captain.actual if record.captain else None,
            }
            for record in records
        ]
        current_season = rows[0]["season_id"] if rows else None
        season_rows = [row for row in rows if row["season_id"] == current_season]
        summary = None
        if season_rows:
            deltas = [row["actual"] - row["projected"] for row in season_rows]
            summary = {
                "scored_gameweeks": len(season_rows),
                "avg_actual_minus_projected": round(sum(deltas) / len(deltas), 4),
                "total_transfer_delta": round(sum(row["transfer_delta"] for row in season_rows), 4),
                "avg_captain_actual": round(sum(row["captain_actual"] or 0 for row in season_rows) / len(season_rows), 4),
            }
        return {"rows": rows, "summary": summary}

    def initial_squad(self, strategy: HermesStrategy) -> tuple[OptimizedSquad, int, HorizonPlanSnapshot]:
        rows, catalog_id = self._horizon_rows(1, strategy.planning_horizon)
        preferred = {row.player_id for row in rows if row.player_name.casefold() in {name.casefold() for name in strategy.preferred_players}}
        plan = plan_horizon_transfers(
            rows, HorizonSquadState(player_ids=[], bank=0, free_transfers=0),
            preferred_player_ids=preferred,
            differential_appetite=strategy.differential_appetite,
            pre_season=True,
            churn_penalty=_strategy_churn_penalty(strategy),
        )
        opening = plan.gameweeks[0]
        total_cost = sum(player.cost for player in opening.resulting_squad)
        # Keep the existing initial-decision contract while deriving it from the horizon plan.
        return OptimizedSquad(
            players=opening.resulting_squad, total_cost=total_cost, bank=opening.bank_after,
            projected_points=opening.projected_points, budget=total_cost + opening.bank_after,
            solver_status=plan.solver_status, methodology=plan.methodology,
            starting_xi=opening.starting_xi, captain=opening.captain,
        ), opening.gameweek, _plan_snapshot(plan, catalog_id, pre_season=True)

    def horizon_plan(self, state: HermesSquadState, strategy: HermesStrategy, target_gameweek: int) -> tuple[HorizonTransferPlan, str]:
        rows, catalog_id = self._horizon_rows(target_gameweek, strategy.planning_horizon)
        preferred = {row.player_id for row in rows if row.player_name.casefold() in {name.casefold() for name in strategy.preferred_players}}
        pre_season = target_gameweek == 1
        return plan_horizon_transfers(rows, HorizonSquadState(
            player_ids=state.player_ids, bank=state.bank, free_transfers=state.free_transfers,
            purchase_prices=state.purchase_prices,
        ), decision_hit_penalty=4 + strategy.hit_aversion * 4 + (1 - strategy.risk_tolerance) * 2,
            preferred_player_ids=preferred, differential_appetite=strategy.differential_appetite,
             pre_season=pre_season, churn_penalty=_strategy_churn_penalty(strategy)), catalog_id

    def hold_horizon_plan(self, state: HermesSquadState, strategy: HermesStrategy, target_gameweek: int) -> tuple[HorizonTransferPlan, str]:
        rows, catalog_id = self._horizon_rows(target_gameweek, strategy.planning_horizon)
        preferred = {row.player_id for row in rows if row.player_name.casefold() in {name.casefold() for name in strategy.preferred_players}}
        pre_season = target_gameweek == 1
        return plan_hold_horizon_transfers(
            rows,
            HorizonSquadState(
                player_ids=state.player_ids, bank=state.bank,
                free_transfers=state.free_transfers, purchase_prices=state.purchase_prices,
            ),
            decision_hit_penalty=4 + strategy.hit_aversion * 4 + (1 - strategy.risk_tolerance) * 2,
            preferred_player_ids=preferred,
            differential_appetite=strategy.differential_appetite,
            pre_season=pre_season,
            churn_penalty=_strategy_churn_penalty(strategy),
        ), catalog_id

    def hold_week(self, state: HermesSquadState, horizon: int, target_gameweek: int) -> dict[str, Any]:
        rows, _ = self._horizon_rows(target_gameweek, horizon)
        gameweek = target_gameweek
        by_id = {row.player_id: row for row in rows if row.gameweek == gameweek and row.player_id in state.player_ids}
        players = [CurrentPlayerProjection(row.player_id, row.player_name, row.position, row.club, row.cost, row.projected_points, 1.0, row.methodology) for row in by_id.values()]
        lineup = select_best_lineup(SquadRequest(players=[SquadPlayer(id=p.player_id, name=p.player_name, position=p.position, club=p.club, cost=p.cost, projected_points=p.projected_points) for p in players], budget=sum(p.cost for p in players) + state.bank))
        starters = [player for player in players if player.player_id in {p.id for p in lineup.starters}]
        vice_captain = max(
            (player for player in starters if player.player_id != lineup.captain.id),
            key=lambda player: player.projected_points,
            default=None,
        )
        return {"gameweek": gameweek, "starting_ids": [p.id for p in lineup.starters], "captain_id": lineup.captain.id,
                "vice_captain_id": vice_captain.player_id if vice_captain is not None else None,
                "projected_points": lineup.projected_points, "methodology": players[0].methodology,
                "formation": f"{sum(p.position == 'DEF' for p in lineup.starters)}-{sum(p.position == 'MID' for p in lineup.starters)}-{sum(p.position == 'FWD' for p in lineup.starters)}"}

    def _latest_catalog_id(self, minimum_gameweeks: int, maximum_gameweeks: int) -> str:
        directory = self.root / "normalized" / "current" / "odds_projections"
        candidates: list[tuple[datetime, Path]] = []
        for path in directory.glob("*.jsonl"):
            manifest = path.with_suffix(".manifest.json")
            if manifest.exists() and OddsProjectionStore._is_raw_catalog(path):
                candidates.append((datetime.fromisoformat(json.loads(manifest.read_text(encoding="utf-8"))["created_at"]), path))
        for _, path in sorted(candidates, reverse=True):
            try:
                rows = OddsProjectionStore(self.root).latest(path.name)
            except ValueError:
                continue
            count = len({row.gameweek for row in rows})
            if minimum_gameweeks <= count <= maximum_gameweeks:
                return path.name
        raise FileNotFoundError(f"No odds projection catalog covers {minimum_gameweeks}-{maximum_gameweeks} gameweeks")

    def _horizon_rows(self, target_gameweek: int, horizon: int) -> tuple[list[OddsAdjustedGameweekProjection], str]:
        directory = self.root / "normalized" / "current" / "odds_projections"
        candidates: list[tuple[datetime, Path]] = []
        for path in directory.glob("*.jsonl"):
            manifest = path.with_suffix(".manifest.json")
            if manifest.exists() and OddsProjectionStore._is_raw_catalog(path):
                candidates.append((datetime.fromisoformat(json.loads(manifest.read_text(encoding="utf-8"))["created_at"]), path))
        for _, path in sorted(candidates, reverse=True):
            calibrated = calibrated_odds_catalog(self.root, path.name)
            available = sorted({row.gameweek for row in calibrated.rows})
            if target_gameweek in available:
                selected = set(range(target_gameweek, min(max(available), target_gameweek + horizon - 1) + 1))
                return [row for row in calibrated.rows if row.gameweek in selected], calibrated.catalog_id
        raise FileNotFoundError(f"No odds projection catalog contains gameweek {target_gameweek}")


class HermesManager:
    def __init__(self, root: Path, model: HermesModel | None = None, backend: HermesDecisionBackend | None = None) -> None:
        self.root = root
        self.model = model
        self.backend = backend or HermesDecisionBackend(root)
        self._strategy: HermesStrategy | None = None
        self._initial: OptimizedSquad | None = None
        self._initial_snapshot: HorizonPlanSnapshot | None = None
        self._horizon: HorizonTransferPlan | None = None
        self._catalog_id: str | None = None
        self._hold: dict[str, Any] | None = None
        self._hold_horizon: HorizonTransferPlan | None = None
        self._initial_gameweek: int | None = None
        self._expected_gameweek: int | None = None
        self._expected_season_id: str | None = None
        self._deadline: datetime | None = None
        self._deadline_clock: datetime | None = None

    def run(
        self,
        expected_gameweek: int | None = None,
        expected_season_id: str | None = None,
        deadline: datetime | None = None,
        deadline_clock: datetime | None = None,
    ) -> HermesRunResult:
        self._expected_gameweek = expected_gameweek
        self._expected_season_id = expected_season_id
        self._deadline = deadline
        self._deadline_clock = deadline_clock
        lock_path = self.root / "hermes" / "run.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                flock(descriptor, LOCK_EX | LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another Hermes run is already in progress") from exc
            return self._run_unlocked()
        finally:
            flock(descriptor, LOCK_UN)
            os.close(descriptor)

    def run_current(self) -> HermesRunResult:
        from aifpl.scheduler import DeadlineScheduler

        schedule = DeadlineScheduler(self.root).status()
        if schedule.missed:
            raise ValueError(f"GW{schedule.event} has already passed its deadline")
        return self.run(
            expected_gameweek=schedule.event,
            expected_season_id=schedule.season_id,
            deadline=schedule.deadline,
        )

    def supersede_decision(
        self,
        base_state_id: str,
        supersedes_decision_id: str,
        reason: str,
        expected_gameweek: int,
        expected_season_id: str,
    ) -> HermesSupersessionResult:
        """Append a corrected decision from a known-valid prior state.

        This is intentionally explicit: a bad decision remains immutable while the
        replacement is linked to both its valid base state and the bad decision.
        """
        self._expected_gameweek = expected_gameweek
        self._expected_season_id = expected_season_id
        lock_path = self.root / "hermes" / "run.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                flock(descriptor, LOCK_EX | LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another Hermes run is already in progress") from exc
            return self._supersede_decision_unlocked(base_state_id, supersedes_decision_id, reason)
        finally:
            flock(descriptor, LOCK_UN)
            os.close(descriptor)

    def _supersede_decision_unlocked(
        self, base_state_id: str, supersedes_decision_id: str, reason: str,
    ) -> HermesSupersessionResult:
        reason = reason.strip()
        if not reason:
            raise ValueError("Correction reason must not be empty")
        base_state_path = self._hermes_artifact_path("states", base_state_id)
        superseded_decision_path = self._hermes_artifact_path("decisions", supersedes_decision_id)
        base_state = HermesState.model_validate_json(base_state_path.read_text(encoding="utf-8"))
        superseded = HermesDecision.model_validate_json(superseded_decision_path.read_text(encoding="utf-8"))
        base_decision_path = self._hermes_artifact_path("decisions", Path(base_state.decision_path).name)
        base_decision = HermesDecision.model_validate_json(base_decision_path.read_text(encoding="utf-8"))
        superseded_state_path = self._hermes_artifact_path("states", Path(superseded.state_path).name)
        superseded_state = HermesState.model_validate_json(superseded_state_path.read_text(encoding="utf-8"))
        latest_decision = self.latest_decision(season_id=self._expected_season_id)
        latest_state = self.latest_state(season_id=self._expected_season_id)

        if Path(base_decision.state_path).name != base_state_path.name:
            raise ValueError("Base decision does not reference the supplied state")
        if Path(superseded_state.decision_path).name != superseded_decision_path.name:
            raise ValueError("Superseded decision does not reference its state")
        if Path(latest_decision.decision_path).name != superseded_decision_path.name:
            raise ValueError("Only the latest Hermes decision can be superseded")
        if Path(latest_state.decision_path).name != superseded_decision_path.name:
            raise ValueError("Latest Hermes state does not match the decision to supersede")
        if (
            base_state.season_id != self._expected_season_id
            or base_decision.season_id != self._expected_season_id
            or superseded.season_id != self._expected_season_id
            or superseded_state.season_id != self._expected_season_id
        ):
            raise ValueError("Correction artifacts must belong to the expected season")
        if base_decision.gameweek != base_state.gameweek:
            raise ValueError("Base decision and state gameweeks do not match")
        if self._expected_gameweek != base_state.gameweek + 1 or superseded.gameweek != self._expected_gameweek:
            raise ValueError("Correction must advance exactly one gameweek from its base state")

        self._strategy = base_state.strategy
        self._horizon, self._catalog_id = self.backend.horizon_plan(
            base_state.squad, self._strategy, self._expected_gameweek,
        )
        self._hold_horizon, _ = self.backend.hold_horizon_plan(
            base_state.squad, self._strategy, self._expected_gameweek,
        )
        self._hold = _hold_week_from_plan(self._hold_horizon, self._expected_gameweek)
        action = "execute_horizon" if self._horizon.gameweeks[0].transfers_made else "hold"
        result = self._commit(
            {
                "action": action,
                "explanation": (
                    f"Corrected GW{self._expected_gameweek} recommendation from the committed "
                    f"GW{base_state.gameweek} squad: {reason}"
                ),
            },
            base_state,
            model_name="deterministic_correction",
            state_version=latest_state.version + 1,
            base_state_path=str(base_state_path),
            supersedes_decision_path=str(superseded_decision_path),
            supersedes_state_path=str(superseded_state_path),
            correction_reason=reason,
        )
        correction_path = self.root / "hermes" / "corrections" / f"{Path(result.decision.decision_path).stem}.json"
        write_immutable(correction_path, json_bytes({
            "created_at": result.decision.created_at,
            "reason": reason,
            "base_state_path": str(base_state_path),
            "base_state_sha256": sha256_path(base_state_path),
            "superseded_decision_path": str(superseded_decision_path),
            "superseded_decision_sha256": sha256_path(superseded_decision_path),
            "superseded_state_path": str(superseded_state_path),
            "superseded_state_sha256": sha256_path(superseded_state_path),
            "replacement_decision_path": result.decision.decision_path,
            "replacement_decision_sha256": sha256_path(Path(result.decision.decision_path)),
            "replacement_state_path": result.state_path,
            "replacement_state_sha256": sha256_path(Path(result.state_path)),
        }, pretty=True))
        return HermesSupersessionResult(
            decision=result.decision,
            state_path=result.state_path,
            correction_path=str(correction_path),
        )

    def reinitialize_current(self, force: bool = False) -> HermesRunResult | None:
        from aifpl.scheduler import DeadlineScheduler

        schedule = DeadlineScheduler(self.root).status()
        if schedule.event != 1 or schedule.missed:
            return None
        return self.reinitialize_opening_squad(schedule.event, schedule.season_id, force=force)

    def reinitialize_opening_squad(
        self, expected_gameweek: int, expected_season_id: str, force: bool = False,
    ) -> HermesRunResult | None:
        if expected_gameweek != 1:
            return None
        self._expected_gameweek = expected_gameweek
        self._expected_season_id = expected_season_id
        lock_path = self.root / "hermes" / "run.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                flock(descriptor, LOCK_EX | LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another Hermes run is already in progress") from exc
            previous = self.latest_state(optional=True)
            if (
                previous is None
                or previous.gameweek != expected_gameweek
                or previous.season_id != expected_season_id
            ):
                return None
            if not force and self._opening_plan_is_current():
                return None
            self._strategy = previous.strategy
            self._initial, self._initial_gameweek, self._initial_snapshot = self.backend.initial_squad(self._strategy)
            return self._commit(
                {
                    "action": "adopt_initial",
                    "explanation": "Pre-season reinitialization using the multi-gameweek horizon optimizer.",
                },
                previous,
                model_name=previous.model,
            )
        finally:
            flock(descriptor, LOCK_UN)
            os.close(descriptor)

    def _opening_plan_is_current(self) -> bool:
        try:
            state = self.latest_state(optional=True)
            plan = self.latest_decision().horizon_plan
            return (
                state is not None
                and state.initialization_method == "horizon_v1"
                and plan is not None
                and plan.planner_version == PLANNER_VERSION
            )
        except FileNotFoundError:
            return False

    def _run_unlocked(self) -> HermesRunResult:
        if self.model is None:
            self.model = OpenAICompatibleHermesModel()
        previous = self.latest_state(optional=True)
        if previous is not None and self._expected_season_id and not previous.season_id:
            raise ValueError("Legacy Hermes state has no season ID; run hermes-migrate-state first")
        if previous is not None and self._expected_season_id and previous.season_id != self._expected_season_id:
            previous = None
        if previous is not None and self._expected_gameweek is not None:
            if previous.gameweek == self._expected_gameweek:
                decision = self.latest_decision(
                    season_id=previous.season_id or self._expected_season_id,
                    gameweek=self._expected_gameweek,
                )
                return HermesRunResult(decision=decision, state_path=decision.state_path, tool_steps=0)
            if previous.gameweek > self._expected_gameweek:
                raise ValueError(f"Hermes has already advanced to gameweek {previous.gameweek}")
        if previous is not None and self._expected_gameweek and self._expected_gameweek > previous.gameweek + 1:
            skipped = self._expected_gameweek - previous.gameweek - 1
            previous = previous.model_copy(update={
                "squad": previous.squad.model_copy(update={"free_transfers": min(5, previous.squad.free_transfers + skipped)})
            })
        # A manager can be reused for consecutive gameweeks. Never carry a plan
        # computed for the prior run into the next strategy decision.
        self._initial = None
        self._initial_snapshot = None
        self._horizon = None
        self._catalog_id = None
        self._hold = None
        self._hold_horizon = None
        self._initial_gameweek = None
        self._strategy = previous.strategy if previous else None
        system = (
            "You are Hermes, an autonomous FPL manager playing your own experimental team. "
            "The backend is authoritative for all numbers and legality. Set your own stable strategy when none exists, "
            "inspect backend recommendations, then commit exactly one action. Never invent projections or player IDs. "
            "The get_horizon_plan output is computed for your own current squad: every player named in its out/in lists "
            "is a member of your squad and the transfers are directly actionable. During pre-season, only GW1 "
            "transfers are free; later gameweeks follow normal free-transfer rollover and hit rules. "
            "Do not reject a plan because a name looks unfamiliar; "
            "re-read the squad and plan before deciding. "
            "The context includes decision_history with your prior decisions' outcomes; use it as advisory evidence only. "
            "Any outcome-driven strategy change is recommendation-only for a new planning cycle, never an automatic mutation "
            "of an already computed plan; set and finalize strategy before requesting a plan."
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"existing_state": previous.model_dump(mode="json") if previous else None, "context": self.backend.context()}, default=str)},
        ]
        tools = _tools()
        max_steps = self.model.settings.max_tool_steps if isinstance(self.model, OpenAICompatibleHermesModel) else 8
        transcript: dict[str, Any] = {"messages": messages}
        try:
            for step in range(1, max_steps + 1):
                message = self.model.complete(messages, tools)
                messages.append(message)
                tool_calls = message.get("tool_calls") or []
                if not tool_calls:
                    raise RuntimeError("Hermes ended without committing a decision")
                for call in tool_calls:
                    try:
                        name = call["function"]["name"]
                        arguments = json.loads(call["function"].get("arguments") or "{}")
                        output = self._call_tool(name, arguments, previous)
                    except (KeyError, TypeError, json.JSONDecodeError, ValueError, FileNotFoundError) as exc:
                        output = {"error": str(exc), "recoverable": True}
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(output, default=str)})
                    if isinstance(output, HermesRunResult):
                        transcript["outcome"] = "succeeded"
                        transcript["tool_steps"] = step
                        transcript["gameweek"] = output.decision.gameweek
                        transcript["season_id"] = output.decision.season_id
                        transcript["decision_path"] = output.decision.decision_path
                        return output.model_copy(update={"tool_steps": step})
            raise RuntimeError("Hermes exceeded its tool-call step limit")
        except Exception as exc:
            transcript["outcome"] = "failed"
            transcript["error"] = redact_secrets(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            transcript.setdefault("created_at", datetime.now(timezone.utc))
            transcript.setdefault("gameweek", self._expected_gameweek)
            transcript.setdefault("season_id", self._expected_season_id)
            transcript.setdefault("tool_steps", 0)
            self._write_transcript(transcript)

    def _write_transcript(self, transcript: dict[str, Any]) -> None:
        stamp = transcript["created_at"].strftime("%Y%m%dT%H%M%S%fZ")
        path = self.root / "hermes" / "runs" / f"{stamp}.json"
        write_immutable(path, json_bytes(HermesRunTranscript(**transcript).model_dump(mode="json"), pretty=True))

    def _latest_transcript(
        self, season_id: str | None = None, gameweek: int | None = None,
    ) -> "HermesRunTranscript":
        directory = self.root / "hermes" / "runs"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("Hermes has no run transcripts yet")
        for path in reversed(files):
            try:
                transcript = HermesRunTranscript.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if season_id is not None and transcript.season_id != season_id:
                continue
            if gameweek is not None and transcript.gameweek != gameweek:
                continue
            return transcript
        raise FileNotFoundError("Hermes has no matching run transcripts yet")

    def latest_transcript(
        self, season_id: str | None = None, gameweek: int | None = None,
    ) -> "HermesRunTranscript":
        return self._latest_transcript(season_id, gameweek)

    def decisions(
        self, limit: int = 50, season_id: str | None = None, gameweek: int | None = None,
    ) -> list["HermesDecision"]:
        directory = self.root / "hermes" / "decisions"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        if limit <= 0:
            return []
        decisions: list[HermesDecision] = []
        for path in reversed(files):
            try:
                if not _sibling_exists(self.root, path, "states"):
                    continue
                decision = HermesDecision.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if season_id is not None and decision.season_id != season_id:
                continue
            if gameweek is not None and decision.gameweek != gameweek:
                continue
            decisions.append(decision)
            if len(decisions) >= limit:
                break
        return decisions

    def latest_state(
        self, optional: bool = False, season_id: str | None = None, gameweek: int | None = None,
    ) -> HermesState | None:
        directory = self.root / "hermes" / "states"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        state: HermesState | None = None
        for path in reversed(files):
            try:
                if not _sibling_exists(self.root, path, "decisions"):
                    continue
                candidate = HermesState.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if season_id is not None and candidate.season_id != season_id:
                continue
            if gameweek is not None and candidate.gameweek != gameweek:
                continue
            state = candidate
            break
        if state is None:
            if optional:
                return None
            raise FileNotFoundError("Hermes has no state yet; run hermes-run first")
        updates: dict[str, Any] = {}
        if not state.squad.purchase_prices:
            raise ValueError("Legacy Hermes state has no purchase prices; migrate it explicitly before autonomous transfers")
        if state.gameweek == 0:
            try:
                updates["gameweek"] = self.latest_decision().gameweek
            except FileNotFoundError:
                catalog_id = self.backend._latest_catalog_id(1, 6)
                updates["gameweek"] = min(row.gameweek for row in OddsProjectionStore(self.root).latest(catalog_id))
        return state.model_copy(update=updates)

    def latest_decision(
        self, season_id: str | None = None, gameweek: int | None = None,
    ) -> HermesDecision:
        decisions = self.decisions(1, season_id=season_id, gameweek=gameweek)
        if not decisions:
            raise FileNotFoundError("Hermes has no decisions yet")
        return decisions[0]

    def migrate_legacy_state(self, purchase_prices: dict[int, int], gameweek: int, season_id: str) -> HermesRunResult:
        lock_path = self.root / "hermes" / "run.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                flock(descriptor, LOCK_EX | LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another Hermes run is already in progress") from exc
            return self._migrate_legacy_state(purchase_prices, gameweek, season_id)
        finally:
            flock(descriptor, LOCK_UN)
            os.close(descriptor)

    def _migrate_legacy_state(self, purchase_prices: dict[int, int], gameweek: int, season_id: str) -> HermesRunResult:
        directory = self.root / "hermes" / "states"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        legacy: HermesState | None = None
        for path in reversed(files):
            try:
                if not _sibling_exists(self.root, path, "decisions"):
                    continue
                legacy = HermesState.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            break
        if legacy is None:
            raise FileNotFoundError("No Hermes state exists to migrate")
        if set(purchase_prices) != set(legacy.squad.player_ids):
            raise ValueError("Purchase-price mapping must contain exactly the 15 squad IDs")
        if not 1 <= gameweek <= 38:
            raise ValueError("gameweek must be within 1..38")
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        decision_path = self.root / "hermes" / "decisions" / f"{stamp}.json"
        state_path = self.root / "hermes" / "states" / f"{stamp}.json"
        squad = legacy.squad.model_copy(update={"purchase_prices": purchase_prices})
        decision = HermesDecision(
            action="migrate_legacy_state", gameweek=gameweek, squad=squad,
            captain_id=legacy.captain_id, starting_xi_ids=legacy.starting_xi_ids,
            transfers_out=[], transfers_in=[], explanation="Explicit legacy state migration",
            strategy=legacy.strategy, model=legacy.model, created_at=now,
            backend_methodology="legacy_state_migration", decision_path=str(decision_path),
            state_path=str(state_path), season_id=season_id,
            vice_captain_id=legacy.vice_captain_id,
            bench_ids=legacy.bench_ids or [
                player_id for player_id in squad.player_ids
                if player_id not in set(legacy.starting_xi_ids)
            ],
            active_chip=legacy.active_chip,
            active_chip_set=legacy.active_chip_set,
        )
        state = legacy.model_copy(update={
            "squad": squad, "gameweek": gameweek, "season_id": season_id,
            "updated_at": now, "version": legacy.version + 1, "decision_path": str(decision_path),
        })
        write_immutable(state_path, json_bytes(state.model_dump(mode="json"), pretty=True))
        write_immutable(decision_path, json_bytes(decision.model_dump(mode="json"), pretty=True))
        return HermesRunResult(decision=decision, state_path=str(state_path), tool_steps=0)

    def _hermes_artifact_path(self, directory: str, artifact_id: str) -> Path:
        candidate = Path(artifact_id)
        if candidate.name != artifact_id or candidate.suffix != ".json":
            raise ValueError(f"Invalid Hermes {directory} artifact ID")
        path = self.root / "hermes" / directory / candidate
        if not path.is_file():
            raise FileNotFoundError(f"Hermes {directory} artifact does not exist: {artifact_id}")
        return path

    def _call_tool(self, name: str, arguments: dict[str, Any], previous: HermesState | None) -> Any:
        if name == "set_strategy":
            strategy = HermesStrategy.model_validate(arguments)
            if previous is not None and strategy != previous.strategy:
                raise ValueError(
                    "Hermes strategy cannot change after a plan or initial decision; outcome-driven changes require manual review"
                )
            if (
                self._initial is not None or self._horizon is not None or self._hold is not None
            ) and strategy != self._strategy:
                raise ValueError("Hermes strategy cannot change after a plan has been computed")
            self._strategy = strategy
            return {"accepted": True, "strategy": self._strategy.model_dump()}
        if name == "get_initial_squad":
            if self._strategy is None:
                raise ValueError("Set strategy before requesting a squad")
            self._initial, self._initial_gameweek, self._initial_snapshot = self.backend.initial_squad(self._strategy)
            return _squad_summary(self._initial)
        if name == "get_horizon_plan":
            if previous is None:
                return {"error": "No existing squad; use get_initial_squad"}
            if self._strategy is None:
                raise ValueError("Set strategy before requesting a plan")
            target_gameweek = self._expected_gameweek or previous.gameweek + 1
            self._horizon, self._catalog_id = self.backend.horizon_plan(previous.squad, self._strategy, target_gameweek)
            self._hold_horizon, _ = self.backend.hold_horizon_plan(previous.squad, self._strategy, target_gameweek)
            self._hold = _hold_week_from_plan(self._hold_horizon, target_gameweek)
            summary = _horizon_summary(self._horizon)
            summary["applies_to_current_squad"] = True
            summary["projection_catalog"] = self._catalog_id
            if target_gameweek == 1:
                summary["pre_season"] = True
                summary["note"] = "Pre-season: GW1 transfers are free; later gameweeks include normal hit costs."
            return summary
        if name == "commit_decision":
            return self._commit(arguments, previous)
        raise ValueError(f"Unknown Hermes tool: {name}")

    def _commit(
        self,
        arguments: dict[str, Any],
        previous: HermesState | None,
        model_name: str | None = None,
        state_version: int | None = None,
        base_state_path: str | None = None,
        supersedes_decision_path: str | None = None,
        supersedes_state_path: str | None = None,
        correction_reason: str | None = None,
    ) -> HermesRunResult:
        if self._strategy is None:
            raise ValueError("Hermes must set a strategy before committing")
        action = arguments["action"]
        explanation = str(arguments["explanation"])
        plan_snapshot: HorizonPlanSnapshot | None = None
        vice_captain_id: int | None = None
        bench_ids: list[int] = []
        if action == "adopt_initial":
            if self._initial is None:
                raise ValueError("Hermes must inspect the initial squad before adopting it")
            squad_ids = [player.player_id for player in self._initial.players]
            starting_ids = [player.player_id for player in self._initial.starting_xi]
            gameweek = self._initial_gameweek
            assert gameweek is not None
            captain_id, bank, free, outgoing, incoming = self._initial.captain.player_id, self._initial.bank, 1, [], squad_ids
            purchase_prices = {player.player_id: player.cost for player in self._initial.players}
            methodology = self._initial.methodology
            plan_snapshot = self._initial_snapshot
            vice_captain = max(
                (player for player in self._initial.starting_xi if player.player_id != captain_id),
                key=lambda player: player.projected_points,
                default=None,
            )
            if vice_captain is not None:
                vice_captain_id = vice_captain.player_id
            bench_ids = [player_id for player_id in squad_ids if player_id not in set(starting_ids)]
        elif action in ("execute_horizon", "hold"):
            if previous is None or self._horizon is None:
                raise ValueError("Hermes must inspect a horizon plan before committing")
            if action == "hold":
                assert self._hold is not None
                squad_ids, starting_ids = previous.squad.player_ids, self._hold["starting_ids"]
                captain_id, bank, outgoing, incoming, gameweek = self._hold["captain_id"], previous.squad.bank, [], [], self._hold["gameweek"]
                vice_captain_id = self._hold.get("vice_captain_id")
                free, methodology, purchase_prices = min(5, previous.squad.free_transfers + 1), self._hold["methodology"], previous.squad.purchase_prices
                bench_ids = [player_id for player_id in squad_ids if player_id not in set(starting_ids)]
            else:
                week = self._horizon.gameweeks[0]
                squad_ids = [player.player_id for player in week.resulting_squad]
                starting_ids = [player.player_id for player in week.starting_xi]
                captain_id, bank, outgoing, incoming, gameweek = week.captain.player_id, week.bank_after, [p.player_id for p in week.outgoing], [p.player_id for p in week.incoming], week.gameweek
                vice_captain_id = week.vice_captain.player_id if week.vice_captain is not None else None
                free = min(5, max(0, previous.squad.free_transfers - week.transfers_made) + 1)
                methodology = self._horizon.methodology
                costs = {player.player_id: player.cost for player in week.resulting_squad}
                purchase_prices = {player_id: previous.squad.purchase_prices.get(player_id, costs[player_id]) for player_id in squad_ids}
                bench_ids = [player_id for player_id in squad_ids if player_id not in set(starting_ids)]
            if gameweek == 1:
                free = 1
            if action == "hold":
                plan_snapshot = (
                    _plan_snapshot(self._hold_horizon, self._catalog_id or "", pre_season=gameweek == 1)
                    if self._hold_horizon is not None
                    else _hold_plan_snapshot(
                        self._horizon, self._catalog_id or "", pre_season=gameweek == 1,
                        hold=self._hold, previous=previous,
                    )
                )
            else:
                plan_snapshot = _plan_snapshot(self._horizon, self._catalog_id or "", pre_season=gameweek == 1)
        else:
            raise ValueError("action must be adopt_initial, execute_horizon, or hold")
        now = datetime.now(timezone.utc)
        if previous is not None and self._expected_gameweek is None and gameweek != previous.gameweek + 1:
            raise ValueError(f"Hermes must commit gameweek {previous.gameweek + 1}, not {gameweek}")
        if self._expected_gameweek is not None and gameweek != self._expected_gameweek:
            raise ValueError(f"Hermes decision is for gameweek {gameweek}, expected {self._expected_gameweek}")
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        decision_path = self.root / "hermes" / "decisions" / f"{stamp}.json"
        squad = HermesSquadState(player_ids=squad_ids, bank=bank, free_transfers=free, purchase_prices=purchase_prices)
        state_path = self.root / "hermes" / "states" / f"{stamp}.json"
        season_id = self._expected_season_id or (previous.season_id if previous else _season_id_for_now(now))
        decision_model = model_name or (self.model.model_name if self.model is not None else previous.model if previous else "unknown")
        active_chip = arguments.get("active_chip")
        active_chip_set = arguments.get("active_chip_set")
        if active_chip is not None:
            if active_chip not in ("wildcard", "free_hit", "bench_boost", "triple_captain"):
                raise ValueError("active_chip must be a supported FPL chip")
            if active_chip_set not in (1, 2):
                raise ValueError("active_chip_set must be 1 or 2 when a chip is confirmed")
            from aifpl.chips import ChipStateStore

            ChipStateStore(self.root).validate_activation(
                season_id, active_chip, active_chip_set, gameweek,
            )
        decision = HermesDecision(
            action=action, gameweek=gameweek, squad=squad, captain_id=captain_id,
            starting_xi_ids=starting_ids, transfers_out=outgoing, transfers_in=incoming,
            explanation=explanation, strategy=self._strategy, model=decision_model,
            created_at=now, backend_methodology=methodology, decision_path=str(decision_path),
            state_path=str(state_path),
            season_id=season_id,
            horizon_plan=plan_snapshot,
            base_state_path=base_state_path,
            supersedes_decision_path=supersedes_decision_path,
            correction_reason=correction_reason,
            vice_captain_id=vice_captain_id,
            bench_ids=bench_ids,
            active_chip=active_chip,
            active_chip_set=active_chip_set,
        )
        state = HermesState(
            strategy=self._strategy, squad=squad, captain_id=captain_id, starting_xi_ids=starting_ids,
            model=decision_model, updated_at=now,
            version=state_version if state_version is not None else (previous.version + 1 if previous else 1),
            gameweek=gameweek, decision_path=str(decision_path),
            season_id=season_id,
            supersedes_state_path=supersedes_state_path,
            vice_captain_id=vice_captain_id,
            bench_ids=bench_ids,
            active_chip=active_chip,
            active_chip_set=active_chip_set,
            initialization_method="horizon_v1" if action == "adopt_initial" else (
                previous.initialization_method if previous else ""
            ),
        )
        if self._deadline is not None:
            now_for_deadline = self._deadline_clock or datetime.now(timezone.utc)
            if now_for_deadline.astimezone(timezone.utc) >= self._deadline.astimezone(timezone.utc):
                raise ValueError("Hermes decision cannot be committed at or after the official deadline")
        write_immutable(state_path, json_bytes(state.model_dump(mode="json"), pretty=True))
        write_immutable(decision_path, json_bytes(decision.model_dump(mode="json"), pretty=True))
        if active_chip is not None:
            ChipStateStore(self.root).mark_used(season_id, active_chip, active_chip_set, gameweek)
        return HermesRunResult(decision=decision, state_path=str(state_path), tool_steps=0)


def _sibling_exists(root: Path, artifact: Path, sibling_directory: str) -> bool:
    try:
        document = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(document, dict):
        return False
    reference = document.get("state_path" if sibling_directory == "states" else "decision_path")
    candidates: list[Path] = []
    if reference:
        candidate = Path(str(reference))
        candidates.extend([candidate] if candidate.is_absolute() else [
            root / candidate, root / "hermes" / sibling_directory / candidate,
        ])
        candidates.append(root / "hermes" / sibling_directory / candidate.name)
    candidates.append(root / "hermes" / sibling_directory / f"{artifact.stem}.json")
    linked_key = "decision_path" if sibling_directory == "states" else "state_path"
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            sibling = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(sibling, dict):
            continue
        linked = sibling.get(linked_key)
        if linked and not _reference_points_to(root, linked_key, linked, artifact):
            continue
        return True
    return False


def _reference_points_to(root: Path, reference_key: str, reference: str, artifact: Path) -> bool:
    directory = "decisions" if reference_key == "decision_path" else "states"
    candidate = Path(str(reference))
    paths = [candidate] if candidate.is_absolute() else [
        root / candidate, root / "hermes" / directory / candidate,
    ]
    return any(path.is_file() and path.resolve() == artifact.resolve() for path in paths)


def _tools() -> list[dict[str, Any]]:
    return [
        _tool("set_strategy", "Set your autonomous strategy before planning; outcome history is advisory and any outcome-driven change is recommendation-only for this new plan. differential_appetite (0..1) weights under-owned players in squad purchases only, adding up to appetite * projected_points extra value for a 0%-owned player, and never changes which players start", {"risk_tolerance": {"type": "number", "minimum": 0, "maximum": 1}, "hit_aversion": {"type": "number", "minimum": 0, "maximum": 1}, "differential_appetite": {"type": "number", "minimum": 0, "maximum": 1}, "planning_horizon": {"type": "integer", "minimum": 3, "maximum": 6}, "preferred_players": {"type": "array", "items": {"type": "string"}}, "rationale": {"type": "string"}}, ["risk_tolerance", "hit_aversion", "differential_appetite", "planning_horizon", "rationale"]),
        _tool("get_initial_squad", "Get the backend's best multi-gameweek initial squad", {}, []),
        _tool("get_horizon_plan", "Get the backend's transfer plan for the existing squad", {}, []),
        _tool("commit_decision", "Commit one backend-validated action. Chip fields are optional and mean confirmed activation, never mere advice.", {"action": {"type": "string", "enum": ["adopt_initial", "execute_horizon", "hold"]}, "explanation": {"type": "string"}, "active_chip": {"type": ["string", "null"], "enum": ["wildcard", "free_hit", "bench_boost", "triple_captain", None]}, "active_chip_set": {"type": ["integer", "null"], "enum": [1, 2, None]}}, ["action", "explanation"]),
    ]


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}}


def _strategy_churn_penalty(strategy: HermesStrategy) -> float:
    from aifpl.config import transfer_penalty

    return round(transfer_penalty() + (1 - strategy.risk_tolerance) * 2.0, 4)


def _prefer_scorecard(candidate: Any, current: Any) -> bool:
    candidate_confirmed = candidate.evaluation_basis == "confirmed_execution"
    current_confirmed = current.evaluation_basis == "confirmed_execution"
    if candidate_confirmed != current_confirmed:
        return candidate_confirmed
    return candidate.scoring_at > current.scoring_at


def _hold_week_from_plan(plan: HorizonTransferPlan, target_gameweek: int) -> dict[str, Any]:
    if not plan.gameweeks:
        raise ValueError(f"Hold plan has no gameweek {target_gameweek}")
    week = plan.gameweeks[0]
    return {
        "gameweek": week.gameweek,
        "starting_ids": [player.player_id for player in week.starting_xi],
        "captain_id": week.captain.player_id,
        "vice_captain_id": week.vice_captain.player_id if week.vice_captain is not None else None,
        "projected_points": week.projected_points,
        "methodology": plan.methodology,
        "formation": f"{sum(player.position == 'DEF' for player in week.starting_xi)}-"
        f"{sum(player.position == 'MID' for player in week.starting_xi)}-"
        f"{sum(player.position == 'FWD' for player in week.starting_xi)}",
    }


def _squad_summary(squad: OptimizedSquad) -> dict[str, Any]:
    return {"players": [{"id": p.player_id, "name": p.player_name, "position": p.position, "club": p.club, "points": p.projected_points} for p in squad.players], "starting_xi": [p.player_name for p in squad.starting_xi], "captain": squad.captain.player_name, "projected_points": squad.projected_points, "bank": squad.bank, "methodology": squad.methodology}


def _plan_snapshot(plan: HorizonTransferPlan, catalog_id: str, pre_season: bool) -> HorizonPlanSnapshot:
    return HorizonPlanSnapshot(
        projection_catalog=catalog_id,
        pre_season=pre_season,
        solver_status=plan.solver_status,
        methodology=plan.methodology,
        planner_version=PLANNER_VERSION,
        total_projected_points=plan.total_projected_points,
        total_hit_cost=plan.total_hit_cost,
        total_net_projected_points=plan.total_net_projected_points,
        robustness_score=plan.robustness_score,
        objective_value=plan.objective_value,
        objective_components=plan.objective_components,
        weeks=[_week_snapshot(week) for week in plan.gameweeks],
    )


def _hold_plan_snapshot(
    plan: HorizonTransferPlan, catalog_id: str, pre_season: bool,
    hold: dict[str, Any], previous: HermesState,
) -> HorizonPlanSnapshot:
    """Replace the planned action week with the action that was committed."""
    weeks = [_week_snapshot(week) for week in plan.gameweeks]
    if not weeks:
        return _plan_snapshot(plan, catalog_id, pre_season)

    planned = weeks[0]
    gameweek = int(hold["gameweek"])
    free_after = 1 if gameweek == 1 else min(5, previous.squad.free_transfers + 1)
    held = planned.model_copy(update={
        "gameweek": gameweek,
        "transfers_made": 0,
        "free_transfers_before": previous.squad.free_transfers,
        "hit_cost": 0,
        "bank_after": previous.squad.bank,
        "projected_points": hold["projected_points"],
        "net_projected_points": hold["projected_points"],
        "outgoing_ids": [],
        "incoming_ids": [],
        "captain_id": hold["captain_id"],
        "vice_captain_id": hold.get("vice_captain_id"),
        "starting_xi_ids": hold["starting_ids"],
        "squad_ids": list(previous.squad.player_ids),
        "free_transfers_after": free_after,
    })
    weeks[0] = held
    total_projected = sum(week.projected_points for week in weeks)
    total_hit = sum(week.hit_cost for week in weeks)
    total_net = sum(week.net_projected_points for week in weeks)
    objective_components = dict(plan.objective_components)
    return HorizonPlanSnapshot(
        projection_catalog=catalog_id,
        pre_season=pre_season,
        solver_status=plan.solver_status,
        methodology=plan.methodology,
        planner_version=PLANNER_VERSION,
        total_projected_points=round(total_projected, 4),
        total_hit_cost=total_hit,
        total_net_projected_points=round(total_net, 4),
        robustness_score=plan.robustness_score,
        objective_value=plan.objective_value,
        objective_components=objective_components,
        weeks=weeks,
    )


def _week_snapshot(week: HorizonGameweekPlan) -> HorizonPlanWeekSnapshot:
    return HorizonPlanWeekSnapshot(
        gameweek=week.gameweek,
        transfers_made=week.transfers_made,
        free_transfers_before=week.free_transfers_before,
        hit_cost=week.hit_cost,
        bank_after=week.bank_after,
        projected_points=week.projected_points,
        net_projected_points=week.net_projected_points,
        odds_coverage=week.odds_coverage,
        robustness_score=week.robustness_score,
        unlimited_transfers=week.unlimited_transfers,
        free_transfers_after=week.free_transfers_after,
        outgoing_ids=[player.player_id for player in week.outgoing],
        incoming_ids=[player.player_id for player in week.incoming],
        captain_id=week.captain.player_id,
        vice_captain_id=week.vice_captain.player_id if week.vice_captain is not None else None,
        starting_xi_ids=[player.player_id for player in week.starting_xi],
        squad_ids=[player.player_id for player in week.resulting_squad],
        bank_before=week.bank_before,
        purchase_value=week.purchase_value,
        sale_value=week.sale_value,
        objective_net_points=week.objective_net_points,
        objective_components=week.objective_components,
    )


def _horizon_summary(plan: HorizonTransferPlan) -> dict[str, Any]:
    return {
        "solver_status": plan.solver_status,
        "total_net_points": plan.total_net_projected_points,
        "weeks": [
            {
                "gameweek": w.gameweek,
                "out": [p.player_name for p in w.outgoing],
                "in": [p.player_name for p in w.incoming],
                "transfers_made": w.transfers_made,
                "free_transfers_before": w.free_transfers_before,
                "free_transfers_after": w.free_transfers_after,
                "unlimited_transfers": w.unlimited_transfers,
                "hits": w.hit_cost,
                "bank_after": w.bank_after,
                "captain": w.captain.player_name,
                "net_points": w.net_projected_points,
            }
            for w in plan.gameweeks
        ],
    }


def _season_id_for_now(now: datetime) -> str:
    start_year = now.year if now.month >= 7 else now.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"
