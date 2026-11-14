"""Daily voice journal.

``record_entry`` is the whole feed and knows nothing about Telegram: it takes a recording
or text and returns what it did. Any front end can call it. Today that is the Telegram
adapter (``feeds/journal_telegram.py``); the GTD web app is expected to add a journal
recording later, and should call ``record_entry`` with its upload ID as ``entry_id`` and
``via="GTD app"`` rather than growing a second implementation.

Run as a module to send the evening prompt:  python3 -m backend.feeds.journal prompt
"""

import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ValidationError

from backend import config
from backend.feeds import ledger, llm, notes, raw, state
from backend.feeds.dryrun import RunReport
from backend.prompts import load_prompt

FEED = "journal"
ENTRY_FEED = "journal-entry"     # state key space for per-message de-duplication
PROMPT_TEXT = ("🌙 Evening journal. Reply to this message with a voice note:\n"
               "• What happened today?\n• What did you promise anyone?\n• What's on your mind?")
# An entry made after midnight but before this hour belongs to the evening before.
DAY_ROLLOVER_HOUR = 4
# Gemini bills audio at about 32 tokens a second; speech comes back at roughly 5.
AUDIO_TOKENS_PER_SECOND, TRANSCRIPT_TOKENS_PER_SECOND = 32, 5
# Used to size the recording when the front end doesn't report a duration. Deliberately
# low, so the length (and so the cost) is over- rather than under-estimated.
ASSUMED_AUDIO_BYTES_PER_SECOND = 1000


class JournalExtract(BaseModel):
    summary: str = ""
    commitments: list[str] = []
    on_my_mind: list[str] = []


@dataclass
class JournalResult:
    status: str                      # saved | duplicate | empty | audio_only | not_saved
    journal_date: str = ""
    note_path: str | None = None
    commitments: list[str] = field(default_factory=list)
    summarised: bool = False
    report: RunReport | None = None

    def message(self) -> str:
        """One line for the user. Counts only: never repeats what was said."""
        if self.status == "duplicate":
            return "📓 That journal entry was already saved."
        if self.status == "empty":
            return "📓 Nothing to save: the recording was silent."
        day = datetime.strptime(self.journal_date, "%Y-%m-%d").strftime("%-d %b")
        if self.status == "audio_only":
            return (f"📓 This month's feed budget is used up, so the recording for {day} was kept "
                    "but not transcribed.")
        if self.status == "not_saved":
            return f"📓 This month's feed budget is used up, so the entry for {day} was not saved."
        n = len(self.commitments)
        line = f"📓 Journal saved for {day}."
        if n:
            line += f" {n} commitment{'s' if n != 1 else ''} added to Captured."
        if not self.summarised:
            line += " Saved as a transcript only: no summary this time."
        return line


def journal_date_for(when: datetime) -> date:
    return (when - timedelta(hours=DAY_ROLLOVER_HOUR)).date()


def _render_entry(when: datetime, via: str, transcript: str, extract: JournalExtract | None) -> str:
    lines = [f"## {when.strftime('%H:%M')} entry (via {via})", ""]
    if extract and extract.summary.strip():
        lines += [extract.summary.strip(), ""]
    if extract and extract.commitments:
        lines += ["**Commitments**", *[f"- {c}" for c in extract.commitments], ""]
    if extract and extract.on_my_mind:
        lines += ["**On my mind**", *[f"- {m}" for m in extract.on_my_mind], ""]
    lines += ["**Transcript**", "", *[f"> {ln}" if ln else ">" for ln in transcript.strip().splitlines()]]
    return "\n".join(lines)


async def _extract(transcript: str, day: date, report: RunReport, dry_run: bool) -> JournalExtract | None:
    """Summary and commitments, or None when the budget is spent or the reply is unusable."""
    try:
        reply = await llm.generate(
            FEED, llm.wrap_untrusted(transcript), load_prompt("journal_extract", date=day.isoformat()),
            response_schema=JournalExtract, max_output_tokens=2048, report=report, dry_run=dry_run)
    except ledger.BudgetExceeded as e:
        print(f"Backend [Journal]: {e}")
        return None
    if reply is None:
        return None
    try:
        return JournalExtract.model_validate_json(reply)
    except ValidationError:
        print("Backend [Journal]: model reply was not valid JSON; saving the transcript only.")
        return None


async def record_entry(*, text: str | None = None, audio: bytes | None = None,
                       mime_type: str | None = None, duration_seconds: float | None = None,
                       when: datetime | None = None, journal_date: date | None = None,
                       via: str = "Telegram", entry_id: str | None = None,
                       dry_run: bool = False) -> JournalResult:
    """Record one journal entry from a recording or from text.

    ``entry_id`` identifies the message or upload; giving the same one twice saves once,
    so a front end can retry safely. ``journal_date`` overrides the day the entry belongs
    to (the Telegram adapter passes the date of the prompt being replied to).
    """
    from backend.processing import voice_capture
    from backend.vault import clarify_ops

    when = when or datetime.now()
    day = journal_date or journal_date_for(when)
    report = RunReport(FEED, dry_run=dry_run)
    result = JournalResult(status="saved", journal_date=day.isoformat(), report=report)

    if entry_id and state.get_seen(ENTRY_FEED, entry_id):
        result.status = "duplicate"
        return result

    transcript = (text or "").strip()
    if audio:
        ext = voice_capture.extension_for(mime_type) or "ogg"
        audio_name = f"{day.isoformat()}_{when.strftime('%H-%M-%S')}_journal.{ext}"
        raw.store_raw_bytes(FEED, audio_name, audio, report=report, dry_run=dry_run)

        seconds = duration_seconds or len(audio) / ASSUMED_AUDIO_BYTES_PER_SECOND
        worst = ledger.cost_pence(config.LLM_MODEL, int(seconds * AUDIO_TOKENS_PER_SECOND), 0, audio=True) \
            + ledger.cost_pence(config.LLM_MODEL, 0, int(seconds * TRANSCRIPT_TOKENS_PER_SECOND))
        if dry_run:
            report.model_calls += 1
            report.cost_pence += worst
            transcript = transcript or "(dry run: recording not transcribed)"
        else:
            try:
                ledger.check_budget(FEED, worst)
            except ledger.BudgetExceeded as e:
                print(f"Backend [Journal]: {e}")
                if entry_id:
                    state.mark_seen(ENTRY_FEED, entry_id, "audio-only")
                result.status = "audio_only"
                return result
            transcript = (await voice_capture.transcribe(audio, mime_type)).strip()
            report.model_calls += 1
            report.cost_pence += ledger.record(
                FEED, config.LLM_MODEL, "transcribe", int(seconds * AUDIO_TOKENS_PER_SECOND), 0, audio=True)
            report.cost_pence += ledger.record(
                FEED, config.LLM_MODEL, "transcribe-output", 0, ledger.estimate_tokens(transcript))

    if not transcript:
        result.status = "empty"
        return result

    extract = await _extract(transcript, day, report, dry_run)
    result.summarised = extract is not None

    title = f"{day.isoformat()} Journal"
    seen = state.get_seen(FEED, day.isoformat())
    earlier = notes.existing_body(seen["note_path"] if seen else None)
    entry = _render_entry(when, via, transcript, extract)
    body = f"{earlier}\n\n{entry}" if earlier else entry

    try:
        result.note_path = notes.write_feed_note(
            feed=FEED, source_id=day.isoformat(), note_type="journal", title=title, body=body,
            dest_dir=os.path.join(config.JOURNAL_DIR, str(day.year)), original_date=day,
            report=report, dry_run=dry_run)
    except ledger.BudgetExceeded as e:      # even the indexing reserve is gone
        print(f"Backend [Journal]: {e}")
        result.status = "not_saved"
        return result

    result.commitments = [c.strip() for c in (extract.commitments if extract else []) if c.strip()]
    if not dry_run:
        for commitment in result.commitments:
            clarify_ops.add_capture(commitment, when, via="Journal", source=title)
        if entry_id:
            state.mark_seen(ENTRY_FEED, entry_id, notes.content_hash(entry), note_path=result.note_path)
    return result


# --- evening prompt ------------------------------------------------------------------

def prompt_date(chat_id: int, message_id: int) -> date | None:
    """The journal date of a prompt this bot sent, or None if the message isn't one."""
    with state.connect() as conn:
        row = conn.execute("SELECT journal_date FROM journal_prompts WHERE chat_id = ? AND message_id = ?",
                           (chat_id, message_id)).fetchone()
    return date.fromisoformat(row[0]) if row else None


def _already_prompted(chat_id: int, day: date) -> bool:
    with state.connect() as conn:
        return conn.execute("SELECT 1 FROM journal_prompts WHERE chat_id = ? AND journal_date = ?",
                            (chat_id, day.isoformat())).fetchone() is not None


async def send_evening_prompt(http_client=None, now: datetime | None = None) -> int:
    """Send tonight's prompt to each allowed chat, once. Returns how many were sent."""
    import httpx

    if not config.ENABLE_JOURNAL_FEED or not config.TELEGRAM_BOT_TOKEN:
        print("Backend [Journal]: feed disabled or no bot token; no prompt sent.")
        return 0
    now = now or datetime.now()
    day = journal_date_for(now)
    sent = 0
    client = http_client or httpx.AsyncClient(timeout=20.0)
    try:
        for chat_id in config.TELEGRAM_ALLOWED_CHAT_IDS:
            if _already_prompted(chat_id, day):
                continue
            res = await client.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": PROMPT_TEXT})
            data = res.json()
            if not data.get("ok"):
                print(f"Backend [Journal]: prompt not sent (status {res.status_code}).")
                continue
            with state.connect() as conn:
                conn.execute("INSERT OR IGNORE INTO journal_prompts VALUES (?, ?, ?, ?)",
                             (chat_id, data["result"]["message_id"], day.isoformat(),
                              now.isoformat(timespec="seconds")))
            sent += 1
    finally:
        if http_client is None:
            await client.aclose()
    return sent


if __name__ == "__main__":
    if sys.argv[1:] == ["prompt"]:
        print(f"Backend [Journal]: sent {asyncio.run(send_evening_prompt())} prompt(s).")
    else:
        sys.exit("usage: python3 -m backend.feeds.journal prompt")
