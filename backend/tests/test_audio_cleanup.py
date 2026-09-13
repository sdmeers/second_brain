"""cleanup_old_audio must expire Telegram voice notes left in the inbox."""

import asyncio
import os
import time

from backend import brain_cleaner


def _touch(path, age_days):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    old = time.time() - age_days * 86400
    os.utime(path, (old, old))


def test_old_inbox_voice_files_are_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_cleaner, "VAULT_PATH", str(tmp_path))
    inbox = tmp_path / "00_System" / "Inbox"
    old_ogg = inbox / "2026-01-01_10-00-00_voice.ogg"
    old_md = inbox / "2026-01-01_10-00-00_voice.md"
    new_ogg = inbox / "2026-09-13_10-00-00_voice.ogg"
    elsewhere = tmp_path / "03_Areas" / "my_voice.md"
    for p in (old_ogg, old_md, elsewhere):
        _touch(p, age_days=40)
    _touch(new_ogg, age_days=1)

    asyncio.run(brain_cleaner.cleanup_old_audio())

    assert not old_ogg.exists() and not old_md.exists()
    assert new_ogg.exists()
    assert elsewhere.exists()
