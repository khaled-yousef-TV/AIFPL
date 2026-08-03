"""
Validation for Hermes LLM output.

JSON extraction -> pydantic validation -> player-ID validation against
the FPL bootstrap. One repair retry with the error appended; on second
failure the run degrades to deterministic-only output upstream.
"""

import json
import logging
import re
from typing import Iterable, List, Optional, Set, Tuple

from pydantic import ValidationError

from .schemas import MULTIPLIER_MAX, MULTIPLIER_MIN, HermesAdjustments

logger = logging.getLogger(__name__)


class HermesOutputError(Exception):
    """Raised when LLM output can't be parsed/validated. Message is LLM-readable."""


def extract_json_block(text: str) -> str:
    """Extract the first top-level JSON object from LLM output."""
    # Strip common markdown fences first
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)

    start = text.find("{")
    if start == -1:
        raise HermesOutputError("No JSON object found in output.")

    # Walk to the matching closing brace
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    # Output was truncated mid-object (e.g. hit max_tokens). Attempt repair by
    # trimming to the last complete element and closing open brackets.
    repaired = _repair_truncated_json(text[start:], in_string)
    if repaired is not None:
        return repaired

    raise HermesOutputError("Unterminated JSON object in output.")


def _close_open_brackets(text: str) -> Optional[str]:
    """Close any still-open string/brackets in `text` and return it if it parses."""
    stack = []
    in_str = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()

    candidate = text + ('"' if in_str else "")
    for opener in reversed(stack):
        candidate += "}" if opener == "{" else "]"

    try:
        json.loads(candidate)
        return candidate
    except json.JSONDecodeError:
        return None


def _repair_truncated_json(fragment: str, in_string: bool) -> Optional[str]:
    """
    Best-effort repair of a JSON object truncated by a token limit.

    Strategy: repeatedly strip the trailing incomplete token (a dangling
    value, key, colon or comma) and try to close the remaining brackets,
    salvaging as much complete content as possible. Returns None if
    nothing parseable can be recovered.
    """
    text = fragment

    # If cut off inside a string literal, drop that partial token first
    if in_string:
        last_quote = text.rfind('"')
        if last_quote <= 0:
            return None
        text = text[:last_quote]

    # Iteratively trim trailing dangling tokens until something parses
    for _ in range(200):
        text = text.rstrip().rstrip(",").rstrip()
        if not text:
            return None

        repaired = _close_open_brackets(text)
        if repaired is not None:
            return repaired

        # Strip back past the last structural boundary and retry
        boundary = max(text.rfind("{"), text.rfind("["),
                       text.rfind("}"), text.rfind("]"), text.rfind(","))
        if boundary == -1:
            return None
        # If the boundary is a closer, keep it (it completes a container);
        # otherwise drop it and the dangling token after it.
        text = text[:boundary + 1] if text[boundary] in "}]" else text[:boundary]

    return None


def parse_adjustments(
    raw_text: str,
    valid_player_ids: Set[int],
    captain_candidates: Optional[Iterable[int]] = None,
) -> HermesAdjustments:
    """
    Parse and validate LLM output into HermesAdjustments.

    Raises HermesOutputError with an LLM-readable message suitable for a
    single repair retry.
    """
    block = extract_json_block(raw_text)

    try:
        data = json.loads(block)
    except json.JSONDecodeError as e:
        raise HermesOutputError(f"Invalid JSON: {e}")

    # Clamp multipliers BEFORE validation so borderline outputs survive
    for adj in data.get("adjustments", []) or []:
        if isinstance(adj, dict) and isinstance(adj.get("multiplier"), (int, float)):
            adj["multiplier"] = max(MULTIPLIER_MIN, min(MULTIPLIER_MAX, adj["multiplier"]))

    try:
        result = HermesAdjustments.model_validate(data)
    except ValidationError as e:
        raise HermesOutputError(f"Schema validation failed: {e}")

    # --- Player-ID validation (anti-hallucination) ---
    problems: List[str] = []

    bad_adjustments = [a.player_id for a in result.adjustments if a.player_id not in valid_player_ids]
    if bad_adjustments:
        problems.append(f"adjustments reference unknown player ids: {bad_adjustments}")
    result.adjustments = [a for a in result.adjustments if a.player_id in valid_player_ids]

    candidate_set = set(captain_candidates) if captain_candidates is not None else valid_player_ids
    bad_captains = [pid for pid in result.captain_ranking if pid not in candidate_set]
    if bad_captains:
        problems.append(f"captain_ranking contains ids outside the candidate list: {bad_captains}")
    result.captain_ranking = [pid for pid in result.captain_ranking if pid in candidate_set]

    if result.triple_captain and result.triple_captain.player_id is not None:
        if result.triple_captain.player_id not in candidate_set:
            problems.append(
                f"triple_captain.player_id {result.triple_captain.player_id} not in candidate list"
            )
            result.triple_captain.player_id = None

    result.differentials = [pid for pid in result.differentials if pid in valid_player_ids]

    valid_transfers = []
    for t in result.transfer_priorities:
        if t.out_id in valid_player_ids and t.in_id in valid_player_ids:
            valid_transfers.append(t)
        else:
            problems.append(f"transfer ({t.out_id} -> {t.in_id}) references unknown ids")
    result.transfer_priorities = valid_transfers

    # If the LLM hallucinated heavily AND we have nothing usable, force a retry
    if problems and not (result.adjustments or result.captain_ranking or result.narrative):
        raise HermesOutputError("; ".join(problems))

    if problems:
        logger.warning(f"Hermes output partially repaired: {'; '.join(problems)}")

    return result
