"""Household reference backfill: the reading half, on the home server.

``household_scan.py send`` (run on the laptop) leaves the ticked files and a manifest in
``HOUSEHOLD_STAGING_PATH``. The manifest holds bundles: a ticked file becomes one
reference note, and a ticked folder becomes one note about everything in it (summary,
key facts, timeline, what's outstanding, and every file, including photos and videos that
were listed but never copied). ``process`` extracts text locally (``feeds/readers.py``),
asks the model, files the notes and adds dated reminders. Sending a folder again later
refreshes its note and keeps anything written in it by hand. Staged copies are deleted
once read. As a backfill it never runs without ``--approve``.

  python3 -m backend.feeds.household process [--dry-run] [--approve]
"""

import asyncio
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ValidationError

from backend import config
from backend.feeds import ledger, llm, notes, readers, state
from backend.feeds.dryrun import RunReport
from backend.prompts import load_prompt
from backend.utils.sanitize import sanitize_filename

FEED = "household"
MAX_OUTPUT_TOKENS = 768
FOLDER_OUTPUT_TOKENS = 2048
SUMMARY_OUTPUT_TOKENS = 400
# Generated text sits between these, so a refresh keeps whatever is written around it.
START, END = "<!-- household:start -->", "<!-- household:end -->"
BUNDLE_ID = re.compile(r"^b\d{3}$")


class KeyDate(BaseModel):
    label: str = ""
    date: str = ""
    kind: str = "other"


class HouseholdDoc(BaseModel):
    what: str = ""
    provider: str = ""
    reference: str = ""
    key_dates: list[KeyDate] = []
    summary: str = ""
    area: str = ""


class Event(BaseModel):
    date: str = ""
    event: str = ""


class Outstanding(BaseModel):
    item: str = ""
    due: str = ""


class FileNote(BaseModel):
    file: int = 0
    about: str = ""


class FolderNote(BaseModel):
    title: str = ""
    summary: str = ""
    status: str = ""
    key_facts: list[str] = []
    timeline: list[Event] = []
    outstanding: list[Outstanding] = []
    key_dates: list[KeyDate] = []
    files: list[FileNote] = []
    area: str = ""


@dataclass
class Staged:
    """One staged file of a bundle. ``text`` is empty when it wasn't read (see ``why``)."""
    number: int
    entry: dict
    path: str
    text: str = ""
    why: str = ""


@dataclass
class Bundle:
    manifest: dict
    staged: list[Staged] = field(default_factory=list)
    summarise: bool = False          # too long for one call: condense each file first

    @property
    def is_folder(self) -> bool:
        return self.manifest.get("kind") == "folder"

    @property
    def readable(self) -> list[Staged]:
        return [s for s in self.staged if s.text]

    @property
    def unreadable(self) -> list[Staged]:
        return [s for s in self.staged if s.why == "unreadable"]

    @property
    def source_id(self) -> str:
        """From the original's path, hashed: the path itself may hold a reference number."""
        digest = hashlib.sha256(self.manifest["original_path"].encode("utf-8")).hexdigest()[:16]
        return f"{self.manifest.get('kind') or 'file'}:{digest}"

    @property
    def content_hash(self) -> str:
        parts = sorted(f.get("sha256", "") for f in self.manifest["files"])
        parts += sorted(f"{f.get('name')}|{f.get('size')}|{f.get('modified')}" for f in self.manifest.get("listed", []))
        return notes.content_hash(*parts)


@dataclass
class HouseholdResult:
    report: RunReport
    folders: int = 0
    files: int = 0                       # single-file items
    files_to_read: int = 0
    estimate_pence: float = 0.0
    unreadable: list[str] = field(default_factory=list)     # staged names with no extractable text
    empty: int = 0                       # items with nothing readable at all
    unchanged: int = 0
    reminders: int = 0
    left_over: int = 0
    needs_approval: bool = False

    @property
    def documents(self) -> int:
        return self.folders + self.files

    def text(self) -> str:
        what = ", ".join(p for p in (f"{self.folders} folder(s)" if self.folders else "",
                                     f"{self.files} single file(s)" if self.files else "") if p) or "nothing new"
        lines = [f"household{' (dry run)' if self.report.dry_run else ''}: {what} staged, "
                 f"{self.files_to_read} file(s) to read; estimated cost up to {ledger.format_pence(self.estimate_pence)}; "
                 f"{ledger.format_pence(max(ledger.remaining_pence(), 0))} of this month's budget is left."]
        if self.unchanged:
            lines.append(f"{self.unchanged} item(s) are unchanged since they were last read and are skipped.")
        if self.unreadable:
            lines.append(f"{len(self.unreadable)} file(s) have no readable text (probably scans); "
                         "notes list them as not read.")
        if self.empty:
            lines.append(f"{self.empty} item(s) have nothing readable at all and are skipped.")
        if self.needs_approval:
            lines.append("Nothing has been sent to the model. Run again with --approve to go ahead.")
        elif not self.report.dry_run:
            lines.append(f"Wrote {len(self.report.notes)} note(s) and {self.reminders} dated reminder(s) "
                         f"for {ledger.format_pence(self.report.cost_pence)}.")
            if self.left_over:
                lines.append(f"{self.left_over} item(s) left staged: the budget for this month is used.")
        return "\n".join(lines)


# --- manifest -------------------------------------------------------------------------------

def load_bundles() -> list[dict]:
    """The manifest's bundles. A first-version manifest (single files only) is converted."""
    try:
        with open(os.path.join(config.HOUSEHOLD_STAGING_PATH, "manifest.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("bundles"), list):
        return [b for b in data["bundles"]
                if isinstance(b, dict) and b.get("files") and isinstance(b.get("original_path"), str)]
    return [{"kind": "file", "original_path": d["original_path"], "listed": [],
             "files": [{**d, "name": os.path.basename(d["original_path"])}]}
            for d in data.get("documents") or [] if isinstance(d, dict) and d.get("original_path")]


def _staged_path(entry: dict) -> str | None:
    """The staged copy's path, refusing any name that would leave the staging folder."""
    parts = str(entry.get("staged_name", "")).split("/")
    if len(parts) > 2 or any(p in ("", ".", "..") for p in parts) or (len(parts) == 2 and not BUNDLE_ID.match(parts[0])):
        return None
    return os.path.join(config.HOUSEHOLD_STAGING_PATH, *parts)


def read_bundle(manifest: dict) -> Bundle:
    b, seen = Bundle(manifest), set()
    for n, entry in enumerate(manifest["files"], 1):
        path = _staged_path(entry)
        if not path or not os.path.isfile(path):
            continue
        r = readers.read_file(path)
        if not readers.readable_enough(path, r.text):
            b.staged.append(Staged(n, entry, path, why="unreadable"))
        elif r.same_as and r.same_as in seen:
            b.staged.append(Staged(n, entry, path, why="duplicate"))
        else:
            if r.same_as:
                seen.add(r.same_as)
            b.staged.append(Staged(n, entry, path, r.text))
    return b


def remove_staged(b: Bundle) -> None:
    """The copies have served their purpose; the originals on the laptop are untouched."""
    for s in b.staged:
        if os.path.isfile(s.path):
            os.remove(s.path)
    folder = os.path.dirname(b.staged[0].path) if b.staged else ""
    if folder and BUNDLE_ID.match(os.path.basename(folder)) and not os.listdir(folder):
        os.rmdir(folder)


# --- prompts --------------------------------------------------------------------------------

def candidate_folders() -> list[str]:
    from backend.vault.paths import list_areas
    return [f"03_Areas/{rel}" for rel in list_areas().values()]


def build_prompt(doc: dict, text: str) -> str:
    name = os.path.basename(doc["original_path"])
    return f"File name: {name}\nLast modified: {doc.get('modified', 'unknown')}\n\n" + llm.wrap_untrusted(
        text[:config.HOUSEHOLD_MAX_CHARS])


def _name(entry: dict) -> str:
    return entry.get("name") or os.path.basename(entry["original_path"])


def _label(s: Staged) -> str:
    return f"File {s.number}: {_name(s.entry)} ({s.entry.get('kind') or 'file'}, " \
           f"last changed {s.entry.get('modified', 'unknown')})"


def folder_prompt(b: Bundle, texts: dict[int, str]) -> str:
    """Every readable file's text (or its condensed version), then what was not read."""
    parts = [f"Folder: {os.path.basename(b.manifest['original_path'])}", ""]
    for s in b.readable:
        parts += [_label(s), llm.wrap_untrusted(texts[s.number]), ""]
    others = ([f"{_label(s)}: no readable text" for s in b.unreadable]
              + [f"{_name(f)} ({f.get('kind')}, last changed {f.get('modified')}): not read"
                 for f in b.manifest.get("listed", [])])
    if others:
        parts += ["Also in the folder:", *others]
    return "\n".join(parts)


def summary_prompt(s: Staged) -> str:
    return _label(s) + "\n\n" + llm.wrap_untrusted(s.text[:config.HOUSEHOLD_MAX_CHARS])


# --- notes ----------------------------------------------------------------------------------

def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


def keep_hand_written(rel_path: str | None, generated: str) -> str:
    """The new generated block, with whatever was written above or below the old one."""
    block = f"{START}\n{generated.strip()}\n{END}"
    old = notes.existing_body(rel_path)
    if not old:
        return block
    if START in old and END in old:
        before, rest = old.split(START, 1)
        return f"{before}{block}{rest.split(END, 1)[1]}".strip()
    return f"{block}\n\n{old}"


def render_note(doc: dict, d: HouseholdDoc) -> str:
    lines = [d.summary.strip() or "(no summary)", ""]
    if d.provider.strip():
        lines.append(f"- **Provider:** {d.provider.strip()}")
    if d.reference.strip():
        lines.append(f"- **Reference:** {d.reference.strip()}")
    for kd in d.key_dates:
        if _parse_date(kd.date) and kd.label.strip():
            lines.append(f"- **{kd.label.strip()}:** {kd.date.strip()}")
    lines += [f"- **Original:** `{doc['original_path']}` (on the laptop, last changed {doc.get('modified', 'unknown')})"]
    return "\n".join(lines)


def _file_line(entry: dict, about: str = "") -> str:
    return f"- `{_name(entry)}` ({entry.get('kind') or 'file'}, {entry.get('modified', 'unknown')})" \
           + (f": {about}" if about else "")


def render_folder_note(b: Bundle, d: FolderNote) -> str:
    lines = [d.summary.strip() or "(no summary)", ""]
    if d.status.strip():
        lines += [f"**Status:** {d.status.strip()}", ""]
    facts = [f.strip() for f in d.key_facts if f.strip()]
    if facts:
        lines += ["## Key facts", *[f"- {f}" for f in facts], ""]
    events = sorted((e for e in d.timeline if _parse_date(e.date) and e.event.strip()), key=lambda e: e.date)
    if events:
        lines += ["## Timeline", *[f"- **{e.date.strip()}:** {e.event.strip()}" for e in events], ""]
    todo = [o for o in d.outstanding if o.item.strip()]
    if todo:
        lines += ["## Outstanding",
                  *[f"- {o.item.strip()}" + (f" (by {o.due.strip()})" if _parse_date(o.due) else "") for o in todo], ""]

    about = {f.file: f.about.strip() for f in d.files}
    listed = b.manifest.get("listed", [])
    lines += ["## Files", f"Folder on the laptop: `{b.manifest['original_path']}`", ""]
    lines += [_file_line(s.entry, about.get(s.number, "")) for s in b.readable]
    lines += [_file_line(f) for f in listed]

    not_read = [_file_line(s.entry, "no readable text (probably a scan)") for s in b.unreadable]
    not_read += [_file_line(s.entry, "the same email as another file here") for s in b.staged if s.why == "duplicate"]
    if listed:
        kinds = ", ".join(f"{n} {k}{'s' if n > 1 else ''}" for k, n in Counter(f.get("kind") or "file" for f in listed).items())
        not_read.insert(0, f"- {kinds}: listed above and kept on the laptop, never copied or read")
    if not_read:
        lines += ["", "## Not read", *not_read]
    return "\n".join(lines)


def add_reminders(key_dates: list[KeyDate], what: str, provider: str, note_title: str, today: date,
                  outstanding: list[Outstanding] = (), source_id: str = "") -> int:
    """A dated next action for each future renewal, expiry or dated outstanding item, hidden
    until it is near. A refreshed note never adds the same reminder twice."""
    from backend.utils.tasks_dashboard import append_gtd_item
    from backend.vault.tasks import render_task_line
    wanted = []
    for kd in key_dates:
        when = _parse_date(kd.date)
        if when and when > today and kd.kind in ("renewal", "expiry"):
            verb = "Renew" if kd.kind == "renewal" else "Check expiry of"
            wanted.append((f"{verb} {what.strip()}" + (f" ({provider.strip()})" if provider.strip() else ""), when))
    for o in outstanding:
        when = _parse_date(o.due)
        if when and when > today and o.item.strip():
            wanted.append((f"{o.item.strip()} ({what.strip()})", when))

    key = f"household:reminders:{source_id}" if source_id else ""
    done = set(json.loads(state.get_value(key) or "[]")) if key else set()
    added = 0
    for description, when in wanted:
        mark = hashlib.sha256(f"{description}|{when}".encode()).hexdigest()[:16]
        if mark in done:
            continue
        line = render_task_line(description, due=when, source=note_title,
                                tickler=max(today, when - timedelta(days=config.HOUSEHOLD_REMINDER_DAYS)))
        append_gtd_item(config.TASKS_MD_FILE, line)
        done.add(mark)
        added += 1
    if key and added:
        state.set_value(key, json.dumps(sorted(done)))
    return added


# --- the run --------------------------------------------------------------------------------

def plan(b: Bundle, system: str, summary_system: str) -> float:
    """Worst-case pence for a bundle. Decides whether a folder must be condensed first."""
    if not b.is_folder:
        return llm.worst_case_pence(build_prompt(b.readable[0].entry, b.readable[0].text), system, MAX_OUTPUT_TOKENS)
    full = folder_prompt(b, {s.number: s.text for s in b.readable})
    b.summarise = len(full) > config.HOUSEHOLD_MAX_CHARS
    if not b.summarise:
        return llm.worst_case_pence(full, system, FOLDER_OUTPUT_TOKENS)
    pence = sum(llm.worst_case_pence(summary_prompt(s), summary_system, SUMMARY_OUTPUT_TOKENS) for s in b.readable)
    condensed = folder_prompt(b, {s.number: "x" * (SUMMARY_OUTPUT_TOKENS * 4) for s in b.readable})
    return pence + llm.worst_case_pence(condensed, system, FOLDER_OUTPUT_TOKENS)


def _destination(area: str, folders: list[str]) -> str:
    return area if area in folders else config.HOUSEHOLD_FALLBACK_DIR


async def file_note(b: Bundle, system: str, folders: list[str], today: date, result: HouseholdResult) -> None:
    s = b.readable[0]
    reply = await llm.generate(FEED, build_prompt(s.entry, s.text), system, response_schema=HouseholdDoc,
                               max_output_tokens=MAX_OUTPUT_TOKENS, report=result.report)
    parsed = HouseholdDoc.model_validate_json(reply or "")
    if not parsed.what.strip():
        raise ValueError("no document name")
    seen = state.get_seen(FEED, b.source_id)
    path = notes.write_feed_note(
        feed=FEED, source_id=b.source_id, note_type="household-reference", title=sanitize_filename(parsed.what)[:80],
        body=keep_hand_written(seen and seen["note_path"], render_note(s.entry, parsed)),
        dest_dir=_destination(parsed.area, folders), original_date=s.entry.get("modified") or today,
        source_hash=b.content_hash, extra_frontmatter={"original_path": s.entry["original_path"]},
        report=result.report)
    if path:
        result.reminders += add_reminders(parsed.key_dates, parsed.what, parsed.provider,
                                          os.path.splitext(os.path.basename(path))[0], today, source_id=b.source_id)


async def folder_note(b: Bundle, system: str, summary_system: str, folders: list[str], today: date,
                      result: HouseholdResult) -> None:
    texts = {}
    for s in b.readable:
        if b.summarise:
            condensed = await llm.generate(FEED, summary_prompt(s), summary_system,
                                           max_output_tokens=SUMMARY_OUTPUT_TOKENS, report=result.report)
            texts[s.number] = (condensed or "").strip() or "(could not be condensed)"
        else:
            texts[s.number] = s.text
    reply = await llm.generate(FEED, folder_prompt(b, texts), system, response_schema=FolderNote,
                               max_output_tokens=FOLDER_OUTPUT_TOKENS, report=result.report)
    parsed = FolderNote.model_validate_json(reply or "")
    if not parsed.title.strip():
        raise ValueError("no title")
    seen = state.get_seen(FEED, b.source_id)
    dates = [f["modified"] for f in b.manifest["files"] if f.get("modified")]
    path = notes.write_feed_note(
        feed=FEED, source_id=b.source_id, note_type="household-folder", title=sanitize_filename(parsed.title)[:80],
        body=keep_hand_written(seen and seen["note_path"], render_folder_note(b, parsed)),
        dest_dir=_destination(parsed.area, folders), original_date=max(dates) if dates else today,
        source_hash=b.content_hash,
        extra_frontmatter={"original_path": b.manifest["original_path"], "status": parsed.status.strip() or "unknown"},
        report=result.report)
    if path:
        result.reminders += add_reminders(parsed.key_dates, parsed.title, "",
                                          os.path.splitext(os.path.basename(path))[0], today,
                                          outstanding=parsed.outstanding, source_id=b.source_id)


async def process(*, approved: bool = False, dry_run: bool = False, today: date | None = None) -> HouseholdResult:
    today = today or datetime.now().date()
    result = HouseholdResult(RunReport(FEED, dry_run=dry_run))
    folders = candidate_folders()
    areas = "\n".join(f"  - {f}" for f in folders) or "  (none)"
    file_system = load_prompt("household_reference", today=today.isoformat(), areas=areas)
    folder_system = load_prompt("household_folder", today=today.isoformat(), areas=areas)
    summary_system = load_prompt("household_file_summary", today=today.isoformat())

    def system_for(b: Bundle) -> str:
        return folder_system if b.is_folder else file_system

    todo = []
    for manifest in load_bundles():
        b = read_bundle(manifest)
        if not b.staged:
            continue
        if state.is_unchanged(FEED, b.source_id, b.content_hash):
            result.unchanged += 1
            if approved and not dry_run:
                remove_staged(b)
            continue
        result.unreadable += [s.entry.get("staged_name", "") for s in b.unreadable]
        if not b.readable:
            result.empty += 1
            continue
        if b.is_folder:
            result.folders += 1
        else:
            result.files += 1
        result.files_to_read += len(b.readable)
        result.estimate_pence += plan(b, system_for(b), summary_system)
        todo.append(b)

    if dry_run or not todo:
        result.report.cost_pence = result.estimate_pence
        return result
    if not approved:                         # a backfill always asks
        result.needs_approval = True
        return result

    for n, b in enumerate(todo):
        try:
            ledger.check_budget(FEED, plan(b, system_for(b), summary_system) + config.FEEDS_BACKFILL_HEADROOM_PENCE)
            if b.is_folder:
                await folder_note(b, folder_system, summary_system, folders, today, result)
            else:
                await file_note(b, file_system, folders, today, result)
        except ledger.BudgetExceeded as e:
            print(f"Backend [Household]: stopping early: {e}")
            result.left_over = len(todo) - n
            break
        except (ValidationError, ValueError):
            print(f"Backend [Household]: unusable model reply for item {n + 1}; left staged.")
            continue
        remove_staged(b)
    return result


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] != "process" or set(args[1:]) - {"--dry-run", "--approve"}:
        sys.exit("usage: python3 -m backend.feeds.household process [--dry-run] [--approve]")
    print(asyncio.run(process(approved="--approve" in args, dry_run="--dry-run" in args)).text())
