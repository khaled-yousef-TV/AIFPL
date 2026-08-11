#!/usr/bin/env python3
"""Heavy experiment: does qualitative priors + adaptive strategy change Hermes' teams?

Runs four scenarios, each in an isolated copy of the local data directory:
  A. control       - priors disabled (AIFPL_HERMES_PRIORS_ENABLED=false)
  B. priors        - news/fixtures tools + bounded per-player priors enabled
  C. adapt-bad     - priors enabled + negative decision_history (underperforming)
  D. adapt-good    - priors enabled + positive decision_history (overperforming)

Each scenario starts from a fresh state (no previous decisions) so the model
commits adopt_initial. Output is a JSON report written to the given path.
"""

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aifpl.hermes import HermesDecisionBackend, HermesManager, OpenAICompatibleHermesModel

NEGATIVE_HISTORY = {
    "rows": [
        {"gameweek": 1, "season_id": "2026-27", "action": "execute_horizon", "projected": 60.0, "actual": 51.0,
         "xi_actual": 49.0, "bench_actual": 2.0, "transfer_delta": -9.0, "captain_actual": 5.0},
        {"gameweek": 2, "season_id": "2026-27", "action": "execute_horizon", "projected": 62.0, "actual": 54.0,
         "xi_actual": 52.0, "bench_actual": 2.0, "transfer_delta": -7.0, "captain_actual": 4.0},
        {"gameweek": 3, "season_id": "2026-27", "action": "hold", "projected": 58.0, "actual": 53.0,
         "xi_actual": 51.0, "bench_actual": 2.0, "transfer_delta": 0.0, "captain_actual": 6.0},
    ],
    "summary": {
        "scored_gameweeks": 3, "avg_actual_minus_projected": -7.3,
        "total_transfer_delta": -16.0, "avg_captain_actual": 5.0,
    },
}

POSITIVE_HISTORY = {
    "rows": [
        {"gameweek": 1, "season_id": "2026-27", "action": "execute_horizon", "projected": 58.0, "actual": 66.0,
         "xi_actual": 64.0, "bench_actual": 2.0, "transfer_delta": 8.0, "captain_actual": 14.0},
        {"gameweek": 2, "season_id": "2026-27", "action": "execute_horizon", "projected": 60.0, "actual": 68.0,
         "xi_actual": 66.0, "bench_actual": 2.0, "transfer_delta": 6.0, "captain_actual": 12.0},
    ],
    "summary": {
        "scored_gameweeks": 2, "avg_actual_minus_projected": 8.0,
        "total_transfer_delta": 14.0, "avg_captain_actual": 13.0,
    },
}


class HistoryBackend(HermesDecisionBackend):
    def __init__(self, root: Path, history: dict) -> None:
        super().__init__(root)
        self._history = history

    def _decision_history(self) -> dict:
        return self._history


def prepare_copy(source: Path, tag: str) -> Path:
    target = Path("/tmp") / "aifpl-experiments" / tag
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    for directory in ("hermes/decisions", "hermes/states", "hermes/runs"):
        path = target / directory
        if path.exists():
            shutil.rmtree(path)
    return target


def run_scenario(tag: str, priors_enabled: bool, history: dict | None) -> dict:
    source = ROOT / "data"
    copy = prepare_copy(source, tag)
    env = dict(os.environ)
    env["AIFPL_HERMES_PRIORS_ENABLED"] = "true" if priors_enabled else "false"
    os.environ.update(env)

    backend = HistoryBackend(copy, history) if history is not None else HermesDecisionBackend(copy)
    manager = HermesManager(copy, model=OpenAICompatibleHermesModel(), backend=backend)
    result = manager.run(expected_gameweek=1, expected_season_id="2026-27")
    decision = result.decision
    names = {player.player_id: player.player_name for player in manager.backend.initial_squad(decision.strategy)[0].players}
    squad_ids = decision.squad.player_ids
    xi_ids = set(decision.starting_xi_ids)
    return {
        "tag": tag,
        "action": decision.action,
        "model": decision.model,
        "strategy": decision.strategy.model_dump(),
        "captain": names.get(decision.captain_id, decision.captain_id),
        "bank": decision.squad.bank,
        "priors": [prior.model_dump() for prior in decision.player_priors],
        "players": [
            {"id": pid, "starter": pid in xi_ids}
            for pid in squad_ids
        ],
        "explanation": decision.explanation,
    }


def main() -> None:
    report_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/aifpl-experiments/report.json"
    scenarios = [
        run_scenario("B_priors_v2", priors_enabled=True, history=None),
    ]
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scenarios, indent=2), encoding="utf-8")
    print(f"report written to {path}")


if __name__ == "__main__":
    main()
