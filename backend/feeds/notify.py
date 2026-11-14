"""Telegram messages from feeds that run outside the daemon (timers, command line)."""

from backend import config


async def send(text: str, reply_markup: dict | None = None, http_client=None) -> None:
    """Send a message to each allowed chat. Callers must not pass ingested content."""
    import httpx

    if not config.TELEGRAM_BOT_TOKEN:
        return
    client = http_client or httpx.AsyncClient(timeout=20.0)
    try:
        for chat_id in config.TELEGRAM_ALLOWED_CHAT_IDS:
            payload = {"chat_id": chat_id, "text": text}
            if reply_markup:
                payload["reply_markup"] = reply_markup
            await client.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage", json=payload)
    finally:
        if http_client is None:
            await client.aclose()
