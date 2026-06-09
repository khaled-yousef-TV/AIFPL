"""
Agent registry.

Single place that knows all Hermes agents. run_agents() executes them
sequentially (each agent captures its own errors into its report).
"""

import logging
from typing import Dict, List, Optional

from .availability_agent import AvailabilityAgent
from .base import AgentContext
from .betting_agent import BettingAgent
from .data_agent import DataAgent
from .form_agent import FormAgent
from .mechanics_agent import MechanicsAgent
from .news_agent import NewsAgent
from .schemas import AgentReport
from .variability_agent import VariabilityAgent

logger = logging.getLogger(__name__)

# Six deterministic agents + the LLM-powered news agent (which degrades
# to FPL-news-only when ctx.llm_client/search_provider are unset).
AGENTS = {
    "data": DataAgent,
    "mechanics": MechanicsAgent,
    "availability": AvailabilityAgent,
    "form": FormAgent,
    "variability": VariabilityAgent,
    "betting": BettingAgent,
    "news": NewsAgent,
}


def run_agents(
    ctx: AgentContext,
    include: Optional[List[str]] = None,
) -> Dict[str, AgentReport]:
    """
    Run agents and collect their reports.

    Args:
        ctx: shared AgentContext
        include: agent names to run (None = all registered agents)

    Returns:
        Dict of agent name -> AgentReport
    """
    names = list(AGENTS.keys()) if include is None else [
        n for n in include if n in AGENTS
    ]

    reports: Dict[str, AgentReport] = {}
    for name in names:
        logger.info(f"Running agent '{name}'...")
        reports[name] = AGENTS[name]().run(ctx)
        logger.info(
            f"Agent '{name}' finished: status={reports[name].status} "
            f"({reports[name].elapsed_ms}ms)"
        )
    return reports
