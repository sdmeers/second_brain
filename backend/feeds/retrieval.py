"""Small retrieval additions for the feeds, all behind ``ENABLE_FEED_RETRIEVAL``.

- ``task_chunks``: lets chat see the open task lists, which the vector index leaves out.
- ``ensure_index_columns`` / ``tag_indexed_file``: store each note's frontmatter ``type``
  and ``date`` beside its chunks. Nothing reads these yet; they are there so date-aware
  search can be added later without re-indexing the vault.
"""

import os
import re

from backend import config
from backend.vault.tasks import parse_frontmatter

_TASK_QUESTION = re.compile(
    r"\b(promis\w*|commit\w*|outstanding|overdue|action\w*|tasks?|to-?dos?|to do|waiting|"
    r"owe|chase|follow(?:ing)? up|haven'?t done|not done|next actions?)\b", re.I)

# (file, heading shown to the model); open items only, newest captures included.
_TASK_FILES = (("Next_Actions.md", "Open next actions"),
               ("Waiting_For.md", "Waiting for others"),
               ("Captured.md", "Captured, not yet clarified"))
_MAX_CHARS_PER_LIST = 6000


def wants_tasks(query: str) -> bool:
    return bool(_TASK_QUESTION.search(query))


def task_chunks(query: str) -> list[dict]:
    """Context chunks holding the open task lines, or [] when the question isn't about tasks.

    Lines are passed through exactly as stored: nothing here writes to the task files.
    """
    if not wants_tasks(query):
        return []
    chunks = []
    for filename, heading in _TASK_FILES:
        path = os.path.join(config.TASKS_PATH, filename)
        try:
            with open(path, encoding="utf-8") as f:
                open_lines = [ln.rstrip("\n") for ln in f if ln.lstrip().startswith("- [ ]")]
        except OSError:
            continue
        if not open_lines:
            continue
        text = f"{heading}:\n" + "\n".join(open_lines)
        chunks.append({"file_path": f"01_Tasks/{filename}", "chunk_index": 0,
                       "text": text[:_MAX_CHARS_PER_LIST], "distance": 0.0})
    return chunks


def ensure_index_columns(cursor) -> None:
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(brain_chunks)")}
    for column in ("note_type", "note_date"):
        if column not in existing:
            cursor.execute(f"ALTER TABLE brain_chunks ADD COLUMN {column} TEXT")


def note_metadata(content: str) -> tuple[str | None, str | None]:
    """(type, date) from a note's frontmatter; either may be None."""
    fm = parse_frontmatter(content) if isinstance(content, str) else {}
    note_type, note_date = fm.get("type"), fm.get("date")
    return (str(note_type) if note_type else None, str(note_date) if note_date else None)


def tag_indexed_file(cursor, vault_relative_path: str, content: str) -> None:
    note_type, note_date = note_metadata(content)
    if note_type or note_date:
        cursor.execute("UPDATE brain_chunks SET note_type = ?, note_date = ? WHERE file_path = ?",
                       (note_type, note_date, vault_relative_path))
