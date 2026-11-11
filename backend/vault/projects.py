"""Project management for the GTD web app: listing, roadmaps, archiving.

Parsing of project notes stays in :mod:`backend.vault.tasks`; this module holds the
operations on them, as :mod:`backend.vault.actions` does for the action lists. The
Telegram ``/archive`` command and step promotion call the same functions.

A project is identified by its name (the note's filename); a roadmap step by its exact
text. As with actions, something that changed or vanished since the client last looked
makes the operation skip rather than guess. Everything here is synchronous.
"""

import os
import re
import shutil
from datetime import date, datetime

from backend.config import OBSIDIAN_VAULT_PATH, TASKS_MD_FILE
from backend.utils.sanitize import sanitize_filename
from backend.utils.tasks_dashboard import append_gtd_item
from backend.vault import actions
from backend.vault.actions import OpResult, OpSkipped
from backend.vault.tasks import (
    FRONTMATTER_RE,
    PROJECT_STEPS_RE,
    Project,
    build_project_note,
    dump_frontmatter,
    iter_projects,
    parse_frontmatter,
    parse_task_line,
    projects_root,
    read_project,
    render_task_line,
)
from backend.vault.writer import atomic_write

CATEGORY_DIRS = ("Personal", "Professional")
_DONE_STATUSES = ("completed", "archived")


def archive_root() -> str:
    return os.path.join(OBSIDIAN_VAULT_PATH, "05_Archive", "02_Projects")


def norm(name: str) -> str:
    """How a project name and a ``#project/Tag`` are compared."""
    return (name or "").strip().replace(" ", "_").lower()


def _title(category: str | None) -> str:
    return "Professional" if (category or "").strip().lower() == "professional" else "Personal"


def _tidy(text) -> str:
    if not isinstance(text, str):
        raise OpSkipped("invalid")
    return re.sub(r"\s+", " ", text).strip()


# --- Reading -------------------------------------------------------------------------

def _open_actions() -> dict[str, list[dict]]:
    """Open Next and Waiting actions, grouped by normalised project tag."""
    grouped: dict[str, list[dict]] = {}
    for list_name in ("next", "waiting"):
        for line in actions._read(list_name):
            if not actions._is_open(line):
                continue
            item = actions._item_json(list_name, line)
            if item["project"]:
                grouped.setdefault(norm(item["project"]), []).append({**item, "list": list_name})
    return grouped


def _project_json(project: Project, open_actions: dict) -> dict:
    mine = open_actions.get(norm(project.name), [])
    due = project.due_date
    return {
        "name": project.name,
        "category": project.category.lower(),
        "outcome": "" if project.outcome == "No outcome defined" else project.outcome,
        "due": due.isoformat() if due else None,
        "steps": project.steps,
        "actions": mine,
        # The weekly-review check: a live project with nothing you could do next.
        "stuck": not mine,
    }


def iter_archived_projects():
    """Yield ``(Project, entry_path)`` for everything under ``05_Archive/02_Projects``."""
    for category in CATEGORY_DIRS:
        cat_dir = os.path.join(archive_root(), category)
        if not os.path.isdir(cat_dir):
            continue
        for entry in sorted(os.listdir(cat_dir)):
            if entry.startswith((".", "_")):
                continue
            entry_path = os.path.join(cat_dir, entry)
            if os.path.isdir(entry_path):
                note = os.path.join(entry_path, f"{entry}.md")
            elif entry.endswith(".md"):
                note = entry_path
            else:
                continue
            project = read_project(note)
            if project:
                project.category = category
                yield project, entry_path
            elif os.path.isdir(entry_path):
                # A folder archived without a master note is still a project to list.
                yield Project(name=entry, path=note, category=category), entry_path


def list_projects() -> dict:
    open_actions = _open_actions()
    active = [_project_json(p, open_actions) for p in iter_projects() if p.status not in _DONE_STATUSES]
    archived = []
    for project, _ in iter_archived_projects():
        done = project.frontmatter.get("date_completed")
        archived.append({
            "name": project.name,
            "category": project.category.lower(),
            "outcome": "" if project.outcome == "No outcome defined" else project.outcome,
            "completed": str(done) if done else None,
        })
    archived.sort(key=lambda p: p["completed"] or "", reverse=True)
    return {"active": active, "archived": archived}


def _find(name: str) -> Project:
    for project in iter_projects():
        if project.name == name and project.status not in _DONE_STATUSES:
            return project
    raise OpSkipped("project_not_found")


# --- Note rewriting ------------------------------------------------------------------

def _edit_frontmatter(path: str, change) -> None:
    """Apply ``change(dict)`` to a note's frontmatter, through the YAML helpers."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    fm = parse_frontmatter(text)
    body = FRONTMATTER_RE.sub("", text, count=1).lstrip("\n") if FRONTMATTER_RE.match(text) else text
    change(fm)
    atomic_write(path, dump_frontmatter(fm) + body)


def _set_steps(path: str, new_steps: list[str]) -> None:
    """Rewrite the unchecked items of ``## Project Steps`` to ``new_steps``, in order.

    A step that is unchanged keeps its original line, so anything the parser strips for
    display (a source link, say) is not lost by reordering. Ticked items and any other
    lines in the section are kept, after the steps.
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()

    m = PROJECT_STEPS_RE.search(text)
    original: dict[str, list[str]] = {}
    others: list[str] = []
    if m:
        for line in m.group(1).splitlines():
            task = parse_task_line(line)
            if task and not task.checked and task.description:
                original.setdefault(task.description, []).append(line.rstrip())
            elif line.strip():
                others.append(line.rstrip())

    lines = []
    for step in new_steps:
        kept = original.get(step)
        lines.append(kept.pop(0) if kept else f"- [ ] {step}")
    section = "\n".join(lines + others)

    if m:
        text = text[:m.start(1)] + section + ("\n" if section else "") + text[m.end(1):]
    else:
        text = text.rstrip("\n") + f"\n\n## Project Steps\n{section}\n"
    atomic_write(path, text)


# --- Operations ----------------------------------------------------------------------

def create_project(fields: dict) -> OpResult:
    name = sanitize_filename(_tidy(fields.get("name")), default="")
    if not name:
        raise OpSkipped("invalid")
    category = _title(fields.get("category"))
    due = fields.get("due_date") or ""
    if due and not actions._is_iso_date(due):
        raise OpSkipped("bad_date")
    if any(norm(p.name) == norm(name) for p in iter_projects()):
        raise OpSkipped("project_exists")

    folder = os.path.join(projects_root(), category, name)
    os.makedirs(folder, exist_ok=True)
    outcome = _tidy(fields.get("outcome") or "") or None
    atomic_write(os.path.join(folder, f"{name}.md"), build_project_note(name, category, outcome, due))
    return OpResult(new_raw=name)


def update_project(name: str, fields: dict) -> OpResult:
    project = _find(name)
    changes = {}
    if "outcome" in fields:
        changes["outcome"] = _tidy(fields["outcome"] or "")
    if "due_date" in fields:
        due = fields["due_date"] or ""
        if due and not actions._is_iso_date(due):
            raise OpSkipped("bad_date")
        changes["due_date"] = due
    if not changes:
        return OpResult()

    old_outcome = project.frontmatter.get("outcome")
    _edit_frontmatter(project.path, lambda fm: fm.update(changes))

    # The template repeats the outcome under "## Desired Outcome"; keep that in step, but
    # only when it still says exactly what the frontmatter said (never overwrite prose
    # the user has written there).
    if "outcome" in changes and old_outcome:
        with open(project.path, encoding="utf-8") as f:
            text = f.read()
        marker = f"## Desired Outcome\n{old_outcome}\n"
        if text.count(marker) == 1:
            atomic_write(project.path, text.replace(marker, f"## Desired Outcome\n{changes['outcome']}\n"))
    return OpResult()


def add_step(name: str, text: str) -> OpResult:
    project = _find(name)
    step = _tidy(text)
    if not step:
        raise OpSkipped("invalid")
    _set_steps(project.path, project.steps + [step])
    return OpResult(new_raw=step)


def _step_index(project: Project, step: str | None) -> int:
    try:
        return project.steps.index((step or "").strip())
    except ValueError:
        raise OpSkipped("not_found") from None


def edit_step(name: str, step: str, text: str) -> OpResult:
    project = _find(name)
    i = _step_index(project, step)
    new = _tidy(text)
    if not new:
        raise OpSkipped("invalid")
    steps = list(project.steps)
    steps[i] = new
    _set_steps(project.path, steps)
    return OpResult(new_raw=new)


def remove_step(name: str, step: str) -> OpResult:
    project = _find(name)
    steps = list(project.steps)
    steps.pop(_step_index(project, step))
    _set_steps(project.path, steps)
    return OpResult()


def reorder_steps(name: str, order: list[str]) -> OpResult:
    """Same tolerance as reordering actions: unknown steps are ignored, steps the client
    did not know about keep their relative order after the ones it placed."""
    project = _find(name)
    remaining = list(project.steps)
    placed = []
    for step in order:
        step = step.strip()
        if step in remaining:
            remaining.remove(step)
            placed.append(step)
    if not placed:
        raise OpSkipped("not_found")
    _set_steps(project.path, placed + remaining)
    return OpResult()


def promote_project_step(project_path: str, project_tag: str, category: str, step: str,
                         contexts: list[str] | None = None) -> str:
    """Move a roadmap step into Next Actions. Shared with Telegram's promotion prompt.

    Next Actions is written first: if the second write fails the step exists twice, which
    is visible and harmless; the other order could lose it.
    """
    line = render_task_line(step, contexts=contexts, project=project_tag,
                            category=_title(category).lower())
    append_gtd_item(TASKS_MD_FILE, line)
    project = read_project(project_path)
    if project and step in project.steps:
        steps = list(project.steps)
        steps.remove(step)
        _set_steps(project_path, steps)
    return line


def promote_step(name: str, step: str, fields: dict) -> OpResult:
    project = _find(name)
    _step_index(project, step)  # e.g. already promoted from the Telegram prompt
    contexts = fields.get("contexts")
    actions._validate_fields({"contexts": contexts} if contexts is not None else {})
    line = promote_project_step(project.path, project.tag, project.category, step.strip(), contexts)
    return OpResult(new_raw=line)


def _entry_of(project: Project) -> str:
    """The thing to move: the project's folder, or the note itself if it has none."""
    folder = os.path.dirname(project.path)
    return folder if os.path.basename(folder) == project.name else project.path


def archive_project_path(entry_path: str, category: str, when: date | None = None) -> str:
    """Mark a project completed and move it to ``05_Archive/02_Projects/<Category>/``.

    ``entry_path`` is the project's folder or its single note. Returns the new path.
    Raises ``FileExistsError`` if the archive already holds something of that name: this
    used to delete the existing archive folder and replace it.
    """
    dest_dir = os.path.join(archive_root(), _title(category))
    dest = os.path.join(dest_dir, os.path.basename(entry_path))
    if os.path.exists(dest):
        raise FileExistsError(dest)

    name = os.path.basename(entry_path).removesuffix(".md")
    note = entry_path if os.path.isfile(entry_path) else os.path.join(entry_path, f"{name}.md")
    if os.path.isfile(note):
        today = (when or datetime.now().date()).strftime("%Y-%m-%d")
        _edit_frontmatter(note, lambda fm: fm.update({"status": "completed", "date_completed": today}))

    os.makedirs(dest_dir, exist_ok=True)
    shutil.move(entry_path, dest)
    return dest


def archive_project(name: str) -> OpResult:
    project = _find(name)
    try:
        archive_project_path(_entry_of(project), project.category)
    except FileExistsError:
        raise OpSkipped("archive_exists") from None
    return OpResult()


def restore_project(name: str, category: str | None) -> OpResult:
    """Bring an archived project back as active."""
    wanted = _title(category)
    for project, entry_path in iter_archived_projects():
        if project.name == name and project.category == wanted:
            break
    else:
        raise OpSkipped("project_not_found")

    dest = os.path.join(projects_root(), wanted, os.path.basename(entry_path))
    if os.path.exists(dest):
        raise OpSkipped("project_exists")
    if os.path.isfile(project.path):
        def reactivate(fm: dict):
            fm["status"] = "active"
            fm.pop("date_completed", None)
        _edit_frontmatter(project.path, reactivate)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(entry_path, dest)
    return OpResult()
