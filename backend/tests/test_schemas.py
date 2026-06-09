"""Schema round-trip tests for the agent layer."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from agents.schemas import (
    AgentReport,
    AvailabilitySignals,
    BettingSignals,
    DataSignals,
    MechanicsSignals,
    NewsItem,
    PlayerSnapshot,
    VariabilityEntry,
)


def test_agent_report_round_trip():
    payload = DataSignals(players=[PlayerSnapshot(
        id=1, name="Salah", team="LIV", team_id=12, position="MID",
        position_id=3, price=13.0, form=8.5, predicted_points=7.2,
        ownership=45.0,
    )])
    report = AgentReport(
        agent="data",
        gameweek=20,
        generated_at=datetime.utcnow(),
        summary="test",
        payload=payload.model_dump(mode="json"),
    )
    dumped = report.model_dump(mode="json")
    restored = AgentReport.model_validate(dumped)
    assert restored.agent == "data"
    inner = DataSignals.model_validate(restored.payload)
    assert inner.players[0].name == "Salah"


def test_report_status_is_constrained():
    with pytest.raises(ValidationError):
        AgentReport(
            agent="x", gameweek=1, generated_at=datetime.utcnow(),
            status="banana", payload={},
        )


def test_empty_payload_defaults():
    assert MechanicsSignals(current_gameweek=1, next_gameweek=2).fixture_load == []
    assert AvailabilitySignals().flagged == []
    assert BettingSignals().enabled is False


def test_news_item_incentive_fields():
    item = NewsItem(
        headline="Bruno chasing the assist record",
        impact="incentive",
        incentive_type="record_chase",
        behavioral_implication="likely to prioritize assists",
        sentiment=0.6,
    )
    assert item.incentive_type == "record_chase"
    with pytest.raises(ValidationError):
        NewsItem(headline="x", impact="not-an-impact")


def test_variability_entry_fields():
    e = VariabilityEntry(
        id=1, name="Haaland", team="MCI", position="FWD", n_gws=20,
        mean_pts=7.5, stddev=5.0, cv=0.66, ceiling_p90=17.0, floor_p10=2.0,
        haul_rate=0.35, blank_rate=0.2, consistency_score=0.6,
    )
    assert e.ceiling_p90 > e.floor_p10
