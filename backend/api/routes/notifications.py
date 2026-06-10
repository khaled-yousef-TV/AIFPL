"""
Notification endpoints (Telegram setup/testing).
"""

import logging

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/test")
async def send_test_message():
    """Send a test Telegram message to verify TELEGRAM_* configuration."""
    from notifications.telegram import TelegramNotifier

    notifier = TelegramNotifier()
    if not notifier.enabled:
        raise HTTPException(
            status_code=400,
            detail="Telegram not configured: set TELEGRAM_ENABLED=true, "
                   "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID",
        )

    sent = notifier.send("✅ *FPL AI*: Telegram notifications are working.")
    if not sent:
        raise HTTPException(status_code=502, detail="Telegram send failed (see server logs)")
    return {"success": True}


@router.get("/status")
async def get_notification_status():
    """Telegram configuration status."""
    from notifications.telegram import TelegramNotifier
    notifier = TelegramNotifier()
    return {"telegram_enabled": notifier.enabled}
