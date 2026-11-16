"""The morning review's round-up of what was filed. Synthetic content only."""

from datetime import datetime, timedelta

import pytest

from backend import config
from backend.feeds import state
from backend.utils import filing_roundup as roundup

NOW = datetime.now().replace(microsecond=0)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, vault):
    monkeypatch.setattr(config, "FEEDS_DB_PATH", str(tmp_path / "feeds.db"))
    log = vault / "00_System" / "Librarian_Activity.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.unlink(missing_ok=True)
    yield


def feed_note(vault, feed, rel):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    state.mark_seen(feed, rel, "h", note_path=rel)


def librarian(vault, *entries):
    lines = ["# 📋 Librarian Activity Log", "", "---", ""]
    for when, title, folder in entries:
        lines.append(f"- **{when:%Y-%m-%d %H:%M:%S}:** Ingested [[{title}]] to `{folder}` (reference via Telegram)")
    (vault / "00_System" / "Librarian_Activity.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_gathers_librarian_and_feed_notes_but_not_calendar_or_journal(vault):
    feed_note(vault, "email", "03_Areas/Finance/Email/2026-11-16 Gas bill.md")
    feed_note(vault, "household", "03_Areas/Home/Documents/Boiler warranty.md")
    feed_note(vault, "calendar", "03_Areas/Journal/2026/2026-11-16 Daily.md")
    feed_note(vault, "journal", "03_Areas/Journal/2026/2026-11-16 Journal.md")
    librarian(vault, (NOW - timedelta(hours=2), "Sourdough method", "04_Resources/Cooking"),
              (NOW - timedelta(hours=2), "Sourdough method", "04_Resources/Cooking"),     # repeated line
              (NOW - timedelta(days=2), "Old thing", "04_Resources/Cooking"),
              (NOW - timedelta(hours=1), "Lost", "Unknown"))
    items = roundup.filed_since(NOW - timedelta(days=1))
    assert sorted((i.source, i.folder) for i in items) == [
        ("email", "03_Areas/Finance/Email"), ("household", "03_Areas/Home/Documents"),
        ("librarian", "04_Resources/Cooking")]


def test_telegram_block_has_counts_and_folders_never_titles(vault):
    feed_note(vault, "email", "03_Areas/Finance/Email/2026-11-16 Gas bill.md")
    feed_note(vault, "email", "03_Areas/Finance/Email/2026-11-16 Council tax.md")
    feed_note(vault, "chats", "04_Resources/AI_Conversations/Planning a trip.md")
    librarian(vault, (NOW - timedelta(hours=2), "Sourdough method", "04_Resources/Cooking"))
    since = NOW - timedelta(days=1)
    text = roundup.telegram_block(roundup.filed_since(since), since)
    assert text.splitlines()[1:4] == ["• 2 → `03_Areas/Finance/Email` (email)",
                                      "• 1 → `04_Resources/AI_Conversations` (AI chats)",
                                      "• 1 → `04_Resources/Cooking` (librarian)"]
    assert ": 4**" in text.splitlines()[0]
    for title in ("Gas bill", "Council tax", "Planning a trip", "Sourdough"):
        assert title not in text


def test_digest_section_links_each_note(vault):
    feed_note(vault, "email", "03_Areas/Finance/Email/2026-11-16 Gas bill.md")
    librarian(vault, (NOW - timedelta(hours=2), "Sourdough method", "04_Resources/Cooking"))
    since = NOW - timedelta(days=1)
    lines = roundup.digest_section(roundup.filed_since(since), since).strip().splitlines()
    assert lines[0].startswith("## 🗂 Filed since ")
    assert "- [[Sourdough method|Sourdough method]] · `04_Resources/Cooking` (librarian)" in lines
    assert "- [[03_Areas/Finance/Email/2026-11-16 Gas bill|2026-11-16 Gas bill]] · `03_Areas/Finance/Email` (email)" in lines


def test_nothing_filed_means_nothing_shown():
    since = NOW - timedelta(days=1)
    assert roundup.filed_since(since) == []
    assert roundup.telegram_block([], since) == "" and roundup.digest_section([], since) == ""


def test_deleted_notes_drop_out(vault):
    feed_note(vault, "email", "03_Areas/Finance/Email/Gone.md")
    (vault / "03_Areas/Finance/Email/Gone.md").unlink()
    assert roundup.filed_since(NOW - timedelta(days=1)) == []


def test_weekend_review_leaves_out_professional(vault):
    feed_note(vault, "email", "03_Areas/Professional/Email/Contract.md")
    librarian(vault, (NOW - timedelta(hours=1), "Team notes", "02_Projects/Professional/Launch"))
    since = NOW - timedelta(days=1)
    assert len(roundup.filed_since(since)) == 2
    assert roundup.filed_since(since, skip_professional=True) == []


def test_window_runs_from_the_last_scheduled_review():
    assert roundup.window_start(NOW) == NOW - timedelta(days=1)              # no record yet
    friday = NOW - timedelta(days=1, hours=2, minutes=30)
    roundup.mark_reviewed(friday)
    assert roundup.window_start(NOW) == friday                                 # longer than 24h: no gap
    roundup.mark_reviewed(NOW - timedelta(days=10))
    assert roundup.window_start(NOW) == NOW - roundup.MAX_LOOKBACK             # capped
