import json

from aifpl.current_projections import CurrentPlayerProjection
from aifpl.decision_support import player_metrics
from aifpl.ownership import apply_effective_ownership


def test_configured_effective_ownership_is_distinct_from_public_ownership(monkeypatch, tmp_path) -> None:
    source = tmp_path / "effective_ownership.json"
    source.write_text(json.dumps({"players": {"1": 125.0}}), encoding="utf-8")
    monkeypatch.setenv("AIFPL_EFFECTIVE_OWNERSHIP_FILE", str(source))
    player = CurrentPlayerProjection(1, "Player", "MID", "Club", 50, 6, 1.0, selected_by_percent=12)

    enriched = apply_effective_ownership([player])[0]
    metrics = player_metrics(enriched)

    assert enriched.effective_ownership_pct == 125
    assert metrics.ownership_pct == 12
    assert metrics.effective_ownership_pct == 125
    assert metrics.ownership_basis == "effective_ownership"
    assert metrics.differential_score == 0
