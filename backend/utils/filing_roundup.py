"""What has been filed into the vault since the last morning review.

The morning review (``brain_cleaner.generate_daily_digest``) uses this twice: a short
block in its Telegram message with counts and folder names only, and a section in the
Daily Digest note with a link to each note. Titles stay in the vault because the feeds'
titles are written from ingested content (an email's subject, a document's contents).

Sources: notes written by the email, household and chat feeds (from the feed state
store), and everything the librarian files (from ``00_System/Librarian_Activity.md``).
The calendar's routine daily notes and your own journal entries are left out.
"""

import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend import config
from backend.feeds import state

FEED_SOURCES = {"email": "email", "household": "household", "chats": "AI chats"}
LAST_REVIEW_KEY = "roundup:last_review"
# Never look back further than this, even if the review hasn't run for a while.
MAX_LOOKBACK = timedelta(days=3)
_LOG_LINE = re.compile(r"^- \*\*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}):\*\* Ingested \[\[(.+?)\]\] to `([^`]+)`")


@dataclass(frozen=True)
class Filed:
    source: str      # "librarian", "email", "household" or "AI chats"
    folder: str      # vault-relative
    link: str        # wikilink target
    name: str        # what the link shows


def window_start(now: datetime) -> datetime:
    """Since the previous scheduled review (07:30 weekdays, 10:00 weekends), so nothing
    falls between them; 24 hours if there's no record, and never more than three days."""
    last = state.get_value(LAST_REVIEW_KEY)
    since = datetime.fromisoformat(last) if last else now - timedelta(days=1)
    return max(since, now - MAX_LOOKBACK)


def mark_reviewed(now: datetime) -> None:
    state.set_value(LAST_REVIEW_KEY, now.isoformat(timespec="seconds"))


def _feed_notes(since: datetime) -> list[Filed]:
    marks = ",".join("?" * len(FEED_SOURCES))
    with state.connect() as conn:
        rows = conn.execute(
            f"SELECT feed, note_path FROM seen_items WHERE feed IN ({marks}) AND note_path IS NOT NULL "
            "AND ingested_at >= ? ORDER BY note_path",
            (*FEED_SOURCES, since.isoformat(timespec="seconds"))).fetchall()
    items = []
    for feed, path in rows:
        if os.path.isfile(os.path.join(config.OBSIDIAN_VAULT_PATH, path)):
            stem = os.path.splitext(path)[0]
            items.append(Filed(FEED_SOURCES[feed], os.path.dirname(path), stem, os.path.basename(stem)))
    return items


def _librarian_notes(since: datetime) -> list[Filed]:
    path = os.path.join(config.OBSIDIAN_VAULT_PATH, "00_System", "Librarian_Activity.md")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    seen, items = set(), []
    for line in lines:
        m = _LOG_LINE.match(line)
        if not m or datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") < since:
            continue
        title, folder = m.group(2), m.group(3).strip().rstrip("/")
        if folder == "Unknown" or (title, folder) in seen:     # the log sometimes repeats a line
            continue
        seen.add((title, folder))
        items.append(Filed("librarian", folder, title, title))
    return items


def filed_since(since: datetime, skip_professional: bool = False) -> list[Filed]:
    """Everything filed since ``since``. Weekend reviews leave out Professional areas,
    as the rest of the digest does."""
    items = _librarian_notes(since) + _feed_notes(since)
    if skip_professional:
        items = [i for i in items if "Professional" not in i.folder]
    return items


def telegram_block(items: list[Filed], since: datetime) -> str:
    """Counts and folders only, never titles. Empty when nothing was filed."""
    if not items:
        return ""
    counts = Counter((i.folder, i.source) for i in items)
    lines = [f"🗂 **Filed since {since.strftime('%a %H:%M')}: {len(items)}**"]
    lines += [f"• {n} → `{folder}` ({source})"
              for (folder, source), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    lines.append("Links are in the Daily Digest note.")
    return "\n".join(lines)


def digest_section(items: list[Filed], since: datetime) -> str:
    """The Daily Digest note's section, with a link to each note. Empty when nothing was filed."""
    if not items:
        return ""
    lines = [f"## 🗂 Filed since {since.strftime('%a %d %b %H:%M')}"]
    lines += [f"- [[{i.link}|{i.name}]] · `{i.folder}` ({i.source})" for i in items]
    return "\n".join(lines) + "\n\n"
