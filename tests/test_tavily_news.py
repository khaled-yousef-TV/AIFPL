import json
from datetime import datetime, timedelta, timezone

import pytest

from aifpl.config import TavilyNewsSettings, tavily_news_settings
from aifpl.current import CurrentPlayer
from aifpl.player_evidence import PlayerEvidence
from aifpl.tavily_news import (
    TavilyNewsError,
    TavilyNewsStore,
    _article,
    _assess_player,
    _assessment_evidence,
    _player_sentences,
)


def player(identifier: int = 367, name: str = "Cody Gakpo", club: str = "Liverpool") -> CurrentPlayer:
    return CurrentPlayer(
        id=identifier, name=name, position="MID", club_id=14, club=club, cost=70,
        status="a", chance_of_playing_next_round=100, form=5.0, points_per_game=5.0,
        total_points=5, minutes=90, starts=1, expected_goals=0.4, expected_assists=0.2,
        expected_goal_involvements=0.6, expected_goals_conceded=1.0,
        first_name="Cody", second_name="Gakpo",
    )


def settings(**overrides) -> TavilyNewsSettings:
    values = {
        "api_key": "test-key", "enabled": True, "max_results": 5,
        "cache_hours": 6.0, "max_candidate_players": 5,
        "start_probability_threshold": 0.7,
    }
    values.update(overrides)
    return TavilyNewsSettings(**values)


def result(title: str, url: str, content: str, score: float = 0.7, published: str | None = None) -> dict:
    return {
        "title": title, "url": url, "content": content,
        "score": score, "published_date": published,
    }


def test_article_marks_ruled_out_as_out_impact() -> None:
    article = _article(player(), result(
        "Gakpo ruled out", "https://www.bbc.co.uk/sport/1",
        "Cody Gakpo has been ruled out of Liverpool's next match.", 0.85,
    ))

    assert article.relevant is True
    assert article.impact == "out"
    assert article.source_class == "named_reporter"
    assert article.confidence >= 0.8


def test_article_marks_playing_time_competition_as_rotation() -> None:
    article = _article(player(), result(
        "Barcola signing could shake up Liverpool attack", "https://www.skysports.com/football/2",
        "The arrival of Bradley Barcola increases competition for places and could threaten Cody Gakpo's minutes.",
    ))

    assert article.relevant is True
    assert article.impact == "rotation"
    assert article.confidence <= 0.65


def test_article_ignores_other_players_without_name_match() -> None:
    article = _article(player(), result(
        "Liverpool sign Barcola", "https://www.espn.com/football/3",
        "Liverpool reached agreement for Bradley Barcola in a deal worth 120 million pounds.",
    ))

    assert article.relevant is False
    assert article.impact == "clear"


def test_assessment_keeps_single_rumor_as_watch_only() -> None:
    payload = {"results": [result(
        "Report: City monitoring Gakpo", "https://www.espn.com/football/4",
        "Cody Gakpo is one of a number of forwards Manchester City are monitoring, sources told ESPN.",
    )]}
    assessment = _assess_player(player(), '"Cody Gakpo" Liverpool', payload, datetime.now(timezone.utc), settings())

    assert assessment.status == "watch"
    assert assessment.start_probability_cap is None


def test_assessment_adjusts_down_with_two_independent_credible_reports() -> None:
    now = datetime.now(timezone.utc)
    payload = {"results": [
        result(
            "Gakpo to miss out", "https://www.theathletic.com/football/5",
            "Cody Gakpo is expected to be benched for Liverpool's next league match.",
            0.82,
        ),
        result(
            "Gakpo dropped", "https://www.bbc.co.uk/sport/6",
            "Cody Gakpo is set to be dropped for the upcoming fixture.",
            0.81,
        ),
    ]}
    assessment = _assess_player(player(), '"Cody Gakpo" Liverpool', payload, now, settings())

    assert assessment.status == "adjusted"
    assert assessment.start_probability_cap == 0.45
    assert assessment.confidence >= 0.8


def test_assessment_uses_official_club_confirmation() -> None:
    payload = {"results": [result(
        "Liverpool confirm Gakpo out", "https://www.liverpoolfc.com/news/7",
        "Liverpool confirm Cody Gakpo will miss the next match through injury.",
        0.9,
    )]}
    assessment = _assess_player(player(), '"Cody Gakpo" Liverpool', payload, datetime.now(timezone.utc), settings())

    assert assessment.status == "adjusted"
    assert assessment.start_probability_cap == 0.0


def test_assessment_applies_rotation_cap_from_two_credible_sources() -> None:
    payload = {"results": [
        result(
            "Barcola deal threatens Gakpo's starting role", "https://www.theathletic.com/football/11",
            "The signing of Bradley Barcola could displace Cody Gakpo and limit his minutes for Liverpool.",
            0.81,
        ),
        result(
            "Gakpo faces more competition for places", "https://www.skysports.com/football/12",
            "Cody Gakpo could lose his place in the starting eleven after Liverpool's new signing.",
            0.8,
        ),
    ]}
    assessment = _assess_player(player(), '"Cody Gakpo" Liverpool', payload, datetime.now(timezone.utc), settings())

    assert assessment.status == "adjusted"
    assert assessment.start_probability_cap == 0.75


def test_assessment_treats_manager_uncertainty_as_doubt_cap() -> None:
    payload = {"results": [result(
        "Iraola cannot guarantee Gakpo future", "https://www.theathletic.com/football/13",
        "Andoni Iraola says he cannot guarantee anything regarding the future of Cody Gakpo as Liverpool close in on Barcola.",
        0.8,
    )]}
    assessment = _assess_player(player(), '"Cody Gakpo" Liverpool', payload, datetime.now(timezone.utc), settings())

    assert assessment.status == "adjusted"
    assert assessment.start_probability_cap == 0.7


def test_assessment_keeps_single_rotation_rumor_as_watch_only() -> None:
    payload = {"results": [result(
        "Blog: Gakpo faces rotation risk", "https://www.example-blog.com/football/14",
        "Some fans believe Cody Gakpo faces rotation risk after Liverpool's new signing.",
        0.6,
    )]}
    assessment = _assess_player(player(), '"Cody Gakpo" Liverpool', payload, datetime.now(timezone.utc), settings())

    assert assessment.status == "watch"
    assert assessment.start_probability_cap is None


def test_article_marks_playing_time_competition_as_rotation() -> None:
    article = _article(player(), result(
        "Barcola arrival threatens Gakpo", "https://www.theathletic.com/football/15",
        "Bradley Barcola's arrival could displace Cody Gakpo and reduce his starting role.",
    ))

    assert article.impact == "rotation"


def test_article_marks_manager_uncertainty_as_doubt() -> None:
    article = _article(player(), result(
        "Iraola on Gakpo future", "https://www.theathletic.com/football/16",
        "Iraola cannot guarantee anything regarding Cody Gakpo's future.",
    ))

    assert article.impact == "doubt"


def test_assessment_evidence_contains_rotation_and_start_records() -> None:
    now = datetime.now(timezone.utc)
    payload = {"results": [
        result(
            "Gakpo benched", "https://www.theathletic.com/football/8",
            "Cody Gakpo is expected to be benched.",
            0.8,
        ),
        result(
            "Gakpo out", "https://www.bbc.co.uk/sport/9",
            "Cody Gakpo will not start the next game.",
            0.85,
        ),
    ]}
    assessment = _assess_player(player(), '"Cody Gakpo" Liverpool', payload, now, settings())
    records = _assessment_evidence(assessment, 2, "2026-27", now)

    assert any(record.evidence_type == "rotation_assessment" for record in records)
    predicted = [record for record in records if record.evidence_type == "predicted_start"]
    assert len(predicted) == 1
    assert predicted[0].provider_probability == assessment.start_probability_cap
    assert all(isinstance(record, PlayerEvidence) for record in records)


def test_player_sentences_match_aliases() -> None:
    sentences = _player_sentences(
        player(),
        "Liverpool drew 2-2 with Newcastle. Cody Gakpo started and scored for Liverpool.",
    )

    assert len(sentences) == 1
    assert "started and scored" in sentences[0]


def test_research_returns_disabled_without_configuration(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIFPL_TAVILY_ENABLED", "false")
    store = TavilyNewsStore(tmp_path, settings(enabled=False, api_key=None))

    catalog = store.research([player()], [367], 2, "2026-27")

    assert catalog.status == "disabled"
    assert catalog.assessments == []
    assert catalog.output_path is None


def test_research_caches_immutable_searches(monkeypatch, tmp_path) -> None:
    calls = {"count": 0}

    class FakeClient:
        def __init__(self) -> None:
            pass

        def search(self, query: str, max_results: int) -> dict:
            calls["count"] += 1
            return {"results": [result(
                "Gakpo fine", "https://www.bbc.co.uk/sport/10",
                "Cody Gakpo trained normally and is available.",
            )]}

    store = TavilyNewsStore(tmp_path, client=FakeClient(), settings=settings())
    store.research([player()], [367], 2, "2026-27")
    store.research([player()], [367], 2, "2026-27")

    assert calls["count"] == 1
    assert len([
        path for path in (tmp_path / "raw" / "tavily" / "player_news" / "367").glob("*.json")
        if not path.name.endswith(".manifest.json")
    ]) == 1


def test_research_records_errors_without_failing(monkeypatch, tmp_path) -> None:
    class FailingClient:
        def search(self, query: str, max_results: int) -> dict:
            raise TavilyNewsError("boom")

    store = TavilyNewsStore(tmp_path, client=FailingClient(), settings=settings())
    catalog = store.research([player()], [367], 2, "2026-27")

    assert catalog.status == "partial"
    assert catalog.errors == ["367: boom"]
    assert catalog.assessments == []


def test_configuration_validates_settings(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.setenv("AIFPL_TAVILY_ENABLED", "true")
    monkeypatch.setenv("AIFPL_TAVILY_MAX_RESULTS", "11")

    with pytest.raises(ValueError, match="MAX_RESULTS"):
        tavily_news_settings()


def test_configuration_accepts_up_to_ten_results(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.setenv("AIFPL_TAVILY_ENABLED", "true")
    monkeypatch.setenv("AIFPL_TAVILY_MAX_RESULTS", "10")

    assert tavily_news_settings().max_results == 10


def test_assessment_caps_injured_player_from_training_injury_report() -> None:
    hinshelwood = CurrentPlayer(
        id=123, name="Hinshelwood", position="MID", club_id=21, club="Brighton", cost=60,
        status="a", chance_of_playing_next_round=100, form=10.0, points_per_game=10.0,
        total_points=10, minutes=64, starts=1, expected_goals=0.4, expected_assists=0.1,
        expected_goal_involvements=0.5, expected_goals_conceded=1.0,
        first_name="Jack", second_name="Hinshelwood",
    )
    payload = {"results": [result(
        "Brighton's Jack Hinshelwood set for spell on sidelines after training injury",
        "https://www.nytimes.com/athletic/7543944/2026/08/27/brighton-jack-hinshelwood-injury-latest/",
        "Jack Hinshelwood is set for a spell on the sidelines after sustaining an injury in training. "
        "He had a small issue in training yesterday, we have to go with him day by day. "
        "I can't give a clear schedule of when he will be back.",
        0.8,
    )]}
    assessment = _assess_player(hinshelwood, '"Jack Hinshelwood" Brighton', payload, datetime.now(timezone.utc), settings())

    assert assessment.status == "adjusted"
    assert assessment.start_probability_cap == 0.45
