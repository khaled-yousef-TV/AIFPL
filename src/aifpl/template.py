from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Mapping

from pydantic import BaseModel, Field

from aifpl.artifacts import complete_artifact_paths, json_bytes, verify_artifact, write_immutable, write_manifest
from aifpl.game_state import ExposureState


TemplateStatus = Literal["CORE_TEMPLATE", "TEMPLATE", "SEMI_TEMPLATE", "DIFFERENTIAL", "PUNT"]


class OwnershipLandscape(BaseModel):
    """A timestamped ownership input; raw ownership is not treated as EO."""

    season_id: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")
    gameweek: int = Field(ge=1, le=38)
    fetched_at: datetime
    source: str = "manual"
    cohort_sizes: dict[str, int] = Field(default_factory=dict)
    overall_ownership: dict[int, float] = Field(default_factory=dict)
    engaged_ownership: dict[int, float] = Field(default_factory=dict)
    top_100k_ownership: dict[int, float] = Field(default_factory=dict)
    top_10k_ownership: dict[int, float] = Field(default_factory=dict)
    effective_ownership: dict[int, float] = Field(default_factory=dict)
    expected_captaincy: dict[int, float] = Field(default_factory=dict)


class PlayerTemplateState(BaseModel):
    player_id: int = Field(gt=0)
    overall_ownership: float | None = Field(default=None, ge=0, le=100)
    engaged_ownership: float | None = Field(default=None, ge=0, le=100)
    top_100k_ownership: float | None = Field(default=None, ge=0, le=100)
    top_10k_ownership: float | None = Field(default=None, ge=0, le=100)
    effective_ownership: float | None = Field(default=None, ge=0, le=300)
    expected_captaincy: float | None = Field(default=None, ge=0, le=100)
    template_score: float = Field(ge=0, le=100)
    template_status: TemplateStatus
    ownership_basis: str = "unavailable"


class TemplateCatalog(BaseModel):
    season_id: str
    gameweek: int
    source: str
    fetched_at: datetime
    players: list[PlayerTemplateState]
    landscape: OwnershipLandscape | None = None
    output_path: str = ""


def build_template_states(
    landscape: OwnershipLandscape,
    player_ids: Iterable[int] | None = None,
) -> list[PlayerTemplateState]:
    identifiers = set(player_ids or ())
    for mapping in (
        landscape.overall_ownership,
        landscape.engaged_ownership,
        landscape.top_100k_ownership,
        landscape.top_10k_ownership,
        landscape.effective_ownership,
        landscape.expected_captaincy,
    ):
        identifiers.update(mapping)
    states: list[PlayerTemplateState] = []
    weights = (
        ("overall_ownership", landscape.overall_ownership, 0.15),
        ("engaged_ownership", landscape.engaged_ownership, 0.20),
        ("top_100k_ownership", landscape.top_100k_ownership, 0.30),
        ("top_10k_ownership", landscape.top_10k_ownership, 0.35),
    )
    for player_id in sorted(identifiers):
        weighted = [value * weight for _, mapping, weight in weights if (value := mapping.get(player_id)) is not None]
        total_weight = sum(weight for _, mapping, weight in weights if player_id in mapping)
        if total_weight:
            score = sum(weighted) / total_weight
            basis = "cohort_ownership"
        elif player_id in landscape.effective_ownership:
            score = min(100.0, float(landscape.effective_ownership[player_id]))
            basis = "effective_ownership"
        else:
            score = 0.0
            basis = "unavailable"
        states.append(PlayerTemplateState(
            player_id=player_id,
            overall_ownership=_value(landscape.overall_ownership, player_id, 100),
            engaged_ownership=_value(landscape.engaged_ownership, player_id, 100),
            top_100k_ownership=_value(landscape.top_100k_ownership, player_id, 100),
            top_10k_ownership=_value(landscape.top_10k_ownership, player_id, 100),
            effective_ownership=_value(landscape.effective_ownership, player_id, 300),
            expected_captaincy=_value(landscape.expected_captaincy, player_id, 100),
            template_score=round(max(0.0, min(100.0, score)), 4),
            template_status=template_status(score),
            ownership_basis=basis,
        ))
    return states


def build_template_catalog(
    landscape: OwnershipLandscape,
    player_ids: Iterable[int] | None = None,
) -> TemplateCatalog:
    return TemplateCatalog(
        season_id=landscape.season_id,
        gameweek=landscape.gameweek,
        source=landscape.source,
        fetched_at=landscape.fetched_at,
        players=build_template_states(landscape, player_ids),
        landscape=landscape,
    )


def template_status(score: float) -> TemplateStatus:
    if score >= 70:
        return "CORE_TEMPLATE"
    if score >= 45:
        return "TEMPLATE"
    if score >= 25:
        return "SEMI_TEMPLATE"
    if score >= 10:
        return "DIFFERENTIAL"
    return "PUNT"


def target_cohort_eo(
    player_id: int,
    state: PlayerTemplateState | None,
    raw_ownership: float | None = None,
) -> tuple[float | None, str]:
    if state is not None and state.effective_ownership is not None:
        return state.effective_ownership, "effective_ownership"
    if state is not None and state.expected_captaincy is not None:
        base = state.engaged_ownership or state.overall_ownership
        if base is not None:
            return min(300.0, base + state.expected_captaincy), "ownership_plus_captaincy_proxy"
    if state is not None and state.engaged_ownership is not None:
        return state.engaged_ownership, "engaged_ownership_proxy"
    if state is not None and state.overall_ownership is not None:
        return state.overall_ownership, "overall_ownership_proxy"
    if raw_ownership is not None:
        return raw_ownership, "selected_by_percent_proxy"
    return None, "unavailable"


def build_exposure_states(
    players: Iterable[object],
    squad_ids: set[int],
    captain_id: int | None = None,
    triple_captain: bool = False,
    template_states: Mapping[int, PlayerTemplateState] | None = None,
) -> list[ExposureState]:
    template_states = template_states or {}
    exposures: list[ExposureState] = []
    for player in players:
        player_id = int(_read(player, "player_id", _read(player, "id", 0)))
        if player_id <= 0:
            continue
        raw_ownership = _optional_float(_read(player, "selected_by_percent", None))
        template = template_states.get(player_id)
        field_eo, basis = target_cohort_eo(player_id, template, raw_ownership)
        my_exposure = 100.0 if player_id in squad_ids else 0.0
        if player_id == captain_id and player_id in squad_ids:
            my_exposure += 200.0 if triple_captain else 100.0
        net = round(my_exposure - field_eo, 4) if field_eo is not None else None
        projected = _optional_float(_read(player, "projected_points", None))
        swing = round(abs(net) * projected / 100, 4) if net is not None and projected is not None else None
        captaincy = template.expected_captaincy if template is not None else None
        exposures.append(ExposureState(
            player_id=player_id,
            my_exposure=round(my_exposure, 4),
            target_cohort_eo=field_eo,
            net_exposure=net,
            rank_swing_potential=swing,
            captaincy_eo=captaincy,
            basis=basis,
        ))
    return exposures


def coverage(states: Iterable[PlayerTemplateState], player_ids: Iterable[int]) -> float | None:
    selected_ids = set(player_ids)
    selected = [state.template_score for state in states if state.player_id in selected_ids]
    if not selected:
        return None
    return round(sum(selected) / len(selected) / 100, 4)


class TemplateCatalogStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, catalog: TemplateCatalog) -> Path:
        created_at = catalog.fetched_at.astimezone(timezone.utc)
        path = self.root / "template" / catalog.season_id / f"gw{catalog.gameweek}.{created_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        persisted = catalog.model_copy(update={"output_path": str(path)})
        write_immutable(path, json_bytes(persisted.model_dump(mode="json"), pretty=True))
        write_manifest(
            self.root,
            path,
            artifact_type="template_catalog",
            created_at=created_at.isoformat(),
            record_count=len(catalog.players),
            sources={},
            parameters={"season_id": catalog.season_id, "gameweek": catalog.gameweek, "source": catalog.source},
        )
        return path

    def latest(self, season_id: str | None = None, gameweek: int | None = None) -> TemplateCatalog:
        directory = self.root / "template"
        paths = [path for path in directory.glob("*/*.json") if not path.name.endswith(".manifest.json")] if directory.exists() else []
        if season_id is not None:
            paths = [path for path in paths if path.parent.name == season_id]
        paths = complete_artifact_paths(sorted(paths))
        if gameweek is not None:
            matching = []
            for path in paths:
                try:
                    catalog = TemplateCatalog.model_validate_json(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if catalog.gameweek == gameweek:
                    matching.append((path, catalog))
            if not matching:
                raise FileNotFoundError(f"No template catalog exists for GW{gameweek}")
            path, catalog = matching[-1]
            verify_artifact(self.root, path)
            return catalog
        if not paths:
            raise FileNotFoundError("No template catalog exists; save a TemplateCatalog first")
        path = paths[-1]
        verify_artifact(self.root, path)
        return TemplateCatalog.model_validate_json(path.read_text(encoding="utf-8"))


def _value(values: Mapping[int, float], player_id: int, cap: float) -> float | None:
    if player_id not in values:
        return None
    value = float(values[player_id])
    if not math.isfinite(value) or not 0 <= value <= cap:
        raise ValueError(f"Ownership value for player {player_id} is outside 0..{cap}")
    return round(value, 4)


def _read(value: object, name: str, default: object) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
