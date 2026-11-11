"""Where a captured item ends up once it has been clarified.

The Telegram ``/clarify`` conversation (``ingestion/handlers/clarify.py``) and the GTD web
app's clarify sheet (``vault/actions.py``) are two front ends to the same decisions. The
writes live here so the two cannot drift: each function files one captured line into its
destination and then removes it from ``Captured.md``.

Every function takes the captured line exactly as it appears in the file, and an optional
``text`` that replaces its wording (the app lets you fix a transcription while filing).
All are synchronous; see ``vault/actions.py`` for why that matters.
"""

import os
import re
import shutil
from datetime import date, datetime

from backend.config import (
    CAPTURED_MD_FILE,
    INBOX_ARCHIVE_PATH,
    INBOX_PATH,
    SOMEDAY_MAYBE_MD_FILE,
    TASKS_MD_FILE,
    WAITING_FOR_MD_FILE,
)
from backend.utils.tasks_dashboard import append_gtd_item, is_placeholder_line, render_captured_file
from backend.vault.tasks import (
    SOURCE_RE,
    append_project_step,
    archive_completed_lines,
    ensure_project_note,
    render_task_line,
    strip_task_metadata,
    update_task_line,
)
from backend.vault.writer import atomic_write


def captured_line(text: str, when: datetime | str | None = None, via: str = "GTD app",
                  source: str | None = None) -> str:
    """The ``Captured.md`` line for a new capture.

    The web app builds the identical string on the device (``webapp/src/optimistic.ts``)
    so that an item captured offline can be clarified before it has ever synced; keep the
    two in step.
    """
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        raise ValueError("Nothing to capture")
    if isinstance(when, str):
        stamp = when
    else:
        stamp = (when or datetime.now()).strftime("%Y-%m-%d %H:%M")
    line = f"- {clean} (Captured on {stamp} via {via})"
    if source:
        line += f" (Source: [[{source}]])"
    return line


def add_capture(text: str, when: datetime | str | None = None, via: str = "GTD app",
                source: str | None = None) -> str:
    """Append a new item to ``Captured.md`` and return the line written."""
    line = captured_line(text, when, via, source)
    append_gtd_item(CAPTURED_MD_FILE, line)
    return line


def remove_captured(item_line: str) -> str | None:
    """Take a clarified item out of ``Captured.md`` and archive its inbox source file.

    Returns the source filename, if the line had one.

    The line is matched exactly and whole. This used to be ``content.replace(line, "")``
    on the file's text, which left a blank line behind each time and would also cut the
    same words out of the middle of any longer item that happened to contain them.
    """
    target = item_line.strip()
    m = SOURCE_RE.search(target)
    filename = m.group(1) if m else None

    if filename:
        src_file = os.path.join(INBOX_PATH, filename)
        if os.path.isfile(src_file):
            os.makedirs(INBOX_ARCHIVE_PATH, exist_ok=True)
            shutil.move(src_file, os.path.join(INBOX_ARCHIVE_PATH, filename))

    if os.path.exists(CAPTURED_MD_FILE):
        with open(CAPTURED_MD_FILE, encoding="utf-8") as f:
            items = [s for s in (ln.strip() for ln in f)
                     if s.startswith("- ") and not is_placeholder_line(s)]
        if target in items:
            items.remove(target)
            atomic_write(CAPTURED_MD_FILE, render_captured_file(items))

    return filename


def _wording(item_line: str, text: str | None) -> str:
    clean = re.sub(r"\s+", " ", text).strip() if text else strip_task_metadata(item_line)
    if not clean:
        raise ValueError("A task needs a description")
    return clean


def _category(category: str | None) -> str:
    return "professional" if (category or "").strip().lower() == "professional" else "personal"


def _as_date(value: date | str | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _action_line(item_line: str, text: str | None, category: str | None, *, contexts=None,
                 project: str | None = None, tickler=None) -> str:
    if project:
        ensure_project_note(project, category=_category(category).title())
    return render_task_line(
        _wording(item_line, text),
        contexts=contexts,
        project=project,
        category=_category(category),
        tickler=_as_date(tickler),
    )


def to_next(item_line: str, *, category: str | None = None, contexts: list[str] | None = None,
            project: str | None = None, tickler: date | str | None = None, text: str | None = None) -> str:
    """File as a next action. A ``tickler`` date is Telegram's "Defer / Tickle"."""
    line = _action_line(item_line, text, category, contexts=contexts, project=project, tickler=tickler)
    append_gtd_item(TASKS_MD_FILE, line)
    remove_captured(item_line)
    return line


def to_waiting(item_line: str, *, waiting_on: str, category: str | None = None,
               project: str | None = None, tickler: date | str | None = None, text: str | None = None) -> str:
    """File under Waiting For, recording who it is with."""
    line = _action_line(item_line, text, category, project=project, tickler=tickler)
    line = update_task_line(line, waiting_on=(waiting_on or "").strip() or "Someone")
    append_gtd_item(WAITING_FOR_MD_FILE, line)
    remove_captured(item_line)
    return line


def to_someday(item_line: str, *, category: str | None = None, text: str | None = None) -> str:
    line = _action_line(item_line, text, category)
    append_gtd_item(SOMEDAY_MAYBE_MD_FILE, line)
    remove_captured(item_line)
    return line


def to_project_step(item_line: str, *, project: str, text: str | None = None) -> str:
    """Add to an existing project's roadmap (``## Project Steps``)."""
    step = _wording(item_line, text)
    append_project_step(project, step)
    remove_captured(item_line)
    return step


def to_new_project(item_line: str, *, name: str, category: str | None = None, outcome: str | None = None,
                   first_action: str | None = None, contexts: list[str] | None = None,
                   text: str | None = None, note_path: str | None = None) -> str:
    """Create a project from a captured item.

    With ``first_action`` the project starts with that next action, so it is never left
    without one. Without it (the Telegram flow, which does not ask) the captured item
    itself becomes the first roadmap step, as before.

    Returns the path of the project note.
    """
    name = re.sub(r"\s+", " ", name or "").strip()
    if not name:
        raise ValueError("A project needs a name")
    cat = _category(category)
    # The app creates the note itself (keeping the name as typed) and passes its path;
    # Telegram goes through ensure_project_note, which title-cases a new name.
    note_path = note_path or ensure_project_note(name, category=cat.title(),
                                                 outcome=(outcome or "").strip() or None)

    first_action = re.sub(r"\s+", " ", first_action or "").strip()
    if first_action:
        append_gtd_item(TASKS_MD_FILE, render_task_line(
            first_action, contexts=contexts, project=os.path.basename(note_path)[:-3], category=cat))
    else:
        append_project_step(os.path.basename(note_path)[:-3], _wording(item_line, text))
    remove_captured(item_line)
    return note_path


def to_done(item_line: str, *, text: str | None = None) -> None:
    """The two-minute rule: it's already done, so just log it."""
    archive_completed_lines([f"- [x] {_wording(item_line, text)}" if text else item_line])
    remove_captured(item_line)


def to_reference(item_line: str, *, text: str | None = None) -> None:
    """Hand to the AI Librarian for filing as reference material."""
    from backend.ingestion.queue import add_to_queue

    content = _wording(item_line, text)
    filename = remove_captured(item_line)
    add_to_queue(source_type="file_drop",
                 raw_payload={"filename": filename or "reference_note.md", "type": "reference_note"},
                 content_text=content)


def trash(item_line: str) -> None:
    remove_captured(item_line)
