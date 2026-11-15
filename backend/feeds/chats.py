"""AI conversation summaries from the providers' official data exports.

Drop an export zip into ``CHAT_EXPORTS_PATH``. A timer runs ``poll`` every 15 minutes,
which moves the zip into the raw store, works out what qualifies and what it could cost
(no tokens spent), and asks for approval over Telegram when the rules require it. Each
qualifying conversation then becomes one summary note.

One parser per provider, all returning ``Conversation`` objects. A parser must refuse
(``UnrecognisedExport``) anything that does not look exactly as expected, instead of guessing.

Run as a module:
  python3 -m backend.feeds.chats poll
  python3 -m backend.feeds.chats process <export.zip> [--dry-run] [--approve]
"""

import asyncio
import json
import os
import re
import shutil
import sys
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC

from pydantic import BaseModel, ValidationError

from backend import config
from backend.feeds import approvals, ledger, llm, notes, notify, state
from backend.feeds.dryrun import RunReport
from backend.prompts import load_prompt
from backend.utils.sanitize import sanitize_filename

FEED = "chats"
SUMMARY_MAX_OUTPUT_TOKENS = 1024
EXCLUSIONS_TEMPLATE = """# Chat exclusions

> Conversations matching any rule below are never summarised or sent to a model.
> Write one rule per line under this box. Lines starting with `#` or `>` are ignored.
>
> - `title: words` skips conversations whose title contains the words
> - `project: name` skips every conversation in that project
> - any other line is a keyword: skips conversations whose title or text contains it

"""


class UnrecognisedExport(RuntimeError):
    """The file is not an export this feed knows how to read."""


@dataclass
class Message:
    role: str            # "user" or "assistant"
    text: str


@dataclass
class Conversation:
    provider: str
    id: str
    title: str
    created: datetime
    updated: datetime
    project: str = ""
    messages: list[Message] = field(default_factory=list)
    url: str = ""

    def transcript(self) -> str:
        return "\n\n".join(f"{'Steven' if m.role == 'user' else 'Assistant'}: {m.text}" for m in self.messages)

    def user_chars(self) -> int:
        return sum(len(m.text) for m in self.messages if m.role == "user")

    def fingerprint(self) -> str:
        return notes.content_hash(self.updated.isoformat(), str(len(self.messages)), self.transcript())


class ChatSummary(BaseModel):
    topic: str = ""
    summary: str = ""
    decisions: list[str] = []
    facts_and_preferences: list[str] = []
    open_actions: list[str] = []
    area: str = ""


# --- parsers -------------------------------------------------------------------------

def _when(value) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _claude_message_text(msg: dict) -> str:
    parts = [p.get("text", "") for p in msg.get("content") or []
             if isinstance(p, dict) and p.get("type") == "text"]
    text = "\n".join(p for p in parts if p).strip() or str(msg.get("text") or "").strip()
    names = [a.get("file_name") for a in (msg.get("attachments") or []) + (msg.get("files") or [])
             if isinstance(a, dict) and a.get("file_name")]
    if names:
        text += "\n" + "\n".join(f"[attachment: {n}]" for n in names)
    return text.strip()


MAX_MEMBER_BYTES = 1024 * 1024 * 1024    # an export's JSON is tens of MB; refuse a zip bomb


def _read_member(zf: zipfile.ZipFile, name: str) -> bytes:
    """Read one archive member, refusing one that would unpack to an absurd size."""
    if zf.getinfo(name).file_size > MAX_MEMBER_BYTES:
        raise UnrecognisedExport("a file in the archive is too large to be a chat export")
    return zf.read(name)


def parse_claude_export(zip_path: str) -> list[Conversation]:
    """Claude data export: a zip holding ``conversations.json``."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            name = next((n for n in zf.namelist() if os.path.basename(n) == "conversations.json"), None)
            if not name:
                raise UnrecognisedExport("no conversations.json in the archive")
            data = json.loads(_read_member(zf, name))
            projects = {}
            pname = next((n for n in zf.namelist() if os.path.basename(n) == "projects.json"), None)
            if pname:
                for p in json.loads(_read_member(zf, pname)) or []:
                    if isinstance(p, dict) and p.get("uuid"):
                        projects[p["uuid"]] = str(p.get("name") or "")
    except (zipfile.BadZipFile, json.JSONDecodeError, OSError) as e:
        raise UnrecognisedExport(f"could not read the archive ({type(e).__name__})") from None

    if not isinstance(data, list):
        raise UnrecognisedExport("conversations.json is not a list")
    conversations = []
    for raw in data:
        try:
            if not isinstance(raw["chat_messages"], list):
                raise TypeError("chat_messages")
            messages = []
            for msg in raw["chat_messages"]:
                sender = msg["sender"]
                if sender not in ("human", "assistant"):
                    raise ValueError(f"sender {sender!r}")
                text = _claude_message_text(msg)
                if text:
                    messages.append(Message("user" if sender == "human" else "assistant", text))
            project = raw.get("project") or {}
            conversations.append(Conversation(
                provider="claude", id=str(raw["uuid"]), title=str(raw.get("name") or "").strip() or "Untitled",
                created=_when(raw["created_at"]), updated=_when(raw.get("updated_at") or raw["created_at"]),
                project=str(project.get("name") or projects.get(raw.get("project_uuid"), "")),
                messages=messages, url=f"https://claude.ai/chat/{raw['uuid']}"))
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise UnrecognisedExport(
                f"a conversation does not have the expected fields ({type(e).__name__}: {e})") from None
    return conversations


GEMINI_ACTIVITY = "Gemini Apps/My Activity.json"
_GEMINI_PROMPT = "Prompted "
_GEMINI_MEDIA = re.compile(r"^\d+ generated (image|video)s?\.?$", re.IGNORECASE)


def _html_text(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text(separator="\n").replace("\xa0", " ")).strip()


def _gemini_conversation_id(entry: dict) -> str:
    """The ID in the entry's gemini.google.com/app/<id> link, or "" when it has none."""
    for d in entry.get("details") or []:
        parsed = urllib.parse.urlparse(str(d.get("url") or "")) if isinstance(d, dict) else None
        parts = [x for x in parsed.path.split("/") if x] if parsed else []
        if parsed and parsed.netloc == "gemini.google.com" and len(parts) == 2 and parts[0] == "app":
            return parts[1]
    return ""


def parse_gemini_export(zip_path: str) -> list[Conversation]:
    """Google Takeout, "My Activity > Gemini Apps", exported as JSON.

    Takeout has no conversation objects: it is one flat log with an entry per prompt.
    Each entry links to ``gemini.google.com/app/<id>``, which is what groups them here.
    Entries with no link are grouped by day. There are no titles either, so the first
    prompt stands in for one. Generated images, video and attached files are recorded
    as a marker only; the media in the archive is never read.
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            name = next((n for n in zf.namelist() if n.endswith(GEMINI_ACTIVITY)), None)
            if not name:
                html = any(n.endswith("Gemini Apps/My Activity.html") for n in zf.namelist())
                raise UnrecognisedExport(
                    "the Takeout archive holds the activity as HTML: in Takeout, set My Activity's format to JSON "
                    "and export again" if html else "no Gemini Apps activity in the archive")
            data = json.loads(_read_member(zf, name))
    except (zipfile.BadZipFile, json.JSONDecodeError, OSError) as e:
        raise UnrecognisedExport(f"could not read the archive ({type(e).__name__})") from None
    if not isinstance(data, list):
        raise UnrecognisedExport("My Activity.json is not a list")

    groups: dict[str, list[tuple[datetime, list[Message]]]] = {}
    for raw in data:
        try:
            title, when = raw["title"], _when(raw["time"])
            if not isinstance(title, str):
                raise TypeError("title")
            if not title.startswith(_GEMINI_PROMPT):
                continue                 # "Created a Gem", "Used an extension", feedback: not conversation
            prompt = title[len(_GEMINI_PROMPT):].strip()
            names = [os.path.basename(str(a)) for a in raw.get("attachedFiles") or []]
            media = [s["name"] for s in raw.get("subtitles") or []
                     if isinstance(s, dict) and _GEMINI_MEDIA.match(str(s.get("name") or ""))]
            generated = {os.path.basename(str(raw["imageFile"]))} if raw.get("imageFile") else set()
            attached = [n for n in names if n not in generated]
            if attached:
                prompt += "\n" + "\n".join(f"[attachment: {n}]" for n in attached)
            reply = "\n\n".join(t for t in (_html_text(str(i.get("html") or ""))
                                             for i in raw.get("safeHtmlItem") or [] if isinstance(i, dict)) if t)
            if media or generated:
                reply = (reply + "\n" if reply else "") + f"[{media[0].rstrip('.') if media else '1 generated image'}; not kept]"
            messages = [Message("user", prompt)] if prompt else []
            if reply:
                messages.append(Message("assistant", reply))
            if messages:
                key = _gemini_conversation_id(raw) or f"day-{when.date().isoformat()}"
                groups.setdefault(key, []).append((when, messages))
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise UnrecognisedExport(
                f"an activity entry does not have the expected fields ({type(e).__name__})") from None

    conversations = []
    for key, turns in groups.items():
        turns.sort(key=lambda t: t[0])
        messages = [m for _, ms in turns for m in ms]
        first = next((m.text for m in messages if m.role == "user"), "")
        title = " ".join(first.split("\n")[0].split())[:70].strip() or "Untitled"
        conversations.append(Conversation(
            provider="gemini", id=key, title=title, created=turns[0][0], updated=turns[-1][0], messages=messages,
            url="" if key.startswith("day-") else f"https://gemini.google.com/app/{key}"))
    return conversations


def detect_provider(zip_path: str) -> str:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError):
        raise UnrecognisedExport("not a zip archive") from None
    if any(os.path.basename(n) == "conversations.json" for n in names):
        return "claude"
    if any("Gemini" in n and "My Activity" in n for n in names):
        return "gemini"
    raise UnrecognisedExport("neither a Claude nor a Gemini export")


PARSERS = {"claude": parse_claude_export, "gemini": parse_gemini_export}


# --- free filters ----------------------------------------------------------------------

def load_exclusions() -> list[tuple[str, str]]:
    """(kind, lower-case text) rules from the exclusions file; kind is title, project or keyword."""
    try:
        with open(config.CHAT_EXCLUSIONS_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    rules = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", ">")):
            continue
        line = line.lstrip("-* ").strip().strip("`").strip()
        if not line:
            continue
        m = re.match(r"(title|project)\s*:\s*(.+)", line, re.I)
        rules.append((m.group(1).lower(), m.group(2).strip().lower()) if m else ("keyword", line.lower()))
    return rules


def is_excluded(conv: Conversation, rules: list[tuple[str, str]]) -> bool:
    title, project = conv.title.lower(), conv.project.lower()
    text = None
    for kind, needle in rules:
        if kind == "title" and needle in title:
            return True
        if kind == "project" and needle == project:
            return True
        if kind == "keyword":
            text = text if text is not None else conv.transcript().lower()
            if needle in title or needle in text:
                return True
    return False


@dataclass
class Selection:
    to_process: list[Conversation] = field(default_factory=list)
    excluded: int = 0
    trivial: int = 0
    unchanged: int = 0


def select(conversations: list[Conversation]) -> Selection:
    """Apply the free rules. Newest first, so a partial backfill does the recent ones."""
    rules, sel = load_exclusions(), Selection()
    for conv in sorted(conversations, key=lambda c: c.updated, reverse=True):
        if is_excluded(conv, rules):
            sel.excluded += 1
        elif conv.user_chars() < 40 or len(conv.transcript()) < config.CHAT_MIN_CHARS:
            sel.trivial += 1
        elif state.is_unchanged(FEED, f"{conv.provider}:{conv.id}", conv.fingerprint()):
            sel.unchanged += 1
        else:
            sel.to_process.append(conv)
    return sel


# --- summarising -----------------------------------------------------------------------

def candidate_folders() -> list[str]:
    from backend.vault.paths import list_areas
    folders = [f"03_Areas/{rel}" for rel in list_areas().values()]
    resources = os.path.join(config.OBSIDIAN_VAULT_PATH, "04_Resources")
    if os.path.isdir(resources):
        folders += [f"04_Resources/{d}" for d in sorted(os.listdir(resources))
                    if os.path.isdir(os.path.join(resources, d)) and not d.startswith((".", "Templates"))
                    and f"04_Resources/{d}" != config.CHAT_NOTES_DIR]
    return folders


def build_prompt(conv: Conversation) -> str:
    text = conv.transcript()
    if len(text) > config.CHAT_MAX_CHARS:
        head, tail = config.CHAT_MAX_CHARS // 3, config.CHAT_MAX_CHARS - config.CHAT_MAX_CHARS // 3
        text = f"{text[:head]}\n\n[... middle of the conversation left out ...]\n\n{text[-tail:]}"
    header = f"Title: {conv.title}\nDate: {conv.updated.date().isoformat()}"
    if conv.project:
        header += f"\nProject: {conv.project}"
    return f"{header}\n\n{llm.wrap_untrusted(text)}"


def system_instruction(provider: str, folders: list[str]) -> str:
    return load_prompt("chat_summary", provider=provider.capitalize(),
                       areas="\n".join(f"  - {f}" for f in folders) or "  (none)")


def estimate_pence(convs: list[Conversation], folders: list[str]) -> float:
    """Worst case for the whole selection: nothing is sent to a model to work this out."""
    if not convs:
        return 0.0
    system = system_instruction(convs[0].provider, folders)
    return sum(llm.worst_case_pence(build_prompt(c), system, SUMMARY_MAX_OUTPUT_TOKENS) for c in convs)


def render_note(conv: Conversation, s: ChatSummary) -> str:
    meta = f"**Provider:** {conv.provider.capitalize()} · **Date:** {conv.updated.date().isoformat()}"
    if conv.project:
        meta += f" · **Project:** {conv.project}"
    lines = [meta, "", "## Summary", "", s.summary.strip() or "(no summary)"]
    for heading, items in (("Decisions", s.decisions), ("Facts and preferences I stated", s.facts_and_preferences),
                           ("Open actions", s.open_actions)):
        if items:
            lines += ["", f"## {heading}", "", *[f"- {i.strip()}" for i in items if i.strip()]]
    if conv.url:
        lines += ["", f"[Open the original conversation]({conv.url})"]
    return "\n".join(lines)


def destination(conv: Conversation, summary: ChatSummary, folders: list[str]) -> str:
    year = str(conv.updated.year)
    if config.CHAT_NOTES_FILING == "by_area" and summary.area in folders:
        return os.path.join(summary.area, "AI_Conversations")
    return os.path.join(config.CHAT_NOTES_DIR, year)


async def summarise(conv: Conversation, folders: list[str], report: RunReport, dry_run: bool,
                    now: datetime | None = None) -> str | None:
    """Summarise one conversation and write its note. Returns the note path."""
    from backend.vault import clarify_ops

    source_id = f"{conv.provider}:{conv.id}"
    reply = await llm.generate(FEED, build_prompt(conv), system_instruction(conv.provider, folders),
                               response_schema=ChatSummary, max_output_tokens=SUMMARY_MAX_OUTPUT_TOKENS,
                               report=report, dry_run=dry_run)
    if dry_run:
        summary = ChatSummary()
    else:
        try:
            summary = ChatSummary.model_validate_json(reply or "")
        except ValidationError:
            print(f"Backend [Chats]: unusable model reply for conversation {conv.id}; skipped.")
            return None

    title = f"{conv.updated.date().isoformat()} {sanitize_filename(conv.title)}"
    extra = {"provider": conv.provider, "conversation_id": conv.id, "topic": summary.topic.strip()}
    if conv.project:
        extra["project"] = conv.project
    if summary.area in folders:
        extra["area"] = summary.area
    path = notes.write_feed_note(
        feed=FEED, source_id=source_id, note_type="ai-conversation", title=title,
        body=render_note(conv, summary), dest_dir=destination(conv, summary, folders),
        original_date=conv.updated.date(), source_hash=conv.fingerprint(), extra_frontmatter=extra,
        report=report, dry_run=dry_run)

    now = now or datetime.now(UTC)
    recent = now - conv.updated <= timedelta(days=config.CHAT_ACTION_WINDOW_DAYS)
    if path and recent and not dry_run:
        for action in (a.strip() for a in summary.open_actions):
            if action:
                clarify_ops.add_capture(action, via=f"{conv.provider.capitalize()} chat", source=title)
    return path


# --- runs ------------------------------------------------------------------------------

@dataclass
class ExportResult:
    provider: str
    selection: Selection
    estimate_pence: float
    report: RunReport
    left_over: int = 0           # qualifying conversations not reached (budget ran out)
    needs_approval: str = ""     # reason, when the run stopped to ask

    def text(self) -> str:
        s = self.selection
        lines = [f"{self.provider.capitalize()} export: {len(s.to_process)} to summarise, {s.unchanged} unchanged, "
                 f"{s.trivial} too short, {s.excluded} excluded.",
                 f"Estimated cost up to {ledger.format_pence(self.estimate_pence)}; "
                 f"{ledger.format_pence(max(ledger.remaining_pence(), 0))} of this month's budget is left."]
        if self.needs_approval:
            lines.append(f"Waiting for approval ({self.needs_approval}).")
        elif not self.report.dry_run:
            lines.append(f"Wrote {len(self.report.notes)} note(s) for {ledger.format_pence(self.report.cost_pence)}.")
            if self.left_over:
                lines.append(f"{self.left_over} left for next month: the budget for this month is used.")
        return "\n".join(lines)


def is_backfill(provider: str) -> bool:
    with state.connect() as conn:
        return conn.execute("SELECT 1 FROM seen_items WHERE feed = ? AND source_id LIKE ? LIMIT 1",
                            (FEED, f"{provider}:%")).fetchone() is None


async def process_export(zip_path: str, *, approved: bool = False, dry_run: bool = False,
                         now: datetime | None = None) -> ExportResult:
    provider = detect_provider(zip_path)
    selection = select(PARSERS[provider](zip_path))
    folders = candidate_folders()
    estimate = estimate_pence(selection.to_process, folders)
    result = ExportResult(provider, selection, estimate, RunReport(FEED, dry_run=dry_run))
    if not selection.to_process:
        return result

    backfill = is_backfill(provider)
    if not dry_run:
        # A backfill may be larger than the month's budget: it is then spread over months,
        # so only the approval rule is applied here, not the whole-run cap.
        threshold_hit = estimate > config.FEEDS_APPROVAL_THRESHOLD_PENCE
        if (backfill or threshold_hit) and not approved:
            result.needs_approval = "first import for this provider" if backfill else "over the approval threshold"
            return result

    system = system_instruction(provider, folders)
    headroom = config.FEEDS_BACKFILL_HEADROOM_PENCE if backfill else 0.0
    for n, conv in enumerate(selection.to_process):
        if not dry_run:
            worst = llm.worst_case_pence(build_prompt(conv), system, SUMMARY_MAX_OUTPUT_TOKENS)
            try:
                ledger.check_budget(FEED, worst + headroom)
                await summarise(conv, folders, result.report, dry_run, now=now)
            except ledger.BudgetExceeded as e:
                print(f"Backend [Chats]: stopping early: {e}")
                result.left_over = len(selection.to_process) - n
                break
        else:
            await summarise(conv, folders, result.report, dry_run, now=now)
    return result


def _add_export(provider: str, raw_path: str) -> int:
    stamp = datetime.now().isoformat(timespec="seconds")
    with state.connect() as conn:
        cur = conn.execute("INSERT INTO chat_exports (provider, raw_path, status, created_at, updated_at) "
                           "VALUES (?, ?, 'new', ?, ?)", (provider, raw_path, stamp, stamp))
        return cur.lastrowid


def _exports(*statuses: str) -> list[dict]:
    marks = ",".join("?" * len(statuses))
    with state.connect() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM chat_exports WHERE status IN ({marks}) ORDER BY id", statuses)]


def take_in_new_exports() -> list[str]:
    """Move new zips from the drop folder into the raw store. Returns problems to report."""
    problems = []
    if not os.path.isdir(config.CHAT_EXPORTS_PATH):
        return problems
    if not os.path.exists(config.CHAT_EXCLUSIONS_FILE):
        os.makedirs(os.path.dirname(config.CHAT_EXCLUSIONS_FILE), exist_ok=True)
        with open(config.CHAT_EXCLUSIONS_FILE, "w", encoding="utf-8") as f:
            f.write(EXCLUSIONS_TEMPLATE)
    for name in sorted(os.listdir(config.CHAT_EXPORTS_PATH)):
        src = os.path.join(config.CHAT_EXPORTS_PATH, name)
        if not name.lower().endswith(".zip") or not os.path.isfile(src):
            continue
        if datetime.now().timestamp() - os.path.getmtime(src) < 60:
            continue                                     # may still be copying
        try:
            provider = detect_provider(src)
        except UnrecognisedExport as e:
            os.replace(src, src + ".unrecognised")
            problems.append(f"{name}: {e}")
            continue
        dest_dir = os.path.join(config.FEEDS_RAW_PATH, FEED)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_{sanitize_filename(name)}")
        shutil.copy2(src, dest)
        if os.path.getsize(dest) == os.path.getsize(src):
            os.remove(src)
            _add_export(provider, dest)
    return problems


async def poll(http_client=None) -> None:
    if not config.ENABLE_CHATS_FEED:
        print("Backend [Chats]: feed disabled; nothing done.")
        return
    for problem in take_in_new_exports():
        await notify.send(f"⚠️ An export could not be read. {problem}", http_client=http_client)

    for row in _exports("new", "approved", "incomplete"):
        if row["status"] == "incomplete" and ledger.remaining_pence() <= config.FEEDS_BACKFILL_HEADROOM_PENCE:
            continue                                     # wait for next month's budget
        approved = row["status"] in ("approved", "incomplete")
        try:
            result = await process_export(row["raw_path"], approved=approved)
        except UnrecognisedExport as e:
            approvals.set_status(row["id"], "failed")
            await notify.send(f"⚠️ The {row['provider'].capitalize()} export could not be read: {e}",
                              http_client=http_client)
            continue
        if result.needs_approval:
            approvals.set_status(row["id"], "awaiting_approval")
            await notify.send("🗂 " + result.text() + "\nNothing has been sent to the model yet.",
                              reply_markup=approvals.buttons(row["id"]), http_client=http_client)
            continue
        approvals.set_status(row["id"], "incomplete" if result.left_over else "done")
        if result.report.notes or row["status"] != "incomplete":
            await notify.send("🗂 " + result.text(), http_client=http_client)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["poll"]:
        asyncio.run(poll())
    elif len(args) >= 2 and args[0] == "process" and not set(args[2:]) - {"--dry-run", "--approve"}:
        try:
            print(asyncio.run(process_export(args[1], approved="--approve" in args,
                                             dry_run="--dry-run" in args)).text())
        except UnrecognisedExport as e:
            sys.exit(f"Backend [Chats]: {e}")
    else:
        sys.exit("usage: python3 -m backend.feeds.chats poll | process <export.zip> [--dry-run] [--approve]")
