"""
Base classes for Hermes agents.

Agents are deterministic, synchronous modules that wrap existing
engines/services and emit an AgentReport. The base class handles
timing and error capture so a failing agent degrades instead of
breaking the whole signal run.
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from pydantic import BaseModel

from .schemas import AgentReport

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """Shared inputs for an agent run. Built once per signal run."""
    fpl_client: any
    feature_engineer: any
    predictor: any
    betting_odds_client: any = None
    db_manager: any = None
    gameweek: int = 0
    top_n: int = 40
    user_player_ids: List[int] = field(default_factory=list)
    # LLM + web search, used only by the news agent (None = degrade)
    llm_client: any = None
    search_provider: any = None


class BaseAgent(ABC):
    """Base agent: subclasses implement _build() returning (summary, payload, status)."""

    name: str = "base"
    version: str = "1"

    def run(self, ctx: AgentContext) -> AgentReport:
        """Run the agent, capturing timing and errors into the report envelope."""
        start = time.time()
        try:
            summary, payload, status = self._build(ctx)
            return AgentReport(
                agent=self.name,
                version=self.version,
                gameweek=ctx.gameweek,
                generated_at=datetime.utcnow(),
                status=status,
                elapsed_ms=int((time.time() - start) * 1000),
                summary=summary,
                payload=payload.model_dump(mode="json"),
            )
        except Exception as e:
            logger.error(f"Agent '{self.name}' failed: {e}", exc_info=True)
            return AgentReport(
                agent=self.name,
                version=self.version,
                gameweek=ctx.gameweek,
                generated_at=datetime.utcnow(),
                status="error",
                elapsed_ms=int((time.time() - start) * 1000),
                summary=f"Agent failed: {e}",
                payload={},
            )

    @abstractmethod
    def _build(self, ctx: AgentContext) -> Tuple[str, BaseModel, str]:
        """Build the agent's signals. Returns (summary, payload_model, status)."""
        raise NotImplementedError
