from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from aifpl.artifacts import json_bytes, write_immutable
from aifpl.fpl import FplClient
from aifpl.hermes import HermesDecision
from aifpl.odds_projections import OddsProjectionStore
from aifpl.snapshots import SnapshotStore


class ScoredPlayer(BaseModel):
    element: int
    name: str
    position: str
    projected: float
    actual: float


class ScoredTransfer(BaseModel):
    out_element: int
    in_element: int
    out_name: str
    in_name: str
    out_actual: float
    in_actual: float
    delta: float


class DecisionScore(BaseModel):
    decision_path: str
    gameweek: int
    season_id: str
    action: str = ""
    scoring_at: datetime
    projection_catalog: str
    event_snapshot: str
    xi_projected: float
    xi_actual: float
    bench_projected: float
    bench_actual: float
    captain: ScoredPlayer | None = None
    transfers: list[ScoredTransfer] = Field(default_factory=list)
    players: list[ScoredPlayer] = Field(default_factory=list)
    total_projected: float
    total_actual: float
    output_path: str = ""


class DecisionScorer:
    """Score a committed Hermes decision against the completed gameweek's
    actuals. Predictions come from the odds projection catalog used at
    decision time; actuals come from the official event-live snapshot."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def score(self, decision_path: Path | str, event: int | None = None) -> DecisionScore:
        decision = HermesDecision.model_validate_json(Path(decision_path).read_text(encoding="utf-8"))
        gameweek = event or decision.gameweek
        catalog_path = self._catalog_for_gameweek(gameweek)
        rows = [row for row in OddsProjectionStore(self.root).latest(catalog_path.name) if row.gameweek == gameweek]
        projections = {row.player_id: row.projected_points for row in rows}
        names = {row.player_id: (row.player_name, row.position) for row in rows}
        actuals = self._event_actuals(gameweek)

        xi_ids = decision.starting_xi_ids
        bench_ids = [element for element in decision.squad.player_ids if element not in set(xi_ids)]
        captain_actual = actuals.get(decision.captain_id, 0.0)
        captain_projected = projections.get(decision.captain_id, 0.0)
        xi_projected = sum(projections.get(element, 0.0) for element in xi_ids) + captain_projected
        xi_actual = sum(actuals.get(element, 0.0) for element in xi_ids) + captain_actual
        bench_projected = sum(projections.get(element, 0.0) for element in bench_ids)
        bench_actual = sum(actuals.get(element, 0.0) for element in bench_ids)

        players = [
            ScoredPlayer(
                element=element, name=names.get(element, (f"#{element}", "?"))[0],
                position=names.get(element, ("?", "?"))[1],
                projected=round(projections.get(element, 0.0), 4), actual=actuals.get(element, 0.0),
            )
            for element in decision.squad.player_ids
        ]
        transfers = [
            ScoredTransfer(
                out_element=out_element, in_element=in_element,
                out_name=names.get(out_element, (f"#{out_element}", "?"))[0],
                in_name=names.get(in_element, (f"#{in_element}", "?"))[0],
                out_actual=actuals.get(out_element, 0.0), in_actual=actuals.get(in_element, 0.0),
                delta=round(actuals.get(in_element, 0.0) - actuals.get(out_element, 0.0), 4),
            )
            for out_element, in_element in zip(decision.transfers_out, decision.transfers_in)
        ]
        record = DecisionScore(
            decision_path=str(decision_path), gameweek=gameweek, season_id=decision.season_id,
            action=decision.action, scoring_at=datetime.now(timezone.utc), projection_catalog=str(catalog_path),
            event_snapshot=str(self._event_snapshot_path(gameweek)),
            xi_projected=round(xi_projected, 4), xi_actual=round(xi_actual, 4),
            bench_projected=round(bench_projected, 4), bench_actual=round(bench_actual, 4),
            captain=ScoredPlayer(
                element=decision.captain_id, name=names.get(decision.captain_id, (f"#{decision.captain_id}", "?"))[0],
                position=names.get(decision.captain_id, ("?", "?"))[1],
                projected=round(captain_projected, 4), actual=round(captain_actual, 4),
            ),
            transfers=transfers, players=players,
            total_projected=round(xi_projected + bench_projected, 4),
            total_actual=round(xi_actual + bench_actual, 4),
        )
        output_path = self.root / "scoring" / "decisions" / f"{record.scoring_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        record = record.model_copy(update={"output_path": str(output_path)})
        write_immutable(output_path, json_bytes(record.model_dump(mode="json"), pretty=True))
        return record

    def latest(self) -> DecisionScore:
        directory = self.root / "scoring" / "decisions"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No scored decisions exist; run score-decisions first")
        return DecisionScore.model_validate_json(files[-1].read_text(encoding="utf-8"))

    def recent(self, limit: int = 5) -> list[DecisionScore]:
        directory = self.root / "scoring" / "decisions"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        return [DecisionScore.model_validate_json(path.read_text(encoding="utf-8")) for path in reversed(files[-limit:])]

    def is_scored(self, decision_path: Path | str, event: int) -> bool:
        directory = self.root / "scoring" / "decisions"
        if not directory.exists():
            return False
        decision_path = str(decision_path)
        for path in directory.glob("*.json"):
            try:
                record = DecisionScore.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if record.gameweek == event and record.decision_path == decision_path:
                return True
        return False

    def _catalog_for_gameweek(self, gameweek: int) -> Path:
        directory = self.root / "normalized" / "current" / "odds_projections"
        candidates: list[tuple[datetime, Path]] = []
        for path in directory.glob("*.jsonl"):
            manifest = path.with_suffix(".manifest.json")
            if manifest.exists():
                candidates.append((datetime.fromisoformat(json.loads(manifest.read_text(encoding="utf-8"))["created_at"]), path))
        for _, path in sorted(candidates, reverse=True):
            try:
                rows = OddsProjectionStore(self.root).latest(path.name)
            except ValueError:
                continue
            if gameweek in {row.gameweek for row in rows}:
                return path
        raise FileNotFoundError(f"No odds projection catalog contains gameweek {gameweek}")

    def _event_snapshot_path(self, gameweek: int) -> Path:
        path, _ = SnapshotStore(self.root).latest_event_live(gameweek)
        return path

    def _event_actuals(self, gameweek: int) -> dict[int, float]:
        path, _ = SnapshotStore(self.root).latest_event_live(gameweek)
        document = json.loads(path.read_text(encoding="utf-8"))
        actuals: dict[int, float] = {}
        for element in document.get("payload", {}).get("elements", []):
            if isinstance(element, dict) and element.get("id") is not None:
                stats = element.get("stats") or {}
                actuals[int(element["id"])] = float(stats.get("total_points", 0))
        return actuals


class CompletedDecisionScorer:
    """Persist final FPL results and score each completed, committed gameweek once."""

    def __init__(self, root: Path, fpl_client: FplClient | None = None) -> None:
        self.root = root
        self.fpl_client = fpl_client or FplClient()

    def score_pending(self, season_id: str) -> list[str]:
        from aifpl.hermes import HermesManager

        scorer = DecisionScorer(self.root)
        latest_by_gameweek: dict[int, HermesDecision] = {}
        for decision in HermesManager(self.root).decisions():
            if decision.season_id == season_id:
                latest_by_gameweek.setdefault(decision.gameweek, decision)
        pending = [
            decision for decision in latest_by_gameweek.values()
            if not scorer.is_scored(decision.decision_path, decision.gameweek)
        ]
        if not pending:
            return []

        bootstrap = asyncio.run(self.fpl_client.fetch_bootstrap())
        finished_events = {
            event["id"] for event in bootstrap.get("events", [])
            if isinstance(event, dict) and isinstance(event.get("id"), int) and event.get("finished") is True
        }
        completed = [decision for decision in pending if decision.gameweek in finished_events]
        if not completed:
            return []

        snapshots = SnapshotStore(self.root)
        snapshots.save_bootstrap(bootstrap)
        score_paths: list[str] = []
        for decision in completed:
            snapshots.save_event_live(
                decision.gameweek,
                asyncio.run(self.fpl_client.fetch_event_live(decision.gameweek)),
            )
            score_paths.append(scorer.score(decision.decision_path, decision.gameweek).output_path)
        return score_paths
