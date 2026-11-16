"""Telegram front end for the journal feed, plus the /feeds status command.

``maybe_handle`` is called once at the top of the Telegram dispatcher. It returns True
when it has dealt with the message; otherwise the message carries on exactly as before.

A voice note is a journal entry when it replies to the evening prompt or follows
/journal. During the journal window (from the evening prompt until the 04:00 rollover)
any other voice note gets two buttons, Journal or Capture, so an entry recorded later in
the evening without replying is not filed as a capture by mistake.
"""

from datetime import date, datetime

from backend import config
from backend.feeds import journal, ledger, state
from backend.utils.sessions import SessionStore

# Chats that have sent /journal and whose next voice note or text is the entry.
_pending = SessionStore("journal_pending", ttl=30 * 60)
# Voice notes waiting for the Journal / Capture choice, keyed by (chat_id, message_id).
# In memory only: after a restart a tap says the note has expired.
_offers = SessionStore("journal_offers", ttl=8 * 60 * 60)
# Fallback start of the window if the evening prompt was not sent (Telegram down).
WINDOW_START_HOUR = 22
CALLBACK_PREFIX = "jrnl:"
# Set on a message the user chose to capture, so the dispatcher's second pass skips us.
DECLINED = "_journal_declined"


def _now() -> datetime:
    return datetime.now()


def in_journal_window(chat_id: int, now: datetime) -> bool:
    """From tonight's prompt (or 22:00 if none was sent) until the 04:00 rollover."""
    day = journal.journal_date_for(now)
    with state.connect() as conn:
        row = conn.execute("SELECT sent_at FROM journal_prompts WHERE chat_id = ? AND journal_date = ?",
                           (chat_id, day.isoformat())).fetchone()
    if row and datetime.fromisoformat(row[0]) <= now:
        return True
    return now.hour >= WINDOW_START_HOUR or now.hour < journal.DAY_ROLLOVER_HOUR


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


async def _offer(message: dict, chat_id: int, token: str, http_client) -> None:
    msg_id = message.get("message_id")
    _offers.set((chat_id, msg_id), {"message": message, "date": journal.journal_date_for(_now())})
    await http_client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
        "chat_id": chat_id, "text": "🎤 Is this your journal?",
        "reply_to_message_id": msg_id,
        "reply_markup": {"inline_keyboard": [[
            {"text": "📓 Journal", "callback_data": f"{CALLBACK_PREFIX}j:{msg_id}"},
            {"text": "📥 Capture", "callback_data": f"{CALLBACK_PREFIX}c:{msg_id}"}]]}})


async def _record_voice(message: dict, chat_id: int, journal_date: date | None, token: str, http_client,
                        text: str | None = None) -> None:
    from backend.ingestion.telegram import download_telegram_file

    voice = message.get("voice") or message.get("audio")
    audio, mime, duration = None, None, None
    if voice:
        mime = voice.get("mime_type", "audio/ogg")
        duration = voice.get("duration")
        audio = await download_telegram_file(voice["file_id"], token, http_client)
        await _send(http_client, token, chat_id, "🎤 Transcribing your journal...")
    result = await journal.record_entry(
        text=None if audio else text, audio=audio, mime_type=mime, duration_seconds=duration,
        journal_date=journal_date, via="Telegram", entry_id=f"telegram:{chat_id}:{message.get('message_id')}")
    await _send(http_client, token, chat_id, result.message())


async def handle_callback(data: str, chat_id: int, message_id: int, token: str, http_client) -> None:
    """A tap on the Journal / Capture buttons under a voice note."""
    from backend.ingestion.telegram import process_telegram_message

    choice, _, voice_msg_id = data[len(CALLBACK_PREFIX):].partition(":")
    try:
        offer = _offers.pop((chat_id, int(voice_msg_id)))
    except ValueError:
        offer = None
    edit = f"https://api.telegram.org/bot{token}/editMessageText"
    if offer is None:
        await http_client.post(edit, json={
            "chat_id": chat_id, "message_id": message_id,
            "text": "⌛ That choice has expired. Send /journal, then forward the voice note."})
        return
    label = "📓 Journal" if choice == "j" else "📥 Capture"
    await http_client.post(edit, json={"chat_id": chat_id, "message_id": message_id, "text": f"🎤 {label}"})
    try:
        if choice == "j":
            await _record_voice(offer["message"], chat_id, offer["date"], token, http_client)
        else:
            await process_telegram_message({**offer["message"], DECLINED: True}, token, http_client)
    except Exception:
        _offers.set((chat_id, int(voice_msg_id)), offer)      # let the user tap again
        raise


async def maybe_handle(message: dict, text: str, token: str, http_client) -> bool:
    if not config.ENABLE_JOURNAL_FEED or message.get(DECLINED):
        return False
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
            if voice and not text and in_journal_window(chat_id, _now()):
                await _offer(message, chat_id, token, http_client)
                return True
            return False
        if not voice and not text:
            return False               # a photo or document: not a journal entry
        entry_text = text

    _pending.pop(chat_id)
    await _record_voice(message, chat_id, journal_date, token, http_client, text=entry_text)
    return True
