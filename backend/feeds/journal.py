"""Daily voice journal.

``record_entry`` is the whole feed and knows nothing about Telegram: it takes a recording
or text and returns what it did. Any front end can call it. Today that is the Telegram
adapter (``feeds/journal_telegram.py``); the GTD web app is expected to add a journal
recording later, and should call ``record_entry`` with its upload ID as ``entry_id`` and
``via="GTD app"`` rather than growing a second implementation.

Run as a module to send the evening prompt (and apply the audio retention rule):
    python3 -m backend.feeds.journal prompt
    python3 -m backend.feeds.journal prune [--dry-run]
"""

import asyncio
import calendar
import os
import re
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
PROMPT_TEXT = ("🌙 Evening journal. Whenever you're ready before bed, send a voice note "
               "and tap 📓 Journal:\n"
               "• What happened today?\n• What did you promise anyone?\n• What's on your mind?")
# An entry made after midnight but before this hour belongs to the evening before.
DAY_ROLLOVER_HOUR = 4
# The journal window opens with the evening prompt, or at this hour if none was sent.
WINDOW_START_HOUR = 22
# Recordings are named <journal date>_<HH-MM-SS>[-n]_journal.<ext> in the raw store.
AUDIO_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2})-\d{2}(?:-\d+)?_journal\.[a-z0-9]{2,5}$")
AUDIO_MARK_RE = re.compile(r"^<!-- audio: (\S+) -->$")
ENTRY_HEADING_RE = re.compile(r"^## (\d{2}:\d{2}) entry \(via ([^)]*)\)\s*$", re.MULTILINE)
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


def _render_entry(when: datetime, via: str, transcript: str, extract: JournalExtract | None,
                  audio_name: str | None = None) -> str:
    lines = [f"## {when.strftime('%H:%M')} entry (via {via})", ""]
    if audio_name:
        # Hidden in Obsidian's reading view; lets the web app play the recording back.
        lines += [f"<!-- audio: {audio_name} -->", ""]
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
    audio_name = None
    if audio:
        ext = voice_capture.extension_for(mime_type) or "ogg"
        audio_name = _free_audio_name(f"{day.isoformat()}_{when.strftime('%H-%M-%S')}", ext)
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
    entry = _render_entry(when, via, transcript, extract, audio_name)
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


def _audio_dir() -> str:
    return os.path.join(config.FEEDS_RAW_PATH, FEED)


def _free_audio_name(stem: str, ext: str) -> str:
    """Two recordings in the same second must not overwrite each other."""
    name, n = f"{stem}_journal.{ext}", 1
    while os.path.exists(os.path.join(_audio_dir(), name)):
        n += 1
        name = f"{stem}-{n}_journal.{ext}"
    return name


# --- reading entries back (the web app's journal page) ---------------------------------

def in_journal_window(chat_id: int | None, now: datetime) -> bool:
    """From tonight's prompt (or 22:00 if none was sent) until the 04:00 rollover."""
    if chat_id is not None:
        with state.connect() as conn:
            row = conn.execute("SELECT sent_at FROM journal_prompts WHERE chat_id = ? AND journal_date = ?",
                               (chat_id, journal_date_for(now).isoformat())).fetchone()
        if row and datetime.fromisoformat(row[0]) <= now:
            return True
    return now.hour >= WINDOW_START_HOUR or now.hour < DAY_ROLLOVER_HOUR


def today_status(now: datetime | None = None) -> dict:
    """For the web app's quiet dot: is tonight's entry still to do?"""
    now = now or datetime.now()
    day = journal_date_for(now)
    recorded = state.get_seen(FEED, day.isoformat()) is not None
    return {"date": day.isoformat(), "recorded": recorded,
            "due": config.ENABLE_JOURNAL_FEED and not recorded and in_journal_window(None, now)}


def _note_path(day: date) -> str:
    return os.path.join(config.JOURNAL_DIR, str(day.year), f"{day.isoformat()} Journal.md")


def list_days() -> list[dict]:
    """Every day with a journal note, newest first, with how many entries it has."""
    root = os.path.join(config.OBSIDIAN_VAULT_PATH, config.JOURNAL_DIR)
    days = []
    for year in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        folder = os.path.join(root, year)
        if not (year.isdigit() and os.path.isdir(folder)):
            continue
        for name in os.listdir(folder):
            m = re.fullmatch(r"(\d{4}-\d{2}-\d{2}) Journal\.md", name)
            if not m:
                continue
            body = notes.existing_body(os.path.join(config.JOURNAL_DIR, year, name))
            days.append({"date": m.group(1), "entries": len(ENTRY_HEADING_RE.findall(body))})
    return sorted(days, key=lambda d: d["date"], reverse=True)


def _old_audio_for(day: str, hhmm: str) -> str | None:
    """Entries saved before the audio marker existed: match the recording by its time."""
    try:
        names = sorted(os.listdir(_audio_dir()))
    except OSError:
        return None
    for name in names:
        m = AUDIO_NAME_RE.match(name)
        if m and m.group(1) == day and m.group(2) == hhmm.replace(":", "-"):
            return name
    return None


def _parse_entry(day: str, time: str, via: str, chunk: str) -> dict:
    entry = {"time": time, "via": via, "summary": "", "commitments": [], "on_my_mind": [],
             "transcript": "", "audio": None}
    section, summary, transcript = "summary", [], []
    lists = {"commitments": entry["commitments"], "on_my_mind": entry["on_my_mind"]}
    for line in chunk.split("\n"):
        stripped = line.strip()
        mark = AUDIO_MARK_RE.match(stripped)
        if mark:
            entry["audio"] = mark.group(1)
        elif stripped == "**Commitments**":
            section = "commitments"
        elif stripped == "**On my mind**":
            section = "on_my_mind"
        elif stripped == "**Transcript**":
            section = "transcript"
        elif section == "transcript":
            transcript.append(re.sub(r"^> ?", "", line))
        elif section in lists:
            if stripped:      # a line typed by hand in Obsidian is kept too
                lists[section].append(stripped[2:] if stripped.startswith("- ") else stripped)
        else:
            summary.append(line)
    entry["summary"] = "\n".join(summary).strip()
    entry["transcript"] = "\n".join(transcript).strip()
    if entry["audio"] is None:
        entry["audio"] = _old_audio_for(day, time)
    if entry["audio"] and not os.path.isfile(os.path.join(_audio_dir(), entry["audio"])):
        entry["audio"] = None         # deleted by the retention rule
    return entry


def read_day(day: date) -> dict | None:
    """One day's entries, parsed from the note. Text added by hand above the first entry
    comes back as ``notes``. None when there is no journal for that day."""
    rel = _note_path(day)
    if not os.path.isfile(os.path.join(config.OBSIDIAN_VAULT_PATH, rel)):
        return None
    body = notes.existing_body(rel)
    parts = ENTRY_HEADING_RE.split(body)
    entries = [_parse_entry(day.isoformat(), parts[i], parts[i + 1], parts[i + 2])
               for i in range(1, len(parts), 3)]
    return {"date": day.isoformat(), "notes": parts[0].strip(), "entries": entries}


def audio_file(name: str) -> str | None:
    """The path of a stored recording, or None. Only names the feed itself produces."""
    if not AUDIO_NAME_RE.match(name):
        return None
    path = os.path.join(_audio_dir(), name)
    return path if os.path.isfile(path) else None


# --- audio retention -----------------------------------------------------------------

def _months_before(day: date, months: int) -> date:
    y, m = divmod(day.year * 12 + day.month - 1 - months, 12)
    m += 1
    return date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


def prune_audio(months: int | None = None, today: date | None = None, dry_run: bool = False) -> int:
    """Delete recordings older than ``months`` (JOURNAL_AUDIO_KEEP_MONTHS). 0 keeps all.
    Transcripts in the vault are never touched. Returns how many were (or would be) deleted."""
    months = config.JOURNAL_AUDIO_KEEP_MONTHS if months is None else months
    if months <= 0:
        return 0
    cutoff = _months_before(today or date.today(), months)
    try:
        names = os.listdir(_audio_dir())
    except OSError:
        return 0
    removed = 0
    for name in names:
        m = AUDIO_NAME_RE.match(name)
        if m and date.fromisoformat(m.group(1)) < cutoff:
            removed += 1
            if not dry_run:
                os.remove(os.path.join(_audio_dir(), name))
    return removed


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
    args = sys.argv[1:]
    if args == ["prompt"]:
        print(f"Backend [Journal]: sent {asyncio.run(send_evening_prompt())} prompt(s).")
        n = prune_audio()
        if n:
            print(f"Backend [Journal]: deleted {n} recording(s) past the retention period.")
    elif args and args[0] == "prune" and set(args[1:]) <= {"--dry-run"}:
        dry = "--dry-run" in args
        n = prune_audio(dry_run=dry)
        print(f"Backend [Journal]: {'would delete' if dry else 'deleted'} {n} recording(s) "
              f"(keep {config.JOURNAL_AUDIO_KEEP_MONTHS or 'forever'} months).")
    else:
        sys.exit("usage: python3 -m backend.feeds.journal prompt | prune [--dry-run]")
