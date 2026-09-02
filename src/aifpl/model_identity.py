from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path


MODEL_SPEC_VERSION = "aifpl-scoring-spec-2026-27-v2-rank-layer"
MODEL_SOURCE_FILES = (
    "model_identity.py",
    "config.py",
    "current.py",
    "current_projections.py",
    "fixtures.py",
    "fixture_projections.py",
    "odds.py",
    "market_odds.py",
    "odds_matching.py",
    "odds_projections.py",
    "xg_projections.py",
    "live_calibration.py",
    "transfer_awareness.py",
    "player_evidence.py",
    "market_signals.py",
    "scoring.py",
    "rules.py",
    "chips.py",
    "game_state.py",
    "template.py",
    "strategy_policy.py",
    "rank_utility.py",
    "captaincy_strategy.py",
    "objective_accounting.py",
    "optimizer.py",
    "horizon_transfers.py",
    "transfers.py",
    "account.py",
    "fpl.py",
)


def source_hashes() -> dict[str, str]:
    source_dir = Path(__file__).parent
    hashes: dict[str, str] = {}
    for name in MODEL_SOURCE_FILES:
        path = source_dir / name
        try:
            hashes[name] = sha256(path.read_bytes()).hexdigest()
        except OSError:
            hashes[name] = "missing"
    return hashes


def model_identity() -> dict[str, object]:
    return {
        "model_spec_version": MODEL_SPEC_VERSION,
        "deployed_commit": os.environ.get("AIFPL_DEPLOYED_COMMIT", "unknown"),
        "source_hashes": source_hashes(),
    }
