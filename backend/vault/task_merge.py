"""Merges a Syncthing conflict copy of a GTD task file back into the original.

Syncthing chooses which copy stays as the original by modification time and device ID,
not by content, so neither copy can be trusted to be "the" list. The previous healer
kept the original's open tasks and took only ticked ``[x]`` lines from the conflict
copy; on 2026-09-29 the original was an empty list and every open task, sitting in the
conflict copy, was deleted.

The merge here is a union:

- an open task on either side is kept (the original's wording wins if both have it);
- a task ticked on either side is completed, even if the other side still has it open;
- a task deleted on one side only comes back. Without a common ancestor a deletion
  can't be told apart from an addition, and a duplicate is far cheaper than a loss.
"""

import re
from dataclasses import dataclass, field

from backend.vault.tasks import (
    CONTEXT_RE,
    DUE_RE,
    TICKLER_RE,
    WAITING_RE,
    parse_task_line,
    strip_task_metadata,
)

MERGEABLE_TASK_FILES = ("Next_Actions.md", "Waiting_For.md", "Someday_Maybe.md", "Captured.md")

_PLACEHOLDER_MARKERS = ("🎉", "Inbox Zero", "All clear", "List clear", "*None*")
_TAG_RE = re.compile(r'(?<!\S)#[^\s#]+')


def canonical_task_file_name(name: str) -> str | None:
    """The canonical spelling of a mergeable task file name, matched case-insensitively."""
    for candidate in MERGEABLE_TASK_FILES:
        if candidate.lower() == name.lower():
            return candidate
    return None


def task_key(line: str) -> str:
    """Identity of a task for matching across copies: its words, without checkbox,
    contexts, tags, dates or provenance, so a re-tagged task still matches itself."""
    text = strip_task_metadata(line)
    text = WAITING_RE.sub("", text)
    text = DUE_RE.sub("", text)
    text = TICKLER_RE.sub("", text)
    text = CONTEXT_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def split_items(text: str, fname: str) -> tuple[list[str], list[str]]:
    """Returns (open_items, completed_items) as stripped lines."""
    open_items, completed = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or any(marker in line for marker in _PLACEHOLDER_MARKERS):
            continue
        if fname == "Captured.md":
            if line.startswith("- "):
                open_items.append(line)
            continue
        task = parse_task_line(line)
        if task is None:
            continue
        (completed if task.checked else open_items).append(line)
    return open_items, completed


@dataclass
class MergeResult:
    content: str
    open_items: list[str]
    completed: list[str]
    # Open tasks that only the conflict copy had -- the ones the old healer discarded.
    recovered: list[str] = field(default_factory=list)
    # Open tasks from either copy that are neither open nor completed in the result.
    # Always empty unless the merge itself is buggy; the healer refuses to write if not.
    lost: list[str] = field(default_factory=list)


def find_lost_items(sources: list[str], result_text: str, archived: list[str], fname: str) -> list[str]:
    """Open items in any of ``sources`` that are neither open in ``result_text`` nor in
    ``archived``. A write that would lose one of these must not happen."""
    kept = {task_key(line) for line in split_items(result_text, fname)[0]}
    kept |= {task_key(line) for line in archived}
    lost, seen = [], set()
    for text in sources:
        for line in split_items(text, fname)[0]:
            key = task_key(line)
            if key and key not in kept and key not in seen:
                seen.add(key)
                lost.append(line)
    return lost


def _render(fname: str, items: list[str]) -> str:
    from backend.utils.tasks_dashboard import (
        SPLIT_FILES,
        ensure_category_tag,
        render_captured_file,
        render_split_task_file,
    )
    if fname in SPLIT_FILES:
        return render_split_task_file(fname, [ensure_category_tag(line) for line in items])
    return render_captured_file(items)


def merge_task_file(fname: str, original_text: str, conflict_text: str) -> MergeResult:
    """Union-merges two copies of ``fname`` (one of MERGEABLE_TASK_FILES)."""
    orig_open, orig_done = split_items(original_text, fname)
    conf_open, conf_done = split_items(conflict_text, fname)

    completed, done_keys = [], set()
    for line in orig_done + conf_done:
        key = task_key(line)
        if key not in done_keys:
            done_keys.add(key)
            completed.append(line)

    open_items, seen = [], set()
    for line in orig_open + conf_open:
        key = task_key(line)
        if key in done_keys or key in seen:
            continue
        seen.add(key)
        open_items.append(line)

    orig_keys = {task_key(line) for line in orig_open}
    recovered = [line for line in open_items if line in conf_open and task_key(line) not in orig_keys]

    content = _render(fname, open_items)
    lost = find_lost_items([original_text, conflict_text], content, completed, fname)
    return MergeResult(content=content, open_items=open_items, completed=completed,
                       recovered=recovered, lost=lost)
