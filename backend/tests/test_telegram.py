"""Unit tests for Telegram message formatting (no network)."""

from notifications.telegram import TelegramNotifier, format_squad_message

SQUAD = {
    "formation": "3-4-3",
    "starting_xi": [
        {"name": "Raya", "position": "GK"},
        {"name": "Gabriel", "position": "DEF"},
        {"name": "Saliba", "position": "DEF"},
        {"name": "Timber", "position": "DEF"},
        {"name": "Salah", "position": "MID", "is_captain": True},
        {"name": "Saka", "position": "MID", "is_vice_captain": True},
        {"name": "Palmer", "position": "MID"},
        {"name": "Mbeumo", "position": "MID"},
        {"name": "Haaland", "position": "FWD"},
        {"name": "Isak", "position": "FWD"},
        {"name": "Watkins", "position": "FWD"},
    ],
    "bench": [{"name": "Dubravka"}, {"name": "Lewis"}, {"name": "Gordon"}, {"name": "Wood"}],
    "captain": {"name": "Salah", "predicted": 9.1},
    "vice_captain": {"name": "Saka", "predicted": 7.4},
    "predicted_points": 71.5,
}


def test_format_squad_message_contents():
    msg = format_squad_message(SQUAD, gameweek=20)
    assert "GW20" in msg
    assert "3-4-3" in msg
    assert "Salah (C)" in msg
    assert "Saka (V)" in msg
    assert "Bench" in msg and "Dubravka" in msg
    assert "Projected points" in msg


def test_format_with_hermes_narrative_and_transfers():
    msg = format_squad_message(
        SQUAD, gameweek=21,
        hermes_narrative="Captain Salah; Liverpool have the best fixture.",
        transfer_lines=["Out: Watkins → In: Isak (injury)"],
    )
    assert "Hermes says" in msg
    assert "Transfer suggestions" in msg


def test_message_respects_telegram_length_cap():
    msg = format_squad_message(SQUAD, gameweek=20, hermes_narrative="x" * 5000)
    # narrative is trimmed before assembly; final send() also caps at 4096
    assert len(msg) < 4096


def test_notifier_disabled_without_config(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    notifier = TelegramNotifier()
    assert notifier.enabled is False
    assert notifier.send("hello") is False


def test_notifier_requires_all_three_vars(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert TelegramNotifier().enabled is False
