from datetime import datetime, timezone

import pytest

from aifpl.captaincy_strategy import choose_captain
from aifpl.current_projections import CurrentPlayerProjection
from aifpl.game_state import GameState, GameStateStore
from aifpl.horizon_transfers import HorizonSquadState, plan_horizon_transfers
from aifpl.optimizer import optimize_squad
from aifpl.odds_projections import OddsAdjustedGameweekProjection
from aifpl.rank_utility import rank_objective_adjustment
from aifpl.strategy_policy import derive_strategy_policy
from aifpl.template import (
    OwnershipLandscape,
    TemplateCatalogStore,
    build_exposure_states,
    build_template_catalog,
    target_cohort_eo,
)


def projection(
    player_id: int,
    position: str,
    points: float,
    ownership: float = 0.0,
) -> CurrentPlayerProjection:
    return CurrentPlayerProjection(
        player_id=player_id,
        player_name=f"Player {player_id}",
        position=position,
        club=f"Club {player_id}",
        cost=50,
        projected_points=points,
        availability_multiplier=1.0,
        selected_by_percent=ownership,
    )


def template(player_id: int, eo: float, score: float = 50.0):
    from aifpl.template import PlayerTemplateState

    return PlayerTemplateState(
        player_id=player_id,
        effective_ownership=eo,
        template_score=score,
        template_status="TEMPLATE",
    )


def rank_state(rank: int, target: int, *, gameweek: int = 27) -> GameState:
    return GameState(
        season_id="2026-27",
        gameweek=gameweek,
        overall_rank=rank,
        target_rank=target,
        free_transfers=2,
        bank=80,
        chips_remaining={"wildcard": 1, "triple_captain": 1},
        objective_mode="RANK_MODE",
    )


def test_game_state_derives_status_and_remaining_gameweeks() -> None:
    state = rank_state(184_000, 50_000)

    assert state.strategy_status == "BEHIND_TARGET"
    assert state.gameweeks_remaining == 11
    assert state.rank_gap_ratio == 3.68


def test_rank_mode_requires_rank_and_target() -> None:
    with pytest.raises(ValueError, match="overall_rank and target_rank"):
        GameState(
            season_id="2026-27", gameweek=27, free_transfers=1, bank=0,
            objective_mode="RANK_MODE",
        )


def test_template_uses_cohort_ownership_and_exposure_math() -> None:
    landscape = OwnershipLandscape(
        season_id="2026-27",
        gameweek=27,
        fetched_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        overall_ownership={1: 80.0, 2: 10.0},
        engaged_ownership={1: 90.0, 2: 20.0},
        effective_ownership={1: 155.0},
        expected_captaincy={1: 68.0},
    )
    catalog = build_template_catalog(landscape, [1, 2])
    states = {state.player_id: state for state in catalog.players}

    assert target_cohort_eo(1, states[1]) == (155.0, "effective_ownership")
    assert target_cohort_eo(2, states[2]) == (20.0, "engaged_ownership_proxy")
    exposure = build_exposure_states(
        [projection(1, "MID", 8.0), projection(2, "MID", 7.0)],
        {1},
        captain_id=1,
        template_states=states,
    )

    assert exposure[0].my_exposure == 200.0
    assert exposure[0].net_exposure == 45.0
    assert exposure[1].my_exposure == 0.0
    assert exposure[1].net_exposure == -20.0


def test_template_catalog_store_round_trips_output_path_and_manifest(tmp_path) -> None:
    landscape = OwnershipLandscape(
        season_id="2026-27", gameweek=1,
        fetched_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        overall_ownership={1: 40.0},
    )

    path = TemplateCatalogStore(tmp_path).save(build_template_catalog(landscape))
    loaded = TemplateCatalogStore(tmp_path).latest()

    assert loaded.output_path == str(path)
    assert path.with_suffix(".manifest.json").is_file()


def test_rank_policy_changes_with_rank_need_and_urgency() -> None:
    attack = derive_strategy_policy(rank_state(500_000, 50_000, gameweek=37), "RANK_MODE")
    protect = derive_strategy_policy(rank_state(20_000, 50_000, gameweek=37), "RANK_MODE")

    assert attack.status == "BEHIND_TARGET"
    assert attack.strategic_upside_weight > protect.strategic_upside_weight
    assert attack.urgency > 0.9
    assert protect.template_fade_risk_weight > attack.template_fade_risk_weight


def test_captaincy_uses_template_protection_when_ahead_and_leverage_when_behind() -> None:
    high_eo = projection(1, "MID", 8.4)
    low_eo = projection(2, "MID", 8.0)
    templates = {1: template(1, 168.0), 2: template(2, 18.0)}

    protect = choose_captain([high_eo, low_eo], rank_state(20_000, 50_000), templates)
    attack = choose_captain([high_eo, low_eo], rank_state(2_000_000, 50_000), templates)

    assert protect.captain.player_id == 1
    assert protect.options[0].classification == "SHIELD"
    assert attack.captain.player_id == 2
    assert attack.options[0].classification == "AGGRESSIVE_LEVERAGE"


def test_rank_adjustment_is_zero_without_rank_data() -> None:
    player = projection(1, "MID", 8.0)
    points_state = GameState(season_id="2026-27", gameweek=1, free_transfers=1, bank=0)

    assert rank_objective_adjustment(player, points_state, template(1, 150.0)) == 0.0


def test_rank_optimizer_returns_rank_mode_and_rank_aware_captain() -> None:
    candidates = [
        projection(1, "GK", 4.0), projection(2, "GK", 3.0),
        projection(3, "DEF", 5.0), projection(4, "DEF", 4.0), projection(5, "DEF", 3.0),
        projection(6, "DEF", 2.0), projection(7, "DEF", 1.0),
        projection(8, "MID", 8.4), projection(9, "MID", 8.0), projection(10, "MID", 6.0),
        projection(11, "MID", 5.0), projection(12, "MID", 4.0),
        projection(13, "FWD", 9.0), projection(14, "FWD", 8.0), projection(15, "FWD", 7.0),
    ]
    templates = {8: template(8, 168.0), 13: template(13, 18.0)}

    result = optimize_squad(
        candidates,
        budget=1000,
        objective_mode="RANK_MODE",
        game_state=rank_state(20_000, 50_000),
        template_states=templates,
    )

    assert result.objective_mode == "RANK_MODE"
    assert result.captain.player_id == 8


def test_rank_horizon_reports_rank_breakdown_and_rank_aware_vice_captain() -> None:
    players = [
        (1, "GK", "A", 4.0), (2, "GK", "B", 3.0),
        (3, "DEF", "A", 5.0), (4, "DEF", "B", 4.0), (5, "DEF", "C", 3.0),
        (6, "DEF", "D", 2.0), (7, "DEF", "E", 1.0),
        (8, "MID", "A", 8.4), (9, "MID", "B", 8.0), (10, "MID", "C", 6.0),
        (11, "MID", "D", 5.0), (12, "MID", "E", 4.0),
        (13, "FWD", "F", 9.0), (14, "FWD", "G", 8.0), (15, "FWD", "H", 7.0),
    ]
    rows = [
        OddsAdjustedGameweekProjection(
            player_id, f"Player {player_id}", position, club, 50, gameweek, 1, 1, points,
        )
        for gameweek in (1, 2, 3)
        for player_id, position, club, points in players
    ]
    templates = {8: template(8, 168.0), 13: template(13, 18.0)}

    plan = plan_horizon_transfers(
        rows,
        HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
        objective_mode="RANK_MODE",
        game_state=rank_state(20_000, 50_000),
        template_states=templates,
    )

    assert plan.objective_mode == "RANK_MODE"
    assert plan.objective_components["rank_adjustment"] > 0
    assert all(week.captain.player_id == 8 for week in plan.gameweeks)
    assert all(week.vice_captain is not None and week.vice_captain.player_id == 13 for week in plan.gameweeks)
