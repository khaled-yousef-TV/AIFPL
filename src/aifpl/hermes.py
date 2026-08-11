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

from aifpl.artifacts import json_bytes, write_immutable
from aifpl.config import HermesSettings, hermes_settings
from aifpl.current import CurrentPlayerCatalogStore
from aifpl.health import SourceHealthChecker
from aifpl.horizon_transfers import HorizonSquadState, HorizonTransferPlan, plan_horizon_transfers
from aifpl.odds_projections import OddsProjectionStore
from aifpl.optimizer import OptimizedSquad
from aifpl.player_evidence import PlayerEvidenceStore
from aifpl.priors import PlayerPrior
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
    player_priors: list[PlayerPrior] = Field(default_factory=list)
    initialization_method: str = ""


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
    player_priors: list[PlayerPrior] = Field(default_factory=list)


class HermesRunResult(BaseModel):
    decision: HermesDecision
    state_path: str
    tool_steps: int


class HermesRunTranscript(BaseModel):
    created_at: datetime
    gameweek: int | None = None
    season_id: str | None = None
    outcome: Literal["succeeded", "failed", "unknown"] = "unknown"
    decision_path: str | None = None
    error: str | None = None
    tool_steps: int = 0
    messages: list[dict[str, Any]] = Field(default_factory=list)


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
        self.priors: list[PlayerPrior] = []

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
            "priors_enabled": self._priors_enabled(),
        }

    @staticmethod
    def _priors_enabled() -> bool:
        return os.environ.get("AIFPL_HERMES_PRIORS_ENABLED", "true").lower() in ("1", "true", "yes")

    def player_news(self, limit: int = 25) -> list[dict[str, Any]]:
        players = CurrentPlayerCatalogStore(self.root).latest_players()
        with_news = [player for player in players if player.news]
        with_news.sort(key=lambda player: (
            player.chance_of_playing_next_round if player.chance_of_playing_next_round is not None else 100
        ))
        return [
            {
                "id": player.id, "name": player.name, "position": player.position,
                "club": player.club, "news": player.news,
                "chance_of_playing": player.chance_of_playing_next_round,
            }
            for player in with_news[: max(1, min(int(limit), 200))]
        ]

    def team_fixtures(self, team: str) -> list[dict[str, Any]]:
        from aifpl.fixtures import CurrentFixtureCatalogStore
        from aifpl.teams import CurrentTeamCatalogStore

        fixtures = CurrentFixtureCatalogStore(self.root).latest()
        teams = {team.id: team for team in CurrentTeamCatalogStore(self.root).latest()}
        query = team.casefold().strip()
        matches = [team_obj for team_obj in teams.values() if query in team_obj.name.casefold() or query in team_obj.short_name.casefold()]
        if not matches:
            raise ValueError(f"Unknown team: {team}")
        team_obj = matches[0]
        upcoming: list[dict[str, Any]] = []
        for fixture in sorted(fixtures, key=lambda f: f.gameweek or 0):
            if fixture.finished or fixture.gameweek is None:
                continue
            if fixture.home_team_id == team_obj.id:
                opponent = teams.get(fixture.away_team_id)
                difficulty = fixture.home_difficulty
                venue = "home"
            elif fixture.away_team_id == team_obj.id:
                opponent = teams.get(fixture.home_team_id)
                difficulty = fixture.away_difficulty
                venue = "away"
            else:
                continue
            upcoming.append({
                "gameweek": fixture.gameweek, "opponent": opponent.short_name if opponent else "?",
                "venue": venue, "difficulty": difficulty, "kickoff": fixture.kickoff_time,
            })
            if len(upcoming) >= 3:
                break
        return upcoming

    def validate_prior(self, arguments: dict[str, Any]) -> PlayerPrior:
        from aifpl.priors import PlayerPrior, validate_prior_adjustment

        try:
            player_id = int(arguments["player_id"])
            adjustment = validate_prior_adjustment(arguments["adjustment"])
            reason = str(arguments.get("reason", "")).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid prior arguments: {exc}") from exc
        if len(reason) < 10:
            raise ValueError("reason must be at least 10 characters")
        names = {player.id: player.name for player in CurrentPlayerCatalogStore(self.root).latest_players()}
        if player_id not in names:
            raise ValueError(f"Unknown player id: {player_id}")
        return PlayerPrior(player_id=player_id, player_name=names[player_id], adjustment=adjustment, reason=reason)

    def set_prior(self, prior: PlayerPrior) -> list[dict[str, Any]]:
        self.priors = [existing for existing in self.priors if existing.player_id != prior.player_id]
        self.priors.append(prior)
        return [item.model_dump() for item in self.priors]

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

            records = DecisionScorer(self.root).recent(5)
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

    def initial_squad(self, strategy: HermesStrategy) -> tuple[OptimizedSquad, int]:
        from aifpl.priors import apply_priors

        rows = apply_priors(self._horizon_rows(1, strategy.planning_horizon), self.priors)
        preferred = {row.player_id for row in rows if row.player_name.casefold() in {name.casefold() for name in strategy.preferred_players}}
        plan = plan_horizon_transfers(
            rows, HorizonSquadState(player_ids=[], bank=0, free_transfers=0),
            preferred_player_ids=preferred,
            differential_appetite=strategy.differential_appetite,
            pre_season=True,
        )
        opening = plan.gameweeks[0]
        total_cost = sum(player.cost for player in opening.resulting_squad)
        # Keep the existing initial-decision contract while deriving it from the horizon plan.
        return OptimizedSquad(
            players=opening.resulting_squad, total_cost=total_cost, bank=opening.bank_after,
            projected_points=opening.projected_points, budget=total_cost + opening.bank_after,
            solver_status=plan.solver_status, methodology=plan.methodology,
            starting_xi=opening.starting_xi, captain=opening.captain,
        ), opening.gameweek

    def horizon_plan(self, state: HermesSquadState, strategy: HermesStrategy, target_gameweek: int) -> HorizonTransferPlan:
        from aifpl.priors import apply_priors

        rows = apply_priors(self._horizon_rows(target_gameweek, strategy.planning_horizon), self.priors)
        preferred = {row.player_id for row in rows if row.player_name.casefold() in {name.casefold() for name in strategy.preferred_players}}
        pre_season = target_gameweek == 1
        return plan_horizon_transfers(rows, HorizonSquadState(
            player_ids=state.player_ids, bank=state.bank, free_transfers=state.free_transfers,
            purchase_prices=state.purchase_prices,
        ), decision_hit_penalty=4 + strategy.hit_aversion * 4 + (1 - strategy.risk_tolerance) * 2,
            preferred_player_ids=preferred, differential_appetite=strategy.differential_appetite,
            pre_season=pre_season)

    def hold_week(self, state: HermesSquadState, horizon: int, target_gameweek: int) -> dict[str, Any]:
        rows = self._horizon_rows(target_gameweek, horizon)
        gameweek = target_gameweek
        by_id = {row.player_id: row for row in rows if row.gameweek == gameweek and row.player_id in state.player_ids}
        players = [CurrentPlayerProjection(row.player_id, row.player_name, row.position, row.club, row.cost, row.projected_points, 1.0, row.methodology) for row in by_id.values()]
        lineup = select_best_lineup(SquadRequest(players=[SquadPlayer(id=p.player_id, name=p.player_name, position=p.position, club=p.club, cost=p.cost, projected_points=p.projected_points) for p in players], budget=sum(p.cost for p in players) + state.bank))
        return {"gameweek": gameweek, "starting_ids": [p.id for p in lineup.starters], "captain_id": lineup.captain.id,
                "projected_points": lineup.projected_points, "methodology": players[0].methodology,
                "formation": f"{sum(p.position == 'DEF' for p in lineup.starters)}-{sum(p.position == 'MID' for p in lineup.starters)}-{sum(p.position == 'FWD' for p in lineup.starters)}"}

    def _latest_catalog_id(self, minimum_gameweeks: int, maximum_gameweeks: int) -> str:
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
            count = len({row.gameweek for row in rows})
            if minimum_gameweeks <= count <= maximum_gameweeks:
                return path.name
        raise FileNotFoundError(f"No odds projection catalog covers {minimum_gameweeks}-{maximum_gameweeks} gameweeks")

    def _horizon_rows(self, target_gameweek: int, horizon: int):
        directory = self.root / "normalized" / "current" / "odds_projections"
        candidates: list[tuple[datetime, Path]] = []
        for path in directory.glob("*.jsonl"):
            manifest = path.with_suffix(".manifest.json")
            if manifest.exists():
                candidates.append((datetime.fromisoformat(json.loads(manifest.read_text(encoding="utf-8"))["created_at"]), path))
        for _, path in sorted(candidates, reverse=True):
            rows = OddsProjectionStore(self.root).latest(path.name)
            available = sorted({row.gameweek for row in rows})
            if target_gameweek in available:
                selected = set(range(target_gameweek, min(max(available), target_gameweek + horizon - 1) + 1))
                return [row for row in rows if row.gameweek in selected]
        raise FileNotFoundError(f"No odds projection catalog contains gameweek {target_gameweek}")


class HermesManager:
    def __init__(self, root: Path, model: HermesModel | None = None, backend: HermesDecisionBackend | None = None) -> None:
        self.root = root
        self.model = model
        self.backend = backend or HermesDecisionBackend(root)
        self._strategy: HermesStrategy | None = None
        self._initial: OptimizedSquad | None = None
        self._horizon: HorizonTransferPlan | None = None
        self._hold: dict[str, Any] | None = None
        self._initial_gameweek: int | None = None
        self._expected_gameweek: int | None = None
        self._expected_season_id: str | None = None

    def run(self, expected_gameweek: int | None = None, expected_season_id: str | None = None) -> HermesRunResult:
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
            return self._run_unlocked()
        finally:
            flock(descriptor, LOCK_UN)
            os.close(descriptor)

    def run_current(self) -> HermesRunResult:
        from aifpl.scheduler import DeadlineScheduler

        schedule = DeadlineScheduler(self.root).status()
        return self.run(expected_gameweek=schedule.event, expected_season_id=schedule.season_id)

    def reinitialize_current(self) -> HermesRunResult | None:
        from aifpl.scheduler import DeadlineScheduler

        schedule = DeadlineScheduler(self.root).status()
        if schedule.event != 1 or schedule.missed:
            return None
        return self.reinitialize_opening_squad(schedule.event, schedule.season_id)

    def reinitialize_opening_squad(
        self, expected_gameweek: int, expected_season_id: str,
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
                or previous.initialization_method == "horizon_v1"
            ):
                return None
            self._strategy = previous.strategy
            self.backend.priors = previous.player_priors
            self._initial, self._initial_gameweek = self.backend.initial_squad(self._strategy)
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

    def _run_unlocked(self) -> HermesRunResult:
        if self.model is None:
            self.model = OpenAICompatibleHermesModel()
        previous = self.latest_state(optional=True)
        if previous is not None and self._expected_season_id and not previous.season_id:
            raise ValueError("Legacy Hermes state has no season ID; run hermes-migrate-state first")
        if previous is not None and self._expected_season_id and previous.season_id != self._expected_season_id:
            previous = None
        if previous is not None and self._expected_gameweek and self._expected_gameweek > previous.gameweek + 1:
            skipped = self._expected_gameweek - previous.gameweek - 1
            previous = previous.model_copy(update={
                "squad": previous.squad.model_copy(update={"free_transfers": min(5, previous.squad.free_transfers + skipped)})
            })
        self._strategy = previous.strategy if previous else None
        system = (
            "You are Hermes, an autonomous FPL manager playing your own experimental team. "
            "The backend is authoritative for all numbers and legality. Set your own stable strategy when none exists, "
            "inspect backend recommendations, then commit exactly one action. Never invent projections or player IDs. "
            "The get_horizon_plan output is computed for your own current squad: every player named in its out/in lists "
            "is a member of your squad and the transfers are directly actionable, including during pre-season "
            "(pre_season=true) when all transfers are free. Do not reject a plan because a name looks unfamiliar; "
            "re-read the squad and plan before deciding. "
            "Qualitative priors: the projections are purely quantitative. When the qualitative picture differs from the "
            "numbers (injury or rotation risk, fixture congestion, manager tendencies, bandwagon narratives), inspect "
            "get_player_news and get_team_fixtures, then set bounded per-player priors with set_player_prior "
            "(adjustment between -2 and +2 projected points per gameweek; each prior needs a concrete written reason). "
            "Priors are validated, audited, and applied by the backend before optimization; set them only when you have "
            "specific qualitative evidence, not as general sentiment. "
            "Concrete prior rules: a starter who is injured or doubtful for the next gameweek gets -1.0 to -2.0; a player "
            "returning from a long injury layoff gets -0.5 to -1.0; a player with a run of difficulty-2 fixtures gets "
            "+0.5 to +1.0; a player facing three difficulty-4/5 fixtures in a row gets -0.5 to -1.0. Set at most 5 priors. "
            "If the news tool returns no signal for a player you care about, you may skip a prior for them. "
            "Adaptive strategy: the context includes decision_history with your prior decisions' outcomes. Use it "
            "evidence-based: if avg_actual_minus_projected is negative across 2+ scored gameweeks, tighten your "
            "strategy (raise hit_aversion, lower differential_appetite, reduce risk_tolerance). If transfer_delta totals "
            "are strongly negative, raise hit_aversion further. If you consistently overperform, you may loosen "
            "slightly. Always explain the change in rationale."
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

    def latest_transcript(self) -> "HermesRunTranscript":
        directory = self.root / "hermes" / "runs"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("Hermes has no run transcripts yet")
        return HermesRunTranscript.model_validate_json(files[-1].read_text(encoding="utf-8"))

    def decisions(self, limit: int = 50) -> list["HermesDecision"]:
        directory = self.root / "hermes" / "decisions"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        return [
            HermesDecision.model_validate_json(path.read_text(encoding="utf-8"))
            for path in reversed(files[-limit:])
        ]

    def latest_state(self, optional: bool = False) -> HermesState | None:
        directory = self.root / "hermes" / "states"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        files = [path for path in files if _sibling_exists(self.root, path, "decisions")]
        if not files:
            if optional:
                return None
            raise FileNotFoundError("Hermes has no state yet; run hermes-run first")
        state = HermesState.model_validate_json(files[-1].read_text(encoding="utf-8"))
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

    def latest_decision(self) -> HermesDecision:
        directory = self.root / "hermes" / "decisions"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        files = [path for path in files if _sibling_exists(self.root, path, "states")]
        if not files:
            raise FileNotFoundError("Hermes has no decisions yet")
        return HermesDecision.model_validate_json(files[-1].read_text(encoding="utf-8"))

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
        if not files:
            raise FileNotFoundError("No Hermes state exists to migrate")
        legacy = HermesState.model_validate_json(files[-1].read_text(encoding="utf-8"))
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
        )
        state = legacy.model_copy(update={
            "squad": squad, "gameweek": gameweek, "season_id": season_id,
            "updated_at": now, "version": legacy.version + 1, "decision_path": str(decision_path),
        })
        write_immutable(state_path, json_bytes(state.model_dump(mode="json"), pretty=True))
        write_immutable(decision_path, json_bytes(decision.model_dump(mode="json"), pretty=True))
        return HermesRunResult(decision=decision, state_path=str(state_path), tool_steps=0)

    def _call_tool(self, name: str, arguments: dict[str, Any], previous: HermesState | None) -> Any:
        if name == "set_strategy":
            self._strategy = HermesStrategy.model_validate(arguments)
            return {"accepted": True, "strategy": self._strategy.model_dump()}
        if name == "get_player_news":
            if not self.backend._priors_enabled():
                raise ValueError("Qualitative priors are disabled by AIFPL_HERMES_PRIORS_ENABLED=false")
            return {"players": self.backend.player_news(int(arguments.get("limit", 25)))}
        if name == "get_team_fixtures":
            if not self.backend._priors_enabled():
                raise ValueError("Qualitative priors are disabled by AIFPL_HERMES_PRIORS_ENABLED=false")
            return {"team": arguments.get("team"), "fixtures": self.backend.team_fixtures(str(arguments.get("team", "")))}
        if name == "set_player_prior":
            if not self.backend._priors_enabled():
                raise ValueError("Qualitative priors are disabled by AIFPL_HERMES_PRIORS_ENABLED=false")
            prior = self.backend.validate_prior(arguments)
            priors = self.backend.set_prior(prior)
            return {"accepted": True, "active_priors": priors}
        if name == "get_initial_squad":
            if self._strategy is None:
                raise ValueError("Set strategy before requesting a squad")
            self._initial, self._initial_gameweek = self.backend.initial_squad(self._strategy)
            return _squad_summary(self._initial)
        if name == "get_horizon_plan":
            if previous is None:
                return {"error": "No existing squad; use get_initial_squad"}
            if self._strategy is None:
                raise ValueError("Set strategy before requesting a plan")
            target_gameweek = self._expected_gameweek or previous.gameweek + 1
            self._horizon = self.backend.horizon_plan(previous.squad, self._strategy, target_gameweek)
            self._hold = self.backend.hold_week(previous.squad, self._strategy.planning_horizon, target_gameweek)
            summary = _horizon_summary(self._horizon)
            summary["applies_to_current_squad"] = True
            if target_gameweek == 1:
                summary["pre_season"] = True
                summary["note"] = "Pre-season: all transfers are free; hit costs shown are informational."
            return summary
        if name == "commit_decision":
            return self._commit(arguments, previous)
        raise ValueError(f"Unknown Hermes tool: {name}")

    def _commit(
        self, arguments: dict[str, Any], previous: HermesState | None, model_name: str | None = None,
    ) -> HermesRunResult:
        if self._strategy is None:
            raise ValueError("Hermes must set a strategy before committing")
        action = arguments["action"]
        explanation = str(arguments["explanation"])
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
        elif action in ("execute_horizon", "hold"):
            if previous is None or self._horizon is None:
                raise ValueError("Hermes must inspect a horizon plan before committing")
            if action == "hold":
                assert self._hold is not None
                squad_ids, starting_ids = previous.squad.player_ids, self._hold["starting_ids"]
                captain_id, bank, outgoing, incoming, gameweek = self._hold["captain_id"], previous.squad.bank, [], [], self._hold["gameweek"]
                free, methodology, purchase_prices = min(5, previous.squad.free_transfers + 1), self._hold["methodology"], previous.squad.purchase_prices
            else:
                week = self._horizon.gameweeks[0]
                squad_ids = [player.player_id for player in week.resulting_squad]
                starting_ids = [player.player_id for player in week.starting_xi]
                captain_id, bank, outgoing, incoming, gameweek = week.captain.player_id, week.bank_after, [p.player_id for p in week.outgoing], [p.player_id for p in week.incoming], week.gameweek
                free = min(5, max(0, previous.squad.free_transfers - week.transfers_made) + 1)
                methodology = self._horizon.methodology
                costs = {player.player_id: player.cost for player in week.resulting_squad}
                purchase_prices = {player_id: previous.squad.purchase_prices.get(player_id, costs[player_id]) for player_id in squad_ids}
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
        decision = HermesDecision(
            action=action, gameweek=gameweek, squad=squad, captain_id=captain_id,
            starting_xi_ids=starting_ids, transfers_out=outgoing, transfers_in=incoming,
            explanation=explanation, strategy=self._strategy, model=decision_model,
            created_at=now, backend_methodology=methodology, decision_path=str(decision_path),
            state_path=str(state_path),
            season_id=season_id,
            player_priors=getattr(self.backend, "priors", []),
        )
        state = HermesState(
            strategy=self._strategy, squad=squad, captain_id=captain_id, starting_xi_ids=starting_ids,
            model=decision_model, updated_at=now, version=(previous.version + 1 if previous else 1),
            gameweek=gameweek, decision_path=str(decision_path),
            season_id=season_id,
            player_priors=getattr(self.backend, "priors", []),
            initialization_method="horizon_v1" if action == "adopt_initial" else (
                previous.initialization_method if previous else ""
            ),
        )
        write_immutable(state_path, json_bytes(state.model_dump(mode="json"), pretty=True))
        write_immutable(decision_path, json_bytes(decision.model_dump(mode="json"), pretty=True))
        return HermesRunResult(decision=decision, state_path=str(state_path), tool_steps=0)


def _sibling_exists(root: Path, artifact: Path, sibling_directory: str) -> bool:
    document = json.loads(artifact.read_text(encoding="utf-8"))
    reference = document.get("state_path" if sibling_directory == "states" else "decision_path")
    if not reference:
        return True
    if Path(reference).exists():
        return True
    return (root / "hermes" / sibling_directory / f"{artifact.stem}.json").exists()


def _tools() -> list[dict[str, Any]]:
    return [
        _tool("set_strategy", "Set your autonomous strategy", {"risk_tolerance": {"type": "number", "minimum": 0, "maximum": 1}, "hit_aversion": {"type": "number", "minimum": 0, "maximum": 1}, "differential_appetite": {"type": "number", "minimum": 0, "maximum": 1}, "planning_horizon": {"type": "integer", "minimum": 3, "maximum": 6}, "preferred_players": {"type": "array", "items": {"type": "string"}}, "rationale": {"type": "string"}}, ["risk_tolerance", "hit_aversion", "differential_appetite", "planning_horizon", "rationale"]),
        _tool("get_player_news", "Get players with injury/rotation news, lowest playing chance first", {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}, []),
        _tool("get_team_fixtures", "Get the next 3 fixtures for a team with opponent and difficulty", {"team": {"type": "string"}}, ["team"]),
        _tool("set_player_prior", "Set a bounded per-player projection adjustment with a reason (audited, applied before optimization)", {"player_id": {"type": "integer"}, "adjustment": {"type": "number", "minimum": -2, "maximum": 2}, "reason": {"type": "string", "minLength": 10}}, ["player_id", "adjustment", "reason"]),
        _tool("get_initial_squad", "Get the backend's best multi-gameweek initial squad", {}, []),
        _tool("get_horizon_plan", "Get the backend's transfer plan for the existing squad", {}, []),
        _tool("commit_decision", "Commit one backend-validated action", {"action": {"type": "string", "enum": ["adopt_initial", "execute_horizon", "hold"]}, "explanation": {"type": "string"}}, ["action", "explanation"]),
    ]


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}}


def _squad_summary(squad: OptimizedSquad) -> dict[str, Any]:
    return {"players": [{"id": p.player_id, "name": p.player_name, "position": p.position, "club": p.club, "points": p.projected_points} for p in squad.players], "starting_xi": [p.player_name for p in squad.starting_xi], "captain": squad.captain.player_name, "projected_points": squad.projected_points, "bank": squad.bank, "methodology": squad.methodology}


def _horizon_summary(plan: HorizonTransferPlan) -> dict[str, Any]:
    return {"solver_status": plan.solver_status, "total_net_points": plan.total_net_projected_points, "weeks": [{"gameweek": w.gameweek, "out": [p.player_name for p in w.outgoing], "in": [p.player_name for p in w.incoming], "hits": w.hit_cost, "captain": w.captain.player_name, "net_points": w.net_projected_points} for w in plan.gameweeks]}


def _season_id_for_now(now: datetime) -> str:
    start_year = now.year if now.month >= 7 else now.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"
