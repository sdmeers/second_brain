"""List-level operations on the GTD action files, for the actions web app.

Task lines carry no IDs, so every operation names its target by the line's exact text
(``raw``) as the client last saw it. If Telegram, Obsidian or another device changed or
removed that line in the meantime the operation is *skipped* rather than guessed at, and
the client refreshes. That is what makes it safe to replay edits queued while offline.

Every function here is synchronous on purpose: called from an ``async def`` endpoint
with no ``await`` between the read and the write, an operation cannot interleave with
the dashboard sweeper or a Telegram handler running on the same event loop.

Line parsing and rendering stay in :mod:`backend.vault.tasks`; file layout (headers, the
Personal / Professional split) stays in :mod:`backend.utils.tasks_dashboard`.
"""

import os
import re
from dataclasses import dataclass
from datetime import date, datetime

from backend.config import (
    CAPTURED_MD_FILE,
    SOMEDAY_MAYBE_MD_FILE,
    TASKS_MD_FILE,
    WAITING_FOR_MD_FILE,
)
from backend.utils.tasks_dashboard import (
    ensure_category_tag,
    is_placeholder_line,
    render_captured_file,
    render_split_task_file,
    resolve_relative_due,
)
from backend.vault.tasks import (
    CAPTURED_RE,
    CHECKBOX_RE,
    UNSET,
    archive_completed_lines,
    parse_task_line,
    strip_task_metadata,
    update_task_line,
)
from backend.vault.writer import atomic_write

CAPTURED = "captured"
LIST_FILES = {
    CAPTURED: CAPTURED_MD_FILE,
    "next": TASKS_MD_FILE,
    "waiting": WAITING_FOR_MD_FILE,
    "someday": SOMEDAY_MAYBE_MD_FILE,
}
ACTION_LISTS = ("next", "waiting", "someday")
CATEGORIES = ("personal", "professional")


class OpSkipped(Exception):
    """The operation could not be applied; ``reason`` is a short machine-readable code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class OpResult:
    new_raw: str | None = None
    """The line's text after an edit or move, so the client can keep addressing it."""
    project: str | None = None
    """Set by ``complete`` when the action belonged to a project."""


def category_of(line: str) -> str:
    """Same rule as ``split_by_category``: untagged lines count as personal."""
    return "professional" if "#professional" in line else "personal"


def _read(list_name: str) -> list[str]:
    """Item lines of a list in file order, normalised exactly as the sweeper writes them."""
    path = LIST_FILES[list_name]
    if not os.path.exists(path):
        return []
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if is_placeholder_line(s):
                continue
            if list_name == CAPTURED:
                if s.startswith("- "):
                    items.append(s)
            elif s.startswith("- ["):
                m = CHECKBOX_RE.match(s)
                if m and m.group("mark") == " ":
                    s = resolve_relative_due(ensure_category_tag(s))
                # Ticked lines are left exactly as they are for the sweeper to archive.
                items.append(s)
    return items


def _write(list_name: str, items: list[str]) -> None:
    path = LIST_FILES[list_name]
    if list_name == CAPTURED:
        content = render_captured_file(items)
    else:
        content = render_split_task_file(os.path.basename(path), items)
    atomic_write(path, content)


def _is_open(line: str) -> bool:
    m = CHECKBOX_RE.match(line)
    return bool(m) and m.group("mark") == " "


def _index(items: list[str], raw: str) -> int:
    try:
        return items.index(raw.strip())
    except ValueError:
        raise OpSkipped("not_found") from None


def _require_action_list(list_name: str) -> None:
    if list_name not in ACTION_LISTS:
        raise OpSkipped("list_not_editable")


# --- Reading -------------------------------------------------------------------------

def _item_json(list_name: str, line: str) -> dict:
    if list_name == CAPTURED:
        m = CAPTURED_RE.search(line)
        return {"raw": line, "text": strip_task_metadata(line), "note": m.group(0).strip("()") if m else None}
    task = parse_task_line(line)
    return {
        "raw": line,
        "text": task.display,
        "category": category_of(line),
        "contexts": task.contexts,
        "project": task.project,
        "waiting_on": task.waiting_on,
        "due": task.due.isoformat() if task.due else None,
        "tickler": task.tickler.isoformat() if task.tickler else None,
        "deferred": task.is_deferred,
    }


def read_action_lists() -> dict:
    """Every list as JSON-ready dicts, in file (priority) order. Ticked lines are omitted."""
    lists = {}
    for name in LIST_FILES:
        lines = _read(name)
        if name != CAPTURED:
            lines = [ln for ln in lines if _is_open(ln)]
        lists[name] = [_item_json(name, ln) for ln in lines]
    return {"today": datetime.now().date().isoformat(), "lists": lists}


# --- Operations ----------------------------------------------------------------------

_CONTEXT_NAME_RE = re.compile(r"@?[A-Za-z][\w\-]*")


def _is_iso_date(value) -> bool:
    try:
        return isinstance(value, str) and bool(date.fromisoformat(value))
    except ValueError:
        return False


def _validate_fields(changes: dict) -> None:
    """Reject anything that would write a malformed line. Values arrive from JSON."""
    if "category" in changes and changes["category"] not in CATEGORIES:
        raise OpSkipped("bad_category")
    for key in ("due", "tickler"):
        if changes.get(key) is not None and not _is_iso_date(changes[key]):
            raise OpSkipped("bad_date")
    if "description" in changes and not isinstance(changes["description"], str):
        raise OpSkipped("invalid")
    if changes.get("waiting_on") is not None and not isinstance(changes["waiting_on"], str):
        raise OpSkipped("invalid")
    contexts = changes.get("contexts")
    if contexts is not None and not (
        isinstance(contexts, list)
        and all(isinstance(c, str) and _CONTEXT_NAME_RE.fullmatch(c) for c in contexts)
    ):
        raise OpSkipped("bad_context")

def complete_action(list_name: str, raw: str) -> OpResult:
    """Remove the line and archive it, exactly as Telegram ``/done`` does."""
    _require_action_list(list_name)
    items = _read(list_name)
    line = items.pop(_index(items, raw))
    task = parse_task_line(line)
    # Archive first: if the process dies between the two writes the action shows up in
    # both places, which is recoverable; the other order would lose it.
    archive_completed_lines([line])
    _write(list_name, items)
    return OpResult(project=task.project if task else None)


def edit_action(list_name: str, raw: str, fields: dict) -> OpResult:
    """Change fields of one line in place. ``fields`` holds only the keys to change.

    Captured items are unclarified, so only their text may be edited; ``/clarify`` in
    Telegram stays the one place they get contexts, dates and a category.
    """
    if list_name not in LIST_FILES:
        raise OpSkipped("unknown_list")
    allowed = {"description"} if list_name == CAPTURED else {
        "description", "contexts", "category", "waiting_on", "due", "tickler"}
    changes = {k: v for k, v in fields.items() if k in allowed}
    _validate_fields(changes)

    items = _read(list_name)
    i = _index(items, raw)
    try:
        new_line = update_task_line(items[i], **{k: changes.get(k, UNSET) for k in allowed})
    except ValueError:
        raise OpSkipped("invalid") from None

    if changes.get("category") and category_of(new_line) != category_of(items[i]):
        # Changed sides: arrive at the top of the other category rather than wherever
        # the old position happens to fall.
        items.pop(i)
        items.insert(0, new_line)
    else:
        items[i] = new_line
    _write(list_name, items)
    return OpResult(new_raw=new_line)


def move_action(src: str, dst: str, raw: str, waiting_on: str | None = None,
                follow_up: str | None = None) -> OpResult:
    """Move a line between Next / Waiting / Someday, landing at the top of its category.

    Moving into Waiting records who it is with (``#waiting(Name)``) and optionally a 🔔
    follow-up date; moving out of Waiting drops the ``#waiting`` tag.
    """
    _require_action_list(src)
    _require_action_list(dst)
    if src == dst:
        raise OpSkipped("same_list")
    if follow_up and not _is_iso_date(follow_up):
        raise OpSkipped("bad_date")

    src_items = _read(src)
    i = _index(src_items, raw)
    line = src_items[i]
    if not _is_open(line):
        raise OpSkipped("not_found")

    if dst == "waiting":
        line = update_task_line(line, waiting_on=waiting_on or None,
                                tickler=follow_up if follow_up else UNSET)
    elif src == "waiting":
        line = update_task_line(line, waiting_on=None)

    dst_items = _read(dst)
    dst_items.insert(0, line)
    src_items.pop(i)
    # Destination first: a crash between the writes leaves a duplicate, not a loss.
    _write(dst, dst_items)
    _write(src, src_items)
    return OpResult(new_raw=line)


def reorder_actions(list_name: str, category: str, order: list[str]) -> OpResult:
    """Put one category of a list into the given order.

    ``order`` is the client's view, which may be stale: lines it names that no longer
    exist are ignored, and lines it does not know about (added since it last loaded)
    keep their relative order after the ones it placed.
    """
    _require_action_list(list_name)
    if category not in CATEGORIES:
        raise OpSkipped("bad_category")

    items = _read(list_name)
    mine = [ln for ln in items if category_of(ln) == category]
    others = [ln for ln in items if category_of(ln) != category]

    remaining = list(mine)
    placed = []
    for raw in order:
        raw = raw.strip()
        if raw in remaining:
            remaining.remove(raw)
            placed.append(raw)
    if not placed:
        raise OpSkipped("not_found")

    _write(list_name, placed + remaining + others)
    return OpResult()
