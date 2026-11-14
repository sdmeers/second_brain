"""Raw source material (exports, archives) kept apart from notes.

Stored under ``00_System/Inbox/Raw/<feed>/``, which Syncthing does not sync and the RAG
index does not read. Originals are copied, never moved or modified.
"""

import os
import shutil

from backend import config
from backend.feeds.dryrun import RunReport
from backend.utils.sanitize import sanitize_filename


def store_raw(feed: str, source_path: str, report: RunReport | None = None, dry_run: bool = False) -> str:
    """Copy a file into the feed's raw store and return the destination path."""
    dest_dir = os.path.join(config.FEEDS_RAW_PATH, sanitize_filename(feed))
    dest = os.path.join(dest_dir, os.path.basename(source_path))
    if report:
        report.raw_files.append(dest)
    if not dry_run:
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(source_path, dest)
    return dest
