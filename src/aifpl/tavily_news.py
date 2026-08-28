from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx

from aifpl.artifacts import json_bytes, jsonl_bytes, verify_artifact, write_immutable, write_manifest
from aifpl.config import TavilyNewsSettings, http_retry_settings, tavily_news_settings
from aifpl.current import CurrentPlayer
from aifpl.player_evidence import PlayerEvidence
from aifpl.retry import retry_sync
from aifpl.security import redact_secrets


TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_NEWS_METHOD = "tavily_player_news_v1"
NewsImpact = Literal["clear", "watch", "doubt", "unlikely", "out"]


@dataclass(frozen=True)
class TavilyNewsArticle:
    title: str
    url: str
    content: str
    score: float
    published_at: str | None
    domain: str
    relevant: bool
    impact: NewsImpact
    confidence: float
    source_class: str


@dataclass(frozen=True)
class PlayerNewsAssessment:
    player_id: int
    player_name: str
    query: str
    queried_at: str
    status: Literal["clear", "watch", "adjusted"]
    start_probability_cap: float | None
    confidence: float
    rationale: str
    articles: list[TavilyNewsArticle]


@dataclass(frozen=True)
class TavilyNewsCatalog:
    status: Literal["disabled", "ready", "partial"]
    queried_player_ids: list[int]
    actionable_player_ids: list[int]
    assessments: list[PlayerNewsAssessment]
    evidence_records: list[PlayerEvidence]
    output_path: str | None
    raw_paths: list[str]
    errors: list[str]

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "queried_player_ids": self.queried_player_ids,
            "actionable_player_ids": self.actionable_player_ids,
            "assessments": [asdict(assessment) for assessment in self.assessments],
            "output_path": self.output_path,
            "raw_paths": self.raw_paths,
            "errors": self.errors,
        }


class TavilyNewsError(RuntimeError):
    pass


class TavilyClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int) -> dict[str, object]:
        def request() -> httpx.Response:
            response = httpx.post(
                TAVILY_SEARCH_URL,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "query": query,
                    "topic": "news",
                    "search_depth": "basic",
                    "max_results": max_results,
                    "time_range": "week",
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_usage": True,
                },
                timeout=20,
            )
            response.raise_for_status()
            return response

        try:
            payload = retry_sync(request, http_retry_settings()).json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TavilyNewsError(redact_secrets(f"Tavily news search failed: {type(exc).__name__}")) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise TavilyNewsError("Tavily news search returned an invalid response")
        return payload


class TavilyNewsStore:
    def __init__(
        self,
        root: Path,
        client: TavilyClient | None = None,
        settings: TavilyNewsSettings | None = None,
    ) -> None:
        self.root = root
        self.settings = settings or tavily_news_settings()
        self.client = client or (TavilyClient(self.settings.api_key) if self.settings.api_key else None)

    def research(
        self,
        players: list[CurrentPlayer],
        player_ids: list[int],
        gameweek: int,
        season_id: str,
        query_kind: Literal["owned", "candidate"] = "owned",
    ) -> TavilyNewsCatalog:
        if not self.settings.enabled or self.client is None:
            return TavilyNewsCatalog("disabled", [], [], [], [], None, [], [])
        players_by_id = {player.id: player for player in players}
        unique_ids = list(dict.fromkeys(player_ids))
        limit = 15 if query_kind == "owned" else self.settings.max_candidate_players
        selected = [players_by_id[player_id] for player_id in unique_ids if player_id in players_by_id][:limit]
        now = datetime.now(timezone.utc)
        assessments: list[PlayerNewsAssessment] = []
        evidence_records: list[PlayerEvidence] = []
        raw_paths: list[str] = []
        errors: list[str] = []
        for player in selected:
            query = _player_query(player)
            try:
                payload, raw_path = self._search(player, query, now)
                raw_paths.append(str(raw_path))
                assessment = _assess_player(player, query, payload, now, self.settings)
                assessments.append(assessment)
                evidence_records.extend(_assessment_evidence(assessment, gameweek, season_id, now))
            except TavilyNewsError as exc:
                errors.append(f"{player.id}: {exc}")
        output_path = self._write_catalog(assessments, raw_paths, errors, now, query_kind)
        actionable = [
            assessment.player_id
            for assessment in assessments
            if assessment.start_probability_cap is not None
            and assessment.start_probability_cap <= self.settings.start_probability_threshold
        ]
        return TavilyNewsCatalog(
            "partial" if errors else "ready",
            [player.id for player in selected],
            actionable,
            assessments,
            evidence_records,
            str(output_path),
            raw_paths,
            errors,
        )

    def latest_payload(self) -> dict[str, object]:
        directory = self.root / "normalized" / "current" / "tavily_news"
        paths = sorted(directory.glob("*.jsonl")) if directory.exists() else []
        if not paths:
            raise FileNotFoundError("No Tavily news research exists")
        path = paths[-1]
        verify_artifact(self.root, path, require_manifest=True)
        return {
            "output_path": str(path),
            "assessments": [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()],
        }

    def _search(self, player: CurrentPlayer, query: str, now: datetime) -> tuple[dict[str, object], Path]:
        cached = self._cached_path(player.id, now)
        if cached is not None:
            document = json.loads(cached.read_text(encoding="utf-8"))
            payload = document.get("payload")
            if isinstance(payload, dict):
                return payload, cached
        payload = self.client.search(query, self.settings.max_results)
        path = self._raw_path(player.id, now)
        write_immutable(path, json_bytes({
            "player_id": player.id,
            "query": query,
            "fetched_at": now.isoformat(),
            "payload": payload,
        }, pretty=True))
        write_manifest(
            self.root,
            path,
            artifact_type="tavily_news_search",
            created_at=now.isoformat(),
            record_count=len(payload.get("results", [])),
            sources={},
            methodology=TAVILY_NEWS_METHOD,
            parameters={"player_id": player.id, "max_results": self.settings.max_results},
        )
        return payload, path

    def _cached_path(self, player_id: int, now: datetime) -> Path | None:
        directory = self.root / "raw" / "tavily" / "player_news" / str(player_id)
        for path in reversed(sorted(directory.glob("*.json")) if directory.exists() else []):
            try:
                verify_artifact(self.root, path, require_manifest=True)
                fetched_at = datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["fetched_at"])
            except (OSError, ValueError, KeyError):
                continue
            if now - fetched_at <= timedelta(hours=self.settings.cache_hours):
                return path
        return None

    def _raw_path(self, player_id: int, now: datetime) -> Path:
        return self.root / "raw" / "tavily" / "player_news" / str(player_id) / f"{now.strftime('%Y%m%dT%H%M%S%fZ')}.json"

    def _write_catalog(
        self,
        assessments: list[PlayerNewsAssessment],
        raw_paths: list[str],
        errors: list[str],
        now: datetime,
        query_kind: str,
    ) -> Path:
        output_path = self.root / "normalized" / "current" / "tavily_news" / f"{now.strftime('%Y%m%dT%H%M%S%fZ')}.{query_kind}.jsonl"
        write_immutable(output_path, jsonl_bytes(assessments))
        sources = {f"search_{index}": Path(path) for index, path in enumerate(raw_paths)}
        write_manifest(
            self.root,
            output_path,
            artifact_type="tavily_player_news",
            created_at=now.isoformat(),
            record_count=len(assessments),
            sources=sources,
            methodology=TAVILY_NEWS_METHOD,
            parameters={"query_kind": query_kind, "errors": errors},
        )
        return output_path


def _player_query(player: CurrentPlayer) -> str:
    full_name = f"{player.first_name} {player.second_name}".strip() or player.name
    return f'"{full_name}" {player.club}'


def _assess_player(
    player: CurrentPlayer,
    query: str,
    payload: dict[str, object],
    now: datetime,
    settings: TavilyNewsSettings,
) -> PlayerNewsAssessment:
    articles = [_article(player, item) for item in payload.get("results", []) if isinstance(item, dict)]
    relevant = [article for article in articles if article.relevant]
    direct = [article for article in relevant if article.impact in ("out", "unlikely", "doubt")]
    domains = {article.domain for article in direct}
    official = [article for article in direct if article.source_class == "official_club"]
    reporters = [article for article in direct if article.source_class == "named_reporter"]
    cap: float | None = None
    confidence = max((article.confidence for article in direct), default=0.0)
    rationale = "No recent playing-time concern found."
    status: Literal["clear", "watch", "adjusted"] = "clear"
    if any(article.impact == "out" for article in direct) and (official or len(domains) >= 2):
        cap, status, rationale = 0.0, "adjusted", "Multiple credible reports indicate unavailability."
    elif any(article.impact == "unlikely" for article in direct) and (official or len(domains) >= 2 or reporters):
        cap, status, rationale = 0.45, "adjusted", "Credible reporting indicates a low likelihood of starting."
    elif sum(article.impact == "doubt" for article in direct) >= 2 and len(domains) >= 2:
        cap, status, rationale = 0.7, "adjusted", "Independent reports indicate meaningful start uncertainty."
    elif any(article.impact in ("out", "unlikely", "doubt", "watch") for article in relevant):
        status = "watch"
        confidence = max((article.confidence for article in relevant), default=0.0)
        rationale = "Recent news creates a watch item but does not meet the projection-adjustment threshold."
    return PlayerNewsAssessment(
        player.id,
        player.name,
        query,
        now.isoformat(),
        status,
        cap,
        round(confidence, 4),
        rationale,
        articles,
    )


def _article(player: CurrentPlayer, item: dict[str, object]) -> TavilyNewsArticle:
    title = str(item.get("title") or "")
    url = str(item.get("url") or "")
    content = str(item.get("content") or "")
    score = float(item.get("score") or 0.0)
    published_at = _published_at(item.get("published_date"))
    domain = urlparse(url).netloc.casefold().removeprefix("www.")
    source_class, base_confidence = _source_quality(domain)
    mentions = _player_sentences(player, f"{title}. {content}")
    context = " ".join(mentions).casefold()
    relevant = bool(mentions)
    impact: NewsImpact = "clear"
    if relevant and _contains(context, ("ruled out", "will miss", "suspended", "not available", "out injured")):
        impact = "out"
    elif relevant and _contains(context, ("unlikely to start", "expected to be benched", "set to be dropped", "will not start", "not expected to start")):
        impact = "unlikely"
    elif relevant and _contains(context, ("doubt", "fitness concern", "late test", "may not start", "could be rested")):
        impact = "doubt"
    elif relevant and _contains(
        f"{title}. {content}".casefold(),
        ("rotation", "competition for places", "replacement", "new signing", "transfer", "future cannot",
         "monitoring", "interested", "linked with", "target"),
    ):
        impact = "watch"
    confidence = base_confidence if impact != "watch" else min(base_confidence, 0.65)
    if score >= 0.8:
        confidence = min(1.0, confidence + 0.05)
    return TavilyNewsArticle(
        title,
        url,
        content,
        round(score, 6),
        published_at,
        domain,
        relevant,
        impact,
        round(confidence, 4),
        source_class,
    )


def _assessment_evidence(
    assessment: PlayerNewsAssessment,
    gameweek: int,
    season_id: str,
    now: datetime,
) -> list[PlayerEvidence]:
    relevant = [article for article in assessment.articles if article.relevant]
    if not relevant:
        return []
    primary = max(relevant, key=lambda article: article.confidence)
    fingerprint = sha256(
        json.dumps(asdict(assessment), sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()[:16]
    source_class = "official_club" if any(article.source_class == "official_club" for article in relevant) else (
        "named_reporter" if any(article.source_class == "named_reporter" for article in relevant) else "aggregator"
    )
    records = [PlayerEvidence(
        provider="tavily_news",
        source_record_id=f"tavily-watch:{assessment.player_id}:{fingerprint}",
        player_id=assessment.player_id,
        evidence_type="rotation_assessment",
        categorical_value=assessment.rationale,
        provider_probability=None,
        published_at=now.isoformat(),
        fetched_at=now.isoformat(),
        source_url=primary.url,
        source_class=source_class,
        gameweek=gameweek,
        season_id=season_id,
    )]
    if assessment.start_probability_cap is not None:
        records.append(PlayerEvidence(
            provider="tavily_consensus",
            source_record_id=f"tavily-start:{assessment.player_id}:{fingerprint}",
            player_id=assessment.player_id,
            evidence_type="predicted_start",
            categorical_value=assessment.rationale,
            provider_probability=assessment.start_probability_cap,
            published_at=now.isoformat(),
            fetched_at=now.isoformat(),
            source_url=primary.url,
            source_class=source_class,
            gameweek=gameweek,
            season_id=season_id,
        ))
    return records


def _player_sentences(player: CurrentPlayer, text: str) -> list[str]:
    aliases = {player.name.casefold()}
    full_name = f"{player.first_name} {player.second_name}".strip().casefold()
    if full_name:
        aliases.add(full_name)
    if player.second_name and len(player.second_name) >= 4:
        aliases.add(player.second_name.casefold())
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [sentence for sentence in sentences if any(alias in sentence.casefold() for alias in aliases)]


def _contains(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _source_quality(domain: str) -> tuple[str, float]:
    if domain.endswith(("liverpoolfc.com", "premierleague.com", "fpl.com")):
        return "official_club", 0.95
    if domain.endswith(("nytimes.com", "theathletic.com", "skysports.com", "espn.com", "bbc.co.uk", "reuters.com")):
        return "named_reporter", 0.8
    return "aggregator", 0.45


def _published_at(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()
