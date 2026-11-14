"""Telegram front end for the journal feed, plus the /feeds status command.

``maybe_handle`` is called once at the top of the Telegram dispatcher. It returns True
when it has dealt with the message; otherwise the message carries on exactly as before.
"""

from datetime import datetime

from backend import config
from backend.feeds import journal, ledger, state
from backend.utils.sessions import SessionStore

# Chats that have sent /journal and whose next voice note or text is the entry.
_pending = SessionStore("journal_pending", ttl=30 * 60)


async def _send(http_client, token: str, chat_id: int, text: str) -> None:
    await http_client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                           json={"chat_id": chat_id, "text": text})


def feeds_status() -> str:
    s = ledger.summary()
    lines = [f"📡 Feeds, {datetime.now().strftime('%B %Y')}",
             f"Spent {ledger.format_pence(s['total_pence'])} of "
             f"{ledger.format_pence(config.FEEDS_MONTHLY_BUDGET_PENCE)} "
             f"({ledger.format_pence(max(s['remaining_pence'], 0))} left)", ""]
    with state.connect() as conn:
        rows = conn.execute(
            "SELECT feed, COUNT(*), MAX(ingested_at) FROM seen_items "
            "WHERE feed NOT LIKE '%-entry' GROUP BY feed ORDER BY feed").fetchall()
    if not rows:
        lines.append("Nothing ingested yet.")
    for feed, count, last in rows:
        spent = ledger.format_pence(s["by_feed"].get(feed, 0.0))
        lines.append(f"• {feed}: {count} note{'s' if count != 1 else ''}, last {last[:16].replace('T', ' ')}, "
                     f"{spent} this month")
    return "\n".join(lines)


async def maybe_handle(message: dict, text: str, token: str, http_client) -> bool:
    if not config.ENABLE_JOURNAL_FEED:
        return False
    from backend.ingestion.telegram import download_telegram_file

    chat_id = message["chat"]["id"]
    voice = message.get("voice") or message.get("audio")
    command = text.split()[0].lower() if text.startswith("/") else ""

    if command == "/feeds":
        await _send(http_client, token, chat_id, feeds_status())
        return True

    entry_text, journal_date = None, None
    if command == "/journal":
        entry_text = text[len("/journal"):].strip()
        if not entry_text and not voice:
            _pending.set(chat_id, True)
            await _send(http_client, token, chat_id,
                        "📓 Go ahead: send a voice note or a message and I'll save it as your journal.")
            return True
    elif command:
        _pending.pop(chat_id)          # any other command abandons a pending /journal
        return False
    else:
        reply_to = (message.get("reply_to_message") or {}).get("message_id")
        journal_date = journal.prompt_date(chat_id, reply_to) if reply_to else None
        if journal_date is None and not _pending.get(chat_id):
            return False
        if not voice and not text:
            return False               # a photo or document: not a journal entry
        entry_text = text

    _pending.pop(chat_id)
    audio, mime, duration = None, None, None
    if voice:
        mime = voice.get("mime_type", "audio/ogg")
        duration = voice.get("duration")
        audio = await download_telegram_file(voice["file_id"], token, http_client)
        await _send(http_client, token, chat_id, "🎤 Transcribing your journal...")

    result = await journal.record_entry(
        text=None if audio else entry_text, audio=audio, mime_type=mime, duration_seconds=duration,
        journal_date=journal_date, via="Telegram", entry_id=f"telegram:{chat_id}:{message.get('message_id')}")
    await _send(http_client, token, chat_id, result.message())
    return True
