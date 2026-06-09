"""Unit tests for availability agent helpers."""

from agents.availability_agent import has_negative_news


def test_negative_news_detected():
    assert has_negative_news("Hamstring injury - expected back 25 Dec")
    assert has_negative_news("Suspended until 12 Jan")
    assert has_negative_news("Ruled out for the season")
    assert has_negative_news("Will miss next two matches")


def test_neutral_or_empty_news_not_flagged():
    assert not has_negative_news("")
    assert not has_negative_news(None)
    assert not has_negative_news("Transferred to Real Madrid? Speculation only")
    assert not has_negative_news("Back in full training")
