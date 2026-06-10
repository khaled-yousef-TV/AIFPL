"""Tests for the Hermes learning loop (pure evaluation math)."""

from hermes.evaluation import (
    TRUST_MAX,
    TRUST_MIN,
    apply_trust,
    build_calibration_profile,
    calibration_digest,
    compute_trust,
    evaluate_run,
)

ACTUAL = {1: 12, 2: 1, 3: 6, 4: 0, 5: 8}

ADJUSTMENTS = {
    "adjustments": [
        {"player_id": 1, "multiplier": 1.4, "action": "boost"},   # 12 pts -> hit
        {"player_id": 2, "multiplier": 0.6, "action": "fade"},    # 1 pt  -> hit
        {"player_id": 3, "multiplier": 1.2, "action": "boost"},   # 6 pts -> hit
        {"player_id": 4, "multiplier": 1.3, "action": "boost"},   # 0 pts -> miss
    ],
    "captain_ranking": [3, 1, 5],          # picked 3 (6 pts), best was 1 (12)
    "transfer_priorities": [{"out_id": 2, "in_id": 5, "urgency": "soon"}],
    "differentials": [5, 4],
}

SIGNALS = {
    "availability": {"payload": {"flagged": [
        {"id": 4, "status": "i"},          # scored 0 -> flag correct
        {"id": 1, "status": "d"},          # scored 12 -> flag wrong
    ]}},
    "variability": {"payload": {"players": [
        {"id": 1, "floor_p10": 2, "ceiling_p90": 15},   # 12 in band
        {"id": 2, "floor_p10": 2, "ceiling_p90": 15},   # 1 outside band
    ]}},
    "form": {"payload": {
        "hot_players": [{"id": 1}, {"id": 5}],          # avg 10
        "cold_players": [{"id": 2}, {"id": 4}],         # avg 0.5
    }},
}


def test_evaluate_run_full():
    ev = evaluate_run(ADJUSTMENTS, {"squad": {
        "starting_xi": [{"id": 1, "is_captain": True}, {"id": 3}],
        "predicted_points": 30,
    }}, ACTUAL, SIGNALS)

    # Adjustments: boosts 2/3, fades 1/1
    assert ev["adjustments"]["boost"]["hits"] == 2
    assert ev["adjustments"]["boost"]["total"] == 3
    assert ev["adjustments"]["fade"]["hit_rate"] == 1.0

    # Captaincy regret: best (12) - picked (6) = 6
    assert ev["captaincy"]["regret"] == 6
    assert ev["captaincy"]["picked_id"] == 3

    # Transfers: in (8) - out (1) = +7
    assert ev["transfers"][0]["delta"] == 7

    # Squad: captain doubled (24) + 6 = 30
    assert ev["squad"]["actual_points"] == 30

    # Agent calibration
    assert ev["agents"]["availability_flag_accuracy"] == 0.5
    assert ev["agents"]["variability_band_coverage"] == 0.5
    assert ev["agents"]["form_hot_avg"] > ev["agents"]["form_cold_avg"]


def test_evaluate_degraded_run_without_adjustments():
    ev = evaluate_run(None, None, ACTUAL, None)
    assert "adjustments" not in ev
    assert ev["scored_players"] == 5


def test_calibration_profile_and_trust():
    ev1 = evaluate_run(ADJUSTMENTS, None, ACTUAL)
    profile = build_calibration_profile([ev1, ev1])
    assert profile["runs_scored"] == 2
    assert profile["action_hit_rates"]["fade"] == 1.0
    assert profile["trust_weights"]["fade"] == TRUST_MAX
    # boost hit-rate 2/3 -> trust strictly between bounds
    assert TRUST_MIN < profile["trust_weights"]["boost"] < TRUST_MAX


def test_trust_bounds_and_application():
    assert compute_trust(None) == 1.0          # no data: no dampening
    assert compute_trust(0.0) == TRUST_MIN
    assert compute_trust(1.0) == TRUST_MAX
    # Full trust: multiplier unchanged; low trust shrinks toward 1.0
    assert apply_trust(1.5, 1.0) == 1.5
    assert apply_trust(1.5, 0.3) == 1.15
    assert apply_trust(0.6, 0.5) == 0.8
    assert apply_trust(1.0, 0.3) == 1.0        # neutral stays neutral


def test_calibration_digest_renders():
    ev = evaluate_run(ADJUSTMENTS, None, ACTUAL)
    profile = build_calibration_profile([ev])
    text = calibration_digest(profile, [{"category": "captaincy", "lesson": "Prefer ceiling in DGWs."}])
    assert "boost" in text and "captaincy" in text
    assert calibration_digest({"runs_scored": 0}, []) == ""
