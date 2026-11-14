"""Household reference backfill: the reading half, on the home server.

``household_scan.py send`` (run on the laptop) leaves approved documents and a manifest
in ``HOUSEHOLD_STAGING_PATH``. ``process`` extracts their text locally, asks the model
for the key facts, writes one reference note each (pointing at the original's path on
the laptop) and adds dated reminders for renewals and expiries. Staged copies are
deleted once read. As a backfill it never runs without ``--approve``.

  python3 -m backend.feeds.household process [--dry-run] [--approve]
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ValidationError

from backend import config
from backend.feeds import ledger, llm, notes, state
from backend.feeds.dryrun import RunReport
from backend.prompts import load_prompt
from backend.utils.sanitize import sanitize_filename

FEED = "household"
MAX_OUTPUT_TOKENS = 768


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


@dataclass
class HouseholdResult:
    report: RunReport
    documents: int = 0
    estimate_pence: float = 0.0
    unreadable: list[str] = field(default_factory=list)     # staged names with no extractable text
    reminders: int = 0
    left_over: int = 0
    needs_approval: bool = False

    def text(self) -> str:
        lines = [f"household{' (dry run)' if self.report.dry_run else ''}: {self.documents} document(s) staged, "
                 f"estimated cost up to {ledger.format_pence(self.estimate_pence)}; "
                 f"{ledger.format_pence(max(ledger.remaining_pence(), 0))} of this month's budget is left."]
        if self.unreadable:
            lines.append(f"{len(self.unreadable)} have no readable text (probably scans) and will be skipped.")
        if self.needs_approval:
            lines.append("Nothing has been sent to the model. Run again with --approve to go ahead.")
        elif not self.report.dry_run:
            lines.append(f"Wrote {len(self.report.notes)} reference note(s) and {self.reminders} dated reminder(s) "
                         f"for {ledger.format_pence(self.report.cost_pence)}.")
            if self.left_over:
                lines.append(f"{self.left_over} left staged: the budget for this month is used.")
        return "\n".join(lines)


def load_manifest() -> list[dict]:
    try:
        with open(os.path.join(config.HOUSEHOLD_STAGING_PATH, "manifest.json"), encoding="utf-8") as f:
            return list(json.load(f)["documents"])
    except (OSError, KeyError, json.JSONDecodeError, TypeError):
        return []


def extract_text(path: str) -> str:
    """Text of a staged PDF or Word file, extracted locally (no model, no network)."""
    from backend.processing.extractor import extract_docx_text_from_bytes, extract_pdf_text_from_bytes
    try:
        with open(path, "rb") as f:
            data = f.read()
        text = extract_pdf_text_from_bytes(data) if path.lower().endswith(".pdf") else extract_docx_text_from_bytes(data)
    except Exception as e:
        print(f"Backend [Household]: could not extract text ({type(e).__name__}).")
        return ""
    return "" if text.startswith("Error extracting") else text.strip()


def candidate_folders() -> list[str]:
    from backend.vault.paths import list_areas
    return [f"03_Areas/{rel}" for rel in list_areas().values()]


def build_prompt(doc: dict, text: str) -> str:
    name = os.path.basename(doc["original_path"])
    return f"File name: {name}\nLast modified: {doc.get('modified', 'unknown')}\n\n" + llm.wrap_untrusted(
        text[:config.HOUSEHOLD_MAX_CHARS])


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


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


def add_reminders(d: HouseholdDoc, note_title: str, today: date) -> int:
    """A dated next action for each future renewal or expiry, hidden until it is near."""
    from backend.utils.tasks_dashboard import append_gtd_item
    from backend.vault.tasks import render_task_line
    added = 0
    for kd in d.key_dates:
        when = _parse_date(kd.date)
        if not when or when <= today or kd.kind not in ("renewal", "expiry"):
            continue
        verb = "Renew" if kd.kind == "renewal" else "Check expiry of"
        description = f"{verb} {d.what.strip()}" + (f" ({d.provider.strip()})" if d.provider.strip() else "")
        line = render_task_line(description, due=when, source=note_title,
                                tickler=max(today, when - timedelta(days=config.HOUSEHOLD_REMINDER_DAYS)))
        append_gtd_item(config.TASKS_MD_FILE, line)
        added += 1
    return added


async def process(*, approved: bool = False, dry_run: bool = False, today: date | None = None) -> HouseholdResult:
    today = today or datetime.now().date()
    result = HouseholdResult(RunReport(FEED, dry_run=dry_run))
    folders = candidate_folders()
    system = load_prompt("household_reference", today=today.isoformat(),
                         areas="\n".join(f"  - {f}" for f in folders) or "  (none)")

    todo = []
    for doc in load_manifest():
        staged = os.path.join(config.HOUSEHOLD_STAGING_PATH, os.path.basename(doc["staged_name"]))
        if not os.path.isfile(staged) or state.is_unchanged(FEED, doc["sha256"], doc["sha256"]):
            continue
        result.documents += 1
        text = extract_text(staged)
        if len(text) < 200:
            result.unreadable.append(doc["staged_name"])
            continue
        prompt = build_prompt(doc, text)
        todo.append((doc, staged, prompt))
        result.estimate_pence += llm.worst_case_pence(prompt, system, MAX_OUTPUT_TOKENS)

    if dry_run or not todo:
        result.report.cost_pence = result.estimate_pence
        result.report.model_calls = len(todo)
        return result
    if not approved:                         # a backfill always asks
        result.needs_approval = True
        return result

    for n, (doc, staged, prompt) in enumerate(todo):
        try:
            ledger.check_budget(FEED, llm.worst_case_pence(prompt, system, MAX_OUTPUT_TOKENS)
                                + config.FEEDS_BACKFILL_HEADROOM_PENCE)
            reply = await llm.generate(FEED, prompt, system, response_schema=HouseholdDoc,
                                       max_output_tokens=MAX_OUTPUT_TOKENS, report=result.report)
            parsed = HouseholdDoc.model_validate_json(reply or "")
            if not parsed.what.strip():
                raise ValueError("no document name")
            title = sanitize_filename(parsed.what)[:80]
            dest = parsed.area if parsed.area in folders else config.HOUSEHOLD_FALLBACK_DIR
            path = notes.write_feed_note(
                feed=FEED, source_id=doc["sha256"], note_type="household-reference", title=title,
                body=render_note(doc, parsed), dest_dir=dest, original_date=doc.get("modified") or today,
                source_hash=doc["sha256"], extra_frontmatter={"original_path": doc["original_path"]},
                report=result.report)
        except ledger.BudgetExceeded as e:
            print(f"Backend [Household]: stopping early: {e}")
            result.left_over = len(todo) - n
            break
        except (ValidationError, ValueError):
            print(f"Backend [Household]: unusable model reply for {doc['staged_name']}; left staged.")
            continue
        if path:
            note_title = os.path.splitext(os.path.basename(path))[0]
            result.reminders += add_reminders(parsed, note_title, today)
        os.remove(staged)                    # the copy has served its purpose; the original is untouched
    return result


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] != "process" or set(args[1:]) - {"--dry-run", "--approve"}:
        sys.exit("usage: python3 -m backend.feeds.household process [--dry-run] [--approve]")
    print(asyncio.run(process(approved="--approve" in args, dry_run="--dry-run" in args)).text())
