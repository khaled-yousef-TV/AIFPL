import json
from datetime import datetime, timezone

import pytest

from aifpl.current import CurrentPlayerCatalogStore
from aifpl.player_evidence import PlayerEvidence, PlayerEvidenceStore, predicted_start_probabilities
from aifpl.player_evidence import _parse_external
from aifpl.snapshots import SnapshotStore


def bootstrap() -> dict:
    return {"teams": [{"id": 1, "name": "Club"}], "events": [], "elements": [{
        "id": 1, "web_name": "Starter", "first_name": "First", "second_name": "Starter",
        "element_type": 1, "team": 1, "now_cost": 50, "status": "d",
        "chance_of_playing_next_round": 75, "form": "0", "points_per_game": "4", "total_points": 100,
        "minutes": 900, "starts": 10, "expected_goals": "0", "expected_assists": "0",
        "expected_goal_involvements": "0", "expected_goals_conceded": "10",
        "news": "Minor doubt", "news_added": "2026-08-01T10:00:00Z",
        "selected_by_percent": "10", "ep_next": "3",
    }]}


def test_evidence_store_preserves_official_news_and_probabilities(tmp_path) -> None:
    SnapshotStore(tmp_path).save_bootstrap(bootstrap())
    players = CurrentPlayerCatalogStore(tmp_path).normalize_latest()

    catalog = PlayerEvidenceStore(tmp_path).build(__import__("pathlib").Path(players.output_path))
    records = PlayerEvidenceStore(tmp_path).latest(__import__("pathlib").Path(players.output_path))

    assert catalog.records == 3
    assert any(row.evidence_type == "official_news" and row.categorical_value == "Minor doubt" for row in records)
    assert any(row.evidence_type == "official_availability" and row.provider_probability == 0.75 for row in records)


def test_predicted_start_uses_highest_quality_explicit_probability() -> None:
    common = dict(source_record_id="1", player_id=1, evidence_type="predicted_start", categorical_value=None,
                  published_at="2026-08-01T10:00:00Z", fetched_at="2026-08-01T11:00:00Z", source_url=None)
    rows = [
        PlayerEvidence(provider="aggregator", provider_probability=0.9, source_class="aggregator", gameweek=1, season_id="2026-27", **common),
        PlayerEvidence(provider="club", provider_probability=0.7, source_class="official_club", gameweek=1, season_id="2026-27", **common),
    ]

    assert predicted_start_probabilities(rows, 1, "2026-27", datetime(2026, 8, 1, 12, tzinfo=timezone.utc)) == {(1, None): 0.7}


def test_external_evidence_rejects_future_publication() -> None:
    payload = [{"provider": "p", "source_record_id": "1", "player_id": 1,
                "evidence_type": "predicted_start", "provider_probability": .5,
                "published_at": "2026-08-02T00:00:00Z", "gameweek": 1,
                "season_id": "2026-27",
                "source_class": "aggregator"}]

    with pytest.raises(ValueError, match="later"):
        _parse_external(payload, "2026-08-01T00:00:00+00:00", None, {1})
