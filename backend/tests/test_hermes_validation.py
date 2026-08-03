"""Tests for Hermes LLM output validation (the anti-hallucination layer)."""

import json

import pytest

from hermes.validation import HermesOutputError, extract_json_block, parse_adjustments

VALID_IDS = {1, 2, 3, 4, 5}


def make_output(**overrides):
    data = {
        "adjustments": [
            {"player_id": 1, "multiplier": 1.2, "action": "boost", "reason": "form"},
            {"player_id": 2, "multiplier": 0.7, "action": "fade", "reason": "rotation"},
        ],
        "captain_ranking": [1, 3],
        "triple_captain": {"play_now": False, "player_id": None, "target_gameweek": 34, "reason": "DGW"},
        "chip_advice": {"wildcard_now": False, "free_hit_now": False, "bench_boost_now": False,
                        "target_gameweeks": {"free_hit": 33}, "reason": "wait"},
        "differentials": [4],
        "transfer_priorities": [{"out_id": 5, "in_id": 1, "urgency": "this_week", "reason": "injury"}],
        "narrative": "Solid week ahead.",
        "confidence": "medium",
    }
    data.update(overrides)
    return json.dumps(data)


def test_valid_output_parses():
    result = parse_adjustments(make_output(), VALID_IDS, captain_candidates=[1, 3])
    assert len(result.adjustments) == 2
    assert result.captain_ranking == [1, 3]
    assert result.confidence == "medium"


def test_markdown_fenced_json_extracted():
    fenced = "Here you go:\n```json\n" + make_output() + "\n```"
    result = parse_adjustments(fenced, VALID_IDS, captain_candidates=[1, 3])
    assert result.narrative == "Solid week ahead."


def test_prose_wrapped_json_extracted():
    wrapped = "Sure! " + make_output() + " Hope that helps."
    assert json.loads(extract_json_block(wrapped))["confidence"] == "medium"


def test_hallucinated_adjustment_ids_dropped():
    raw = make_output(adjustments=[
        {"player_id": 999, "multiplier": 1.3, "action": "boost", "reason": "x"},
        {"player_id": 1, "multiplier": 1.1, "action": "boost", "reason": "y"},
    ])
    result = parse_adjustments(raw, VALID_IDS)
    assert [a.player_id for a in result.adjustments] == [1]


def test_captain_outside_candidate_list_dropped():
    raw = make_output(captain_ranking=[1, 2, 3])
    result = parse_adjustments(raw, VALID_IDS, captain_candidates=[1, 3])
    assert result.captain_ranking == [1, 3]


def test_out_of_range_multiplier_clamped():
    raw = make_output(adjustments=[
        {"player_id": 1, "multiplier": 3.0, "action": "boost", "reason": "x"},
        {"player_id": 2, "multiplier": 0.1, "action": "fade", "reason": "y"},
    ])
    result = parse_adjustments(raw, VALID_IDS)
    assert result.adjustments[0].multiplier == 1.5
    assert result.adjustments[1].multiplier == 0.5


def test_garbage_raises_for_repair():
    with pytest.raises(HermesOutputError):
        parse_adjustments("I cannot help with that.", VALID_IDS)


def test_invalid_json_raises():
    with pytest.raises(HermesOutputError):
        parse_adjustments('{"adjustments": [unclosed', VALID_IDS)


def test_fully_hallucinated_output_raises():
    raw = json.dumps({
        "adjustments": [{"player_id": 999, "multiplier": 1.2, "action": "boost", "reason": "x"}],
        "captain_ranking": [998],
        "narrative": "",
        "confidence": "high",
    })
    with pytest.raises(HermesOutputError):
        parse_adjustments(raw, VALID_IDS, captain_candidates=[1, 3])


def test_truncated_json_mid_value_repaired():
    # Cut off inside an incomplete value: that partial key is dropped,
    # but the complete preceding fields are salvaged.
    truncated = '{"items": [{"headline": "A", "impact": "out"}, {"headline": "B", "imp'
    parsed = json.loads(extract_json_block(truncated))
    assert len(parsed["items"]) == 2
    assert parsed["items"][0]["headline"] == "A"
    assert parsed["items"][1]["headline"] == "B"


def test_truncated_json_mid_key_repaired():
    # Cut off right after a complete array element + trailing comma
    truncated = '{"items": [{"headline": "A"}, {"headline": "B"}], "confidence": "hi'
    parsed = json.loads(extract_json_block(truncated))
    assert len(parsed["items"]) == 2


def test_invalid_transfer_ids_dropped():
    raw = make_output(transfer_priorities=[
        {"out_id": 5, "in_id": 999, "urgency": "soon", "reason": "x"},
        {"out_id": 2, "in_id": 3, "urgency": "watch", "reason": "y"},
    ])
    result = parse_adjustments(raw, VALID_IDS)
    assert len(result.transfer_priorities) == 1
    assert result.transfer_priorities[0].out_id == 2
