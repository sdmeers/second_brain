"""Single source of truth for reading and writing GTD task lines and project notes.

Before this module existed, the task-line format documented in
``docs/technical_implementation.md`` §2 was re-implemented with ad-hoc regexes in
``engage.py``, ``clarify.py``, ``tasks_dashboard.py``, ``instruction.py`` and
``watcher.py``. Conventions drifted between those copies (``Completed.md`` vs
``z_Completed.md``, ``00_System/Tasks.md`` vs ``01_Tasks/Next_Actions.md``), which is
how several silent failures got in. New code should parse and render task lines only
through the helpers here.

Line format::

    - [ ] <description> @<context> #project/<name> #<category> 📅 <due> 🔔 <tickler> (Source: [[...]])
"""

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from collections.abc import Iterator

import yaml

from backend.config import (
    COMPLETED_TASKS_MD_FILE,
    OBSIDIAN_VAULT_PATH,
)
from backend.vault.writer import atomic_write

# --- Line-level regexes (the single place these patterns are written down) -----------

CHECKBOX_RE = re.compile(r'^\s*-\s*\[(?P<mark>[ xXvV])\]\s*(?P<body>.*)$')
BULLET_RE = re.compile(r'^\s*-\s+(?P<body>.*)$')
CONTEXT_RE = re.compile(r'(?<!\S)(@[A-Za-z][\w\-]*)')
PROJECT_RE = re.compile(r'#project/([A-Za-z0-9_\-]+)')
CATEGORY_RE = re.compile(r'#(personal|professional)\b', re.IGNORECASE)
WAITING_RE = re.compile(r'#waiting\(([^)]*)\)')
DUE_RE = re.compile(r'📅\s*(\d{4}-\d{2}-\d{2})')
TICKLER_RE = re.compile(r'🔔\s*(\d{4}-\d{2}-\d{2})')
SOURCE_RE = re.compile(r'\(Source:\s*\[\[(.*?)\]\]\)')
CAPTURED_RE = re.compile(r'\(Captured[^)]*\)')
COMPLETED_ON_RE = re.compile(r'\(Completed on (\d{4}-\d{2}-\d{2})\)')
STRIKETHROUGH_RE = re.compile(r'~~\s*(.*?)\s*~~', re.DOTALL)

COMPLETED_HEADER = (
    "# ✅ Completed Tasks Archive\n\n"
    "> Archive of completed actions and quick 2-minute tasks. View in Telegram using `/done`.\n\n"
    "---\n\n"
    "## ✅ Completed Tasks\n"
)


@dataclass
class Task:
    """A parsed task line. ``raw`` is always the original text, verbatim."""

    raw: str
    description: str
    checked: bool = False
    contexts: list[str] = field(default_factory=list)
    project: str | None = None
    category: str | None = None
    waiting_on: str | None = None
    due: date | None = None
    tickler: date | None = None
    source: str | None = None
    completed_on: date | None = None

    @property
    def is_deferred(self) -> bool:
        """True when a tickler date hides this task from today's lists."""
        return self.tickler is not None and self.tickler > datetime.now().date()

    @property
    def display(self) -> str:
        """Just the prose, with structured fields stripped, for showing to a human.

        ``description`` deliberately keeps the inline tags so callers can still match on
        them; this is the presentation form used in Telegram summaries.
        """
        text = self.description
        text = CONTEXT_RE.sub("", text)
        text = PROJECT_RE.sub("", text)
        text = CATEGORY_RE.sub("", text)
        text = WAITING_RE.sub("", text)
        text = DUE_RE.sub("", text)
        text = TICKLER_RE.sub("", text)
        return re.sub(r'\s{2,}', " ", text).strip(" -–—")


def _parse_iso(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def strip_task_metadata(line: str) -> str:
    """Return the human-readable description with checkbox, bullet and trailing
    metadata removed.

    Replaces the ``.strip("- [ ]\\n")`` idiom that was used in a dozen places.
    ``str.strip`` takes a *character set*, so that idiom silently corrupted any task
    whose text began or ended with ``[``, ``]``, ``-`` or a space -- e.g.
    ``[Draft] review notes`` lost its leading bracket.
    """
    text = line.strip()
    m = CHECKBOX_RE.match(text)
    if m:
        text = m.group("body")
    else:
        m = BULLET_RE.match(text)
        if m:
            text = m.group("body")

    # Unwrap ~~strikethrough~~ used on completed items
    m_strike = STRIKETHROUGH_RE.search(text)
    if m_strike:
        text = m_strike.group(1)

    text = CAPTURED_RE.sub("", text)
    text = SOURCE_RE.sub("", text)
    text = COMPLETED_ON_RE.sub("", text)
    return re.sub(r'\s{2,}', " ", text).strip()


def parse_task_line(line: str) -> Task | None:
    """Parse one markdown line into a :class:`Task`, or None if it is not a task line."""
    m = CHECKBOX_RE.match(line.rstrip("\n"))
    if not m:
        return None

    raw = line.rstrip("\n")
    body = m.group("body")
    checked = m.group("mark").lower() in ("x", "v")

    m_proj = PROJECT_RE.search(body)
    m_cat = CATEGORY_RE.search(body)
    m_wait = WAITING_RE.search(body)
    m_due = DUE_RE.search(body)
    m_tick = TICKLER_RE.search(body)
    m_src = SOURCE_RE.search(body)
    m_done = COMPLETED_ON_RE.search(body)

    return Task(
        raw=raw,
        description=strip_task_metadata(raw),
        checked=checked,
        contexts=CONTEXT_RE.findall(body),
        project=m_proj.group(1) if m_proj else None,
        category=m_cat.group(1).lower() if m_cat else None,
        waiting_on=m_wait.group(1) if m_wait else None,
        due=_parse_iso(m_due.group(1)) if m_due else None,
        tickler=_parse_iso(m_tick.group(1)) if m_tick else None,
        source=m_src.group(1) if m_src else None,
        completed_on=_parse_iso(m_done.group(1)) if m_done else None,
    )


def render_task_line(
    description: str,
    contexts: list[str] = None,
    project: str = None,
    category: str = None,
    due: date = None,
    tickler: date = None,
    source: str = None,
    checked: bool = False,
) -> str:
    """Render a task line in the canonical documented field order."""
    parts = [f"- [{'x' if checked else ' '}]", description.strip()]
    for ctx in contexts or []:
        parts.append(ctx if ctx.startswith("@") else f"@{ctx}")
    if project:
        parts.append(f"#project/{project.strip().replace(' ', '_')}")
    if category:
        parts.append(f"#{category.strip().lower()}")
    if due:
        parts.append(f"📅 {due.strftime('%Y-%m-%d')}")
    if tickler:
        parts.append(f"🔔 {tickler.strftime('%Y-%m-%d')}")
    if source:
        parts.append(f"(Source: [[{source}]])")
    return " ".join(parts)


def iter_task_lines(path: str, include_checked: bool = True) -> Iterator[Task]:
    """Yield every parsed task in a GTD file. Missing files yield nothing."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            task = parse_task_line(line)
            if task and (include_checked or not task.checked):
                yield task


def format_completed_line(line: str, when: date = None) -> str:
    """Normalise any task line into the archive's ``- [x] ~~text~~ (Completed on ...)`` form."""
    when = when or datetime.now().date()
    if COMPLETED_ON_RE.search(line):
        return line.strip()
    description = strip_task_metadata(line)
    return f"- [x] ~~{description}~~ (Completed on {when.strftime('%Y-%m-%d')})"


def archive_completed_lines(lines: list[str], when: date = None) -> int:
    """Prepend completed task lines to ``z_Completed.md`` in reverse-chronological order.

    Shared by /done, the clarify 2-minute rule, the dashboard sweeper and the
    instruction agent, so every path produces the format /status and /reflect parse.
    Returns the number of lines archived.
    """
    if not lines:
        return 0

    existing: list[str] = []
    if os.path.exists(COMPLETED_TASKS_MD_FILE):
        try:
            with open(COMPLETED_TASKS_MD_FILE, encoding="utf-8") as f:
                for line in f:
                    if line.strip().lower().startswith("- [x]"):
                        existing.append(line.strip())
        except OSError as e:
            print(f"Vault [Tasks]: Could not read completed archive: {e}")

    formatted = [format_completed_line(line, when) for line in lines]
    atomic_write(COMPLETED_TASKS_MD_FILE, COMPLETED_HEADER + "\n".join(formatted + existing) + "\n")
    return len(formatted)


# --- Project notes -------------------------------------------------------------------

PROJECT_STEPS_RE = re.compile(r'## Project Steps\s*\n(.*?)(?=\n## |\Z)', re.DOTALL)
FRONTMATTER_RE = re.compile(r'^---\s*\n(.*?)\n---', re.DOTALL)


@dataclass
class Project:
    name: str
    path: str
    category: str = "Personal"
    frontmatter: dict = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return str(self.frontmatter.get("status", "active")).lower()

    @property
    def due_date(self) -> date | None:
        return parse_flexible_date(self.frontmatter.get("due_date"))

    @property
    def outcome(self) -> str:
        return str(self.frontmatter.get("outcome", "") or "No outcome defined")

    @property
    def tag(self) -> str:
        return self.name.strip().replace(" ", "_")


def parse_flexible_date(value) -> date | None:
    """Parse a frontmatter date that may be ISO, DD-MM-YYYY, or already a date."""
    # datetime is a subclass of date, so it must be tested first.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = str(value).strip().strip('"').strip("'")
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_frontmatter(text: str) -> dict:
    """Return the YAML frontmatter of a note as a dict ({} when absent or malformed)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def dump_frontmatter(data: dict) -> str:
    """Serialise a frontmatter dict to a ``---``-delimited YAML block.

    Use this instead of f-string interpolation: a title containing a quote or colon
    produces invalid YAML that Obsidian and ``yaml.safe_load`` both reject.
    """
    body = yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return f"---\n{body}---\n"


def parse_project_steps(text: str) -> list[str]:
    """Return the unchecked ``## Project Steps`` roadmap items, cleaned."""
    m = PROJECT_STEPS_RE.search(text)
    if not m:
        return []
    steps = []
    for line in m.group(1).splitlines():
        task = parse_task_line(line)
        if task and not task.checked and task.description:
            steps.append(task.description)
    return steps


def projects_root() -> str:
    return os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")


def find_project_note(name: str) -> str | None:
    """Locate a project's master note by name.

    Resolution order matches what the handlers previously each re-implemented:
    exact ``<Name>.md`` filename, then a directory named ``<Name>`` containing
    ``<Name>.md``, then a substring match on the filename.
    """
    if not name:
        return None
    root = projects_root()
    if not os.path.isdir(root):
        return None

    wanted = {
        name.strip().lower(),
        name.strip().replace("_", " ").lower(),
        name.strip().replace(" ", "_").lower(),
        name.strip().replace("_", " ").title().lower(),
    }

    # 1. exact filename
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.endswith(".md") and f[:-3].lower() in wanted:
                return os.path.join(dirpath, f)

    # 2. directory name match containing <Dir>.md
    for dirpath, dirnames, _ in os.walk(root):
        for d in dirnames:
            if d.lower() in wanted:
                candidate = os.path.join(dirpath, d, f"{d}.md")
                if os.path.exists(candidate):
                    return candidate

    # 3. substring fallback
    needle = name.strip().lower()
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.endswith(".md") and needle in f[:-3].lower():
                return os.path.join(dirpath, f)

    return None


def read_project(path: str) -> Project | None:
    """Load a project master note into a :class:`Project`."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"Vault [Tasks]: Could not read project note {path}: {e}")
        return None

    fm = parse_frontmatter(text)
    category = str(fm.get("category") or ("Professional" if "Professional" in path else "Personal"))
    return Project(
        name=os.path.basename(path)[:-3],
        path=path,
        category=category,
        frontmatter=fm,
        steps=parse_project_steps(text),
    )


def iter_projects(categories: tuple = ("Personal", "Professional")) -> Iterator[Project]:
    """Yield every project master note under the given category folders."""
    skip = {"archive", "docs", "Notes", "Meetings", "Outputs", "Sessions", "old", "Syncthing"}
    for category in categories:
        cat_dir = os.path.join(projects_root(), category)
        if not os.path.isdir(cat_dir):
            continue
        for entry in sorted(os.listdir(cat_dir)):
            if entry.startswith((".", "_")) or entry in skip:
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
                yield project


def build_project_note(name: str, category: str = "Personal", outcome: str = None,
                       due_date: str = "") -> str:
    """Render a new project master note.

    Frontmatter goes through yaml.safe_dump, so a project name containing a quote or
    colon can no longer produce a note Obsidian and yaml.safe_load both reject.
    """
    proj_clean = name.strip()
    proj_tag = proj_clean.replace(" ", "_")
    outcome_str = outcome or f"{proj_clean} project goals and desired outcome"

    frontmatter = dump_frontmatter({
        "title": proj_clean,
        "type": "project",
        "category": category,
        "outcome": outcome_str,
        "date_created": datetime.now().strftime("%Y-%m-%d"),
        "due_date": due_date,
        "status": "active",
    })

    return f"""{frontmatter}# {proj_clean}

## Desired Outcome
{outcome_str}

## Project Steps

## Active Actions
> View via Telegram: `/project {proj_clean}` or search `#project/{proj_tag}` in Obsidian.

```dataview
TASK
FROM "01_Tasks"
WHERE contains(tags, "#project/{proj_tag}") AND !completed
```

## Reference Material
> Notes and resources tagged with `#project/{proj_tag}` across the vault.
"""


def ensure_project_note(name: str, category: str = "Personal", outcome: str = None) -> str:
    """Return the path to a project's master note, creating it from the template if absent.

    Consolidates the near-identical creation logic that lived in both
    clarify.ensure_project_note_exists and engage.handle_project_command.
    """
    existing = find_project_note(name)
    if existing:
        if outcome:
            try:
                with open(existing, encoding="utf-8") as f:
                    text = f.read()
                fm = parse_frontmatter(text)
                if fm:
                    fm["outcome"] = outcome
                    body = FRONTMATTER_RE.sub("", text, count=1).lstrip("\n")
                    atomic_write(existing, dump_frontmatter(fm) + body)
            except OSError as e:
                print(f"Vault [Tasks]: Could not update outcome on {existing}: {e}")
        return existing

    proj_clean = name.strip().title()
    target_folder = os.path.join(projects_root(), category, proj_clean)
    os.makedirs(target_folder, exist_ok=True)
    note_path = os.path.join(target_folder, f"{proj_clean}.md")
    if not os.path.exists(note_path):
        atomic_write(note_path, build_project_note(proj_clean, category, outcome))
        print(f"Vault [Tasks]: Created project master note {note_path}")
    return note_path


def append_project_step(project_name: str, step_text: str) -> str:
    """Append a roadmap step under ``## Project Steps``. Returns the note path."""
    note_path = ensure_project_note(project_name)
    with open(note_path, encoding="utf-8") as f:
        content = f.read()

    clean_step = strip_task_metadata(step_text)
    if "## Project Steps" in content:
        content = content.replace("## Project Steps", f"## Project Steps\n- [ ] {clean_step}", 1)
    else:
        content += f"\n\n## Project Steps\n- [ ] {clean_step}\n"

    atomic_write(note_path, content)
    return note_path


def remove_step_from_project(project_path: str, step_text: str) -> bool:
    """Remove one roadmap step from a project note. Returns True if a line was removed."""
    if not project_path or not os.path.exists(project_path):
        return False
    with open(project_path, encoding="utf-8") as f:
        lines = f.readlines()

    target = strip_task_metadata(step_text).lower()
    out, removed = [], False
    for line in lines:
        if not removed:
            task = parse_task_line(line)
            if task and not task.checked:
                candidate = task.description.lower()
                if candidate == target or target in candidate or candidate in target:
                    removed = True
                    continue
        out.append(line)

    if removed:
        atomic_write(project_path, "".join(out))
    return removed
