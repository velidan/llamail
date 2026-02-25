import logging
import httpx
from email_service.config import settings

logger = logging.getLogger(__name__)


def notify(message: str) -> None:
    """Send a push notification via Telegram Bot API."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram notified not configured, skipping")
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": message,
    }

    try:
        resp = httpx.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Telegram notification sent")
    except Exception as e:
        logger.error(f"Telegram notification failed: {e}")
