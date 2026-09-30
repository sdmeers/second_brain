"""Keeps recoverable history of the GTD task files in 01_Tasks/.

Nothing else records these files: orchestrator commits only happen on ingestion, and
Syncthing keeps no versions by default. On 2026-09-29 a sync-conflict merge emptied
Next_Actions.md and there was no copy to restore from. This module provides:

- ``backup_files()`` -- dated copies outside the vault, taken before any merge;
- ``note_own_write()`` / ``edited_elsewhere_recently()`` -- tell the server's own writes
  apart from edits synced in from other devices, so the dashboard can hold off
  rewriting a file another device has just changed;
- ``maybe_commit_task_changes()`` -- commits 01_Tasks/ to the vault's git once the
  server's writes have settled, and only when something actually changed.
"""

import asyncio
import os
import shutil
import time
from datetime import datetime, timedelta

from backend.config import (
    OBSIDIAN_VAULT_PATH,
    TASK_BACKUP_PATH,
    TASK_BACKUP_RETENTION_DAYS,
    TASK_COMMIT_DELAY_SECONDS,
    TASK_SYNC_QUIET_SECONDS,
    TASKS_PATH,
)

# path -> st_mtime_ns right after the server last wrote it
_own_mtimes: dict[str, int] = {}
# monotonic time of the last server write to 01_Tasks/ not yet committed
_pending_since: float | None = None


def is_task_file(path: str) -> bool:
    return os.path.dirname(os.path.abspath(path)) == os.path.abspath(TASKS_PATH)


def note_own_write(path: str):
    """Called by atomic_write after every write into 01_Tasks/."""
    global _pending_since
    try:
        _own_mtimes[os.path.abspath(path)] = os.stat(path).st_mtime_ns
    except OSError:
        pass
    _pending_since = time.monotonic()


def edited_elsewhere_recently(path: str, quiet_seconds: float = None) -> bool:
    """True if ``path`` changed in the last ``quiet_seconds`` and the server didn't write it."""
    quiet = TASK_SYNC_QUIET_SECONDS if quiet_seconds is None else quiet_seconds
    if quiet <= 0:
        return False
    try:
        mtime_ns = os.stat(path).st_mtime_ns
    except OSError:
        return False
    if _own_mtimes.get(os.path.abspath(path)) == mtime_ns:
        return False
    return time.time() - mtime_ns / 1e9 < quiet


def _prune_old_backups():
    cutoff = datetime.now() - timedelta(days=TASK_BACKUP_RETENTION_DAYS)
    try:
        entries = os.listdir(TASK_BACKUP_PATH)
    except OSError:
        return
    for name in entries:
        full = os.path.join(TASK_BACKUP_PATH, name)
        try:
            if os.path.isdir(full) and datetime.fromtimestamp(os.path.getmtime(full)) < cutoff:
                shutil.rmtree(full)
        except OSError:
            pass


def backup_files(paths: list[str], label: str) -> str:
    """Copies each existing file in ``paths`` into a new dated backup folder; returns it."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_label = "".join(c if c.isalnum() or c in "-_." else "_" for c in label)[:80]
    dest = os.path.join(TASK_BACKUP_PATH, f"{stamp}_{safe_label}")
    os.makedirs(dest, exist_ok=True)
    for path in paths:
        if os.path.exists(path):
            shutil.copy2(path, os.path.join(dest, os.path.basename(path)))
    _prune_old_backups()
    return dest


def commit_task_snapshot(message: str) -> bool:
    """Commits the current state of 01_Tasks/ (only that folder). True if a commit was made."""
    from backend.utils.git import TASK_SNAPSHOT_PREFIX, commit_paths
    rel = os.path.relpath(TASKS_PATH, OBSIDIAN_VAULT_PATH)
    return commit_paths([rel], f"{TASK_SNAPSHOT_PREFIX} {message}")


async def maybe_commit_task_changes(delay_seconds: float = None):
    """Commits 01_Tasks/ once no server write has happened for ``delay_seconds``.

    A /clarify session writes the task files several times in a minute; waiting for them
    to settle turns that into one commit. With no writes (e.g. overnight) it does nothing.
    """
    global _pending_since
    delay = TASK_COMMIT_DELAY_SECONDS if delay_seconds is None else delay_seconds
    if _pending_since is None or time.monotonic() - _pending_since < delay:
        return
    _pending_since = None
    try:
        await asyncio.to_thread(commit_task_snapshot, "Task list changes")
    except Exception as e:
        print(f"Backend [Task History]: Snapshot commit failed: {e}")
