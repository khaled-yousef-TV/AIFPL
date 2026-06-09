"""
Hermes orchestrator.

Pipeline: agents -> prompt -> LLM -> validated adjustments -> existing
optimizers -> result + narrative. On LLM failure the run degrades to
deterministic-only output (signals + unadjusted optimizer result) so the
caller never hard-fails on LLM flakiness.
"""

import logging
from typing import Dict, List, Optional

from agents.base import AgentContext
from agents.registry import run_agents
from agents.schemas import AgentReport

from .config import HermesConfig
from .llm_client import LLMClient
from .prompts import SYSTEM_PROMPT, assemble_user_prompt
from .schemas import HermesAdjustments
from .validation import HermesOutputError, parse_adjustments

logger = logging.getLogger(__name__)

RUN_TYPES = [
    "briefing", "squad", "wildcard", "free_hit",
    "triple_captain", "differentials", "my_team",
]

# Run types whose result includes an optimizer-built squad
SQUAD_RUN_TYPES = {"briefing", "squad", "wildcard", "free_hit", "my_team"}


class HermesOrchestrator:

    def __init__(self, config: HermesConfig, llm_client: Optional[LLMClient] = None):
        self.config = config
        self.llm = llm_client or (LLMClient(config) if config.llm_configured else None)

    # ---------- public API ----------

    def run(
        self,
        run_type: str,
        budget: float = 100.0,
        user_player_ids: Optional[List[int]] = None,
        top_n: int = 40,
        memory_digest: Optional[str] = None,
        progress_cb=None,
    ) -> Dict:
        """
        Execute a full Hermes run. Synchronous (call from a background thread).

        Returns dict with: gameweek, run_type, status, signals, adjustments,
        result, narrative, usage.
        """
        if run_type not in RUN_TYPES:
            raise ValueError(f"Unknown run_type '{run_type}'. Valid: {RUN_TYPES}")

        def progress(pct, msg):
            if progress_cb:
                progress_cb(pct, msg)

        from services.dependencies import get_dependencies
        deps = get_dependencies()

        next_gw = deps.fpl_client.get_next_gameweek()
        gameweek = next_gw.id if next_gw else 0

        # 1. Run the signal agents (news agent gets the LLM + web search)
        progress(10, "Running signal agents")
        from .search import load_search_provider
        ctx = AgentContext(
            fpl_client=deps.fpl_client,
            feature_engineer=deps.feature_engineer,
            predictor=deps.predictor_heuristic,
            betting_odds_client=deps.betting_odds_client,
            db_manager=deps.db_manager,
            gameweek=gameweek,
            top_n=top_n,
            user_player_ids=user_player_ids or [],
            llm_client=self.llm,
            search_provider=load_search_provider(),
        )
        reports = run_agents(ctx)

        captain_candidates = self._captain_candidates(reports, user_player_ids, run_type)
        valid_ids = {p.id for p in deps.fpl_client.get_players()}

        # 2. LLM synthesis (with one repair retry), degrading on failure
        adjustments = None
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        status = "completed"

        if self.llm is None:
            logger.warning("Hermes LLM not configured — producing deterministic-only run")
            status = "degraded"
        else:
            progress(50, "Hermes is reasoning over the signals")
            try:
                adjustments, usage = self._synthesize(
                    reports, run_type, gameweek, captain_candidates,
                    valid_ids, memory_digest,
                )
            except Exception as e:
                logger.error(f"Hermes LLM synthesis failed: {e}", exc_info=True)
                status = "degraded"

        # 3. Apply adjustments through the existing optimizers
        progress(75, "Optimizing with adjusted predictions")
        result = self._apply(run_type, adjustments, deps, budget, gameweek, reports)

        progress(95, "Finalizing")
        return {
            "gameweek": gameweek,
            "run_type": run_type,
            "status": status,
            "signals": {name: r.model_dump(mode="json") for name, r in reports.items()},
            "adjustments": adjustments.model_dump(mode="json") if adjustments else None,
            "result": result,
            "narrative": adjustments.narrative if adjustments else self._fallback_narrative(reports),
            "usage": usage,
            "model": self.config.model,
        }

    # ---------- internals ----------

    def _synthesize(
        self, reports, run_type, gameweek, captain_candidates, valid_ids, memory_digest,
    ):
        """One LLM call + one repair retry. Raises on double failure."""
        user_prompt = assemble_user_prompt(
            reports, run_type, gameweek,
            captain_candidates=captain_candidates,
            memory_digest=memory_digest,
        )

        raw, usage = self.llm.complete(SYSTEM_PROMPT, user_prompt)
        try:
            return parse_adjustments(raw, valid_ids, captain_candidates), usage
        except HermesOutputError as e:
            logger.warning(f"Hermes output invalid, attempting repair: {e}")
            repair_prompt = (
                user_prompt
                + "\n\n## Your previous response was invalid\n"
                + f"Error: {e}\n"
                + "Respond again with ONLY a valid JSON object fixing this error."
            )
            raw2, usage2 = self.llm.complete(SYSTEM_PROMPT, repair_prompt)
            total_usage = {
                "prompt_tokens": usage["prompt_tokens"] + usage2["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"] + usage2["completion_tokens"],
            }
            return parse_adjustments(raw2, valid_ids, captain_candidates), total_usage

    @staticmethod
    def _captain_candidates(
        reports: Dict[str, AgentReport],
        user_player_ids: Optional[List[int]],
        run_type: str,
    ) -> List[int]:
        """Top predicted players + variability high-ceiling picks (user squad only in my_team mode)."""
        candidates: List[int] = []

        data = reports.get("data")
        if data and data.payload.get("players"):
            players = data.payload["players"]
            if run_type == "my_team" and user_player_ids:
                pool = [p for p in players if p.get("in_user_team")]
            else:
                pool = players
            candidates.extend(p["id"] for p in pool[:12])

        var = reports.get("variability")
        if var and var.payload.get("captaincy_candidates") and run_type != "my_team":
            for pid in var.payload["captaincy_candidates"]:
                if pid not in candidates:
                    candidates.append(pid)

        return candidates[:20]

    def _apply(
        self,
        run_type: str,
        adjustments: Optional[HermesAdjustments],
        deps,
        budget: float,
        gameweek: int,
        reports: Dict[str, AgentReport],
    ) -> Dict:
        """Feed Hermes adjustments into the deterministic optimizers."""
        from services.squad_service import assemble_squad_result, compute_player_predictions

        result: Dict = {}
        names = {p.id: p.web_name for p in deps.fpl_client.get_players()}

        if run_type in SQUAD_RUN_TYPES:
            predictions = compute_player_predictions(deps.predictor_heuristic)

            locked, excluded = [], []
            if adjustments:
                multipliers = {}
                for adj in adjustments.adjustments:
                    if adj.action == "exclude":
                        excluded.append(adj.player_id)
                    elif adj.action == "lock":
                        locked.append(adj.player_id)
                        multipliers[adj.player_id] = adj.multiplier
                    else:
                        multipliers[adj.player_id] = adj.multiplier

                predictions = [
                    {**p, "predicted": p["predicted"] * multipliers.get(p["id"], 1.0)}
                    for p in predictions
                ]

            result["squad"] = assemble_squad_result(
                predictions, budget, "hermes", gameweek,
                locked_ids=locked or None, excluded_ids=excluded or None,
            )

        if adjustments:
            result["captain_ranking"] = [
                {"id": pid, "name": names.get(pid, "?")}
                for pid in adjustments.captain_ranking
            ]
            result["differentials"] = [
                {"id": pid, "name": names.get(pid, "?")}
                for pid in adjustments.differentials
            ]
            if adjustments.triple_captain:
                tc = adjustments.triple_captain.model_dump()
                tc["player_name"] = names.get(tc.get("player_id"), None)
                result["triple_captain"] = tc
            result["chip_advice"] = adjustments.chip_advice.model_dump()
            result["transfer_priorities"] = [
                {
                    **t.model_dump(),
                    "out_name": names.get(t.out_id, "?"),
                    "in_name": names.get(t.in_id, "?"),
                }
                for t in adjustments.transfer_priorities
            ]

        return result

    @staticmethod
    def _fallback_narrative(reports: Dict[str, AgentReport]) -> str:
        """Deterministic narrative when the LLM is unavailable: agent summaries."""
        lines = ["**Hermes LLM unavailable — deterministic signals only.**", ""]
        for name, r in reports.items():
            lines.append(f"- **{name}**: {r.summary}")
        return "\n".join(lines)
