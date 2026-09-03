from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator

from aifpl.artifacts import complete_artifact_paths, json_bytes, verify_artifact, write_immutable, write_manifest


ObjectiveMode = Literal["POINTS_MODE", "RANK_MODE"]
StrategyStatus = Literal["BEHIND_TARGET", "ON_TRACK", "PROTECT_POSITION", "UNKNOWN"]
AccountSyncStatus = Literal["not_configured", "ready", "stale", "unavailable"]
ReconciliationStatus = Literal["not_checked", "matched", "mismatch", "not_comparable"]


class RankSnapshot(BaseModel):
    gameweek: int = Field(ge=1, le=38)
    overall_rank: int = Field(gt=0)
    target_rank: int | None = Field(default=None, gt=0)
    captured_at: datetime


class ExposureState(BaseModel):
    player_id: int = Field(gt=0)
    my_exposure: float = Field(ge=0, le=300)
    target_cohort_eo: float | None = Field(default=None, ge=0, le=300)
    net_exposure: float | None = None
    rank_swing_potential: float | None = Field(default=None, ge=0)
    captaincy_eo: float | None = Field(default=None, ge=0, le=100)
    basis: str = "unavailable"
    ownership_confidence: float | None = Field(default=None, ge=0, le=1)

    @computed_field
    @property
    def ownership_basis(self) -> str:
        return self.basis


class GameState(BaseModel):
    """Season-scoped state required to make a rank-aware FPL decision."""

    season_id: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")
    account_id: int | None = Field(default=None, gt=0)
    gameweek: int = Field(ge=1, le=38)
    rank_as_of_gameweek: int | None = Field(default=None, ge=1, le=38)
    decision_gameweek: int | None = Field(default=None, ge=1, le=38)
    overall_rank: int | None = Field(default=None, gt=0)
    target_rank: int | None = Field(default=None, gt=0)
    rank_history: list[RankSnapshot] = Field(default_factory=list)
    gameweeks_remaining: int | None = Field(default=None, ge=0, le=38)
    free_transfers: int = Field(ge=0, le=5)
    bank: int = Field(ge=0, description="Available funds in FPL tenths of a million")
    chips_remaining: dict[str, int] = Field(default_factory=dict)
    strategy_status: StrategyStatus = "UNKNOWN"
    risk_level: float = Field(default=0.5, ge=0, le=1)
    template_coverage: float | None = Field(default=None, ge=0, le=1)
    captain_template_coverage: float | None = Field(default=None, ge=0, le=1)
    objective_mode: ObjectiveMode = "POINTS_MODE"
    exposures: list[ExposureState] = Field(default_factory=list)
    account_sync_status: AccountSyncStatus = "not_configured"
    account_sync_warning: str | None = None
    account_reconciliation_status: ReconciliationStatus = "not_checked"
    account_reconciliation_source: str | None = None
    account_reconciliation_warning: str | None = None
    internal_squad_ids: list[int] | None = None
    internal_squad_gameweek: int | None = Field(default=None, ge=1, le=38)
    reconciliation_missing_player_ids: list[int] = Field(default_factory=list)
    reconciliation_unexpected_player_ids: list[int] = Field(default_factory=list)
    source: str = "manual"
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def derive_defaults(self) -> "GameState":
        if self.rank_as_of_gameweek is None:
            self.rank_as_of_gameweek = self.gameweek
        if self.decision_gameweek is None:
            self.decision_gameweek = self.gameweek
        if self.rank_as_of_gameweek > self.decision_gameweek:
            raise ValueError("rank_as_of_gameweek cannot be after decision_gameweek")
        if self.internal_squad_ids is not None:
            if len(self.internal_squad_ids) != 15 or len(set(self.internal_squad_ids)) != 15:
                raise ValueError("internal_squad_ids must contain 15 unique players")
            if any(player_id <= 0 for player_id in self.internal_squad_ids):
                raise ValueError("internal_squad_ids must contain positive player IDs")
            if self.internal_squad_gameweek is None:
                raise ValueError("internal_squad_gameweek is required with internal_squad_ids")
        elif self.internal_squad_gameweek is not None:
            raise ValueError("internal_squad_gameweek requires internal_squad_ids")
        self.gameweek = self.decision_gameweek
        if self.gameweeks_remaining is None:
            self.gameweeks_remaining = max(0, 38 - self.decision_gameweek)
        if self.strategy_status == "UNKNOWN" and self.overall_rank is not None and self.target_rank is not None:
            self.strategy_status = derive_strategy_status(self.overall_rank, self.target_rank)
        if self.objective_mode == "RANK_MODE" and not self.rank_data_available:
            raise ValueError("RANK_MODE requires overall_rank and target_rank")
        return self

    @computed_field
    @property
    def rank_data_available(self) -> bool:
        return self.overall_rank is not None and self.target_rank is not None

    @computed_field
    @property
    def rank_gap_ratio(self) -> float | None:
        if not self.rank_data_available:
            return None
        assert self.overall_rank is not None and self.target_rank is not None
        return round(self.overall_rank / self.target_rank, 6)


def derive_strategy_status(overall_rank: int, target_rank: int) -> StrategyStatus:
    if overall_rank <= target_rank * 0.75:
        return "PROTECT_POSITION"
    if overall_rank <= target_rank * 1.25:
        return "ON_TRACK"
    return "BEHIND_TARGET"


class GameStateStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, state: GameState, sources: dict[str, Path] | None = None) -> Path:
        updated_at = state.updated_at.astimezone(timezone.utc)
        path = self.root / "game_state" / state.season_id / f"gw{state.gameweek}.{updated_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        write_immutable(path, json_bytes(state.model_dump(mode="json"), pretty=True))
        write_manifest(
            self.root,
            path,
            artifact_type="game_state",
            created_at=updated_at.isoformat(),
            record_count=1,
            sources=sources or {},
            parameters={
                "season_id": state.season_id,
                "gameweek": state.gameweek,
                "rank_as_of_gameweek": state.rank_as_of_gameweek,
                "decision_gameweek": state.decision_gameweek,
                "objective_mode": state.objective_mode,
                "rank_data_available": state.rank_data_available,
                "account_sync_status": state.account_sync_status,
                "account_reconciliation_status": state.account_reconciliation_status,
                "internal_squad_gameweek": state.internal_squad_gameweek,
            },
        )
        return path

    def latest(self, season_id: str | None = None) -> GameState:
        directory = self.root / "game_state"
        paths = [path for path in directory.glob("*/*.json") if not path.name.endswith(".manifest.json")] if directory.exists() else []
        if season_id is not None:
            paths = [path for path in paths if path.parent.name == season_id]
        paths = complete_artifact_paths(sorted(paths))
        if not paths:
            raise FileNotFoundError("No game state exists; save a GameState first")
        path = paths[-1]
        verify_artifact(self.root, path)
        return GameState.model_validate_json(path.read_text(encoding="utf-8"))
