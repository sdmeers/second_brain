"""Personal email digest (read-only, heavily filtered).

Stage 1 costs nothing: Gmail's own categories, bulk-mail headers and a rules file decide
which messages are worth reading. Only those reach a model, one call each, and what is
kept is a short digest entry with a link back to the message. Message bodies are never
written to the vault, the state store or the logs; nor are senders or subjects logged.

Run as a module:
  python3 -m backend.feeds.mail authorize
  python3 -m backend.feeds.mail sync [--dry-run]
"""

import asyncio
import base64
import html
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parseaddr, parsedate_to_datetime

from pydantic import BaseModel, ValidationError

from backend import config
from backend.feeds import google_auth, ledger, llm, notes, notify, state
from backend.feeds.dryrun import RunReport
from backend.prompts import load_prompt
from backend.utils.sanitize import sanitize_filename

FEED = "email"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
BASE_QUERY = "-category:promotions -category:social -in:spam -in:trash -in:chats -in:drafts -from:me"
HEADERS = ("From", "Subject", "Date", "List-Unsubscribe", "List-Id", "Precedence")
MAX_OUTPUT_TOKENS = 512
MAX_LISTED = 300                    # per run; a guard against paging through a whole mailbox
RULES_TEMPLATE = """# Email rules

> Which emails the digest reads. Nothing here is sent anywhere.
> Write one rule per line under this box. Lines starting with `#` or `>` are ignored.
>
> - `allow: name@example.com` or `allow: @example.com` always reads mail from that sender
> - `deny: name@example.com` or `deny: @example.com` never reads mail from that sender
> - `pattern: words` reads mail whose subject contains those words, unless it is bulk mail
>
> Promotions, social and spam are always ignored. Mail matching no rule is ignored.

pattern: booking confirm
pattern: order confirm
pattern: your order
pattern: appointment
pattern: reservation
pattern: itinerary
pattern: e-ticket
pattern: renewal
pattern: your policy
pattern: invoice
pattern: receipt
pattern: delivery
"""


class EmailDigest(BaseModel):
    relevant: bool = False
    what: str = ""
    when: str = ""
    reference: str = ""
    amount: str = ""
    action_required: str = ""
    area: str = ""


@dataclass
class Rules:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)


@dataclass
class Meta:
    id: str
    sender: str            # bare lower-case address
    subject: str
    date: datetime | None
    bulk: bool


def load_rules() -> Rules:
    rules = Rules()
    try:
        with open(config.EMAIL_RULES_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return rules
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", ">")):
            continue
        m = re.match(r"[-*\s`]*(allow|deny|pattern)\s*:\s*(.+?)`?\s*$", line, re.I)
        if m:
            target = {"allow": rules.allow, "deny": rules.deny, "pattern": rules.patterns}[m.group(1).lower()]
            target.append(m.group(2).strip().lower())
    return rules


def _sender_matches(sender: str, entries: list[str]) -> bool:
    return any(sender == e or (e.startswith("@") and sender.endswith(e)) for e in entries)


def decide(meta: Meta, rules: Rules) -> str:
    """Stage 1, no tokens: 'keep' or the reason for dropping."""
    if _sender_matches(meta.sender, rules.deny):
        return "denied sender"
    if _sender_matches(meta.sender, rules.allow):
        return "keep"
    if meta.bulk:
        return "bulk mail"
    subject = meta.subject.lower()
    if any(p in subject for p in rules.patterns):
        return "keep"
    return "no rule matched"


# --- Gmail (read-only: every request is a GET) ------------------------------------------

async def _get(http_client, token: str, path: str, params=None) -> dict:
    res = await http_client.get(f"{API}/{path}", params=params, headers={"Authorization": f"Bearer {token}"})
    if res.status_code != 200:
        raise RuntimeError(f"Gmail returned HTTP {res.status_code}")
    return res.json()


async def list_message_ids(http_client, token: str, query: str) -> list[str]:
    ids, page = [], None
    while len(ids) < MAX_LISTED:
        params = {"q": query, "maxResults": 100}
        if page:
            params["pageToken"] = page
        data = await _get(http_client, token, "messages", params)
        ids += [m["id"] for m in data.get("messages", [])]
        page = data.get("nextPageToken")
        if not page:
            break
    return ids[:MAX_LISTED]


def _header(payload: dict, name: str) -> str:
    return next((h.get("value", "") for h in payload.get("headers", []) if h.get("name", "").lower() == name.lower()), "")


async def fetch_meta(http_client, token: str, message_id: str) -> Meta:
    data = await _get(http_client, token, f"messages/{message_id}",
                      [("format", "metadata")] + [("metadataHeaders", h) for h in HEADERS])
    payload = data.get("payload", {})
    try:
        when = parsedate_to_datetime(_header(payload, "Date"))
    except (TypeError, ValueError):
        when = None
    labels = set(data.get("labelIds", []))
    bulk = bool(_header(payload, "List-Unsubscribe") or _header(payload, "List-Id")
                or _header(payload, "Precedence").lower() in ("bulk", "list", "junk")
                or labels & {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "SPAM"})
    return Meta(id=message_id, sender=parseaddr(_header(payload, "From"))[1].lower(),
                subject=_header(payload, "Subject"), date=when, bulk=bulk)


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    text = text.replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def body_text(payload: dict) -> str:
    """The plain-text body (or the HTML body as text). Attachments are ignored."""
    plain, rich = [], []

    def walk(part: dict):
        if part.get("filename"):
            return
        data = (part.get("body") or {}).get("data")
        mime = part.get("mimeType", "")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            rich.append(_html_to_text(_decode(data)))
        for child in part.get("parts") or []:
            walk(child)
    walk(payload)
    return "\n\n".join(plain).strip() or "\n\n".join(rich).strip()


# --- digest ----------------------------------------------------------------------------

def candidate_folders() -> list[str]:
    from backend.vault.paths import list_areas
    return [f"03_Areas/{rel}" for rel in list_areas().values()]


def build_prompt(meta: Meta, body: str) -> str:
    when = meta.date.strftime("%Y-%m-%d %H:%M") if meta.date else "unknown"
    content = f"From: {meta.sender}\nSubject: {meta.subject}\nSent: {when}\n\n{body[:config.EMAIL_MAX_BODY_CHARS]}"
    return llm.wrap_untrusted(content)


def render_entry(meta: Meta, d: EmailDigest) -> str:
    rows = [("When", d.when), ("Reference", d.reference), ("Amount", d.amount),
            ("Action required", d.action_required)]
    lines = [d.what.strip(), ""]
    lines += [f"- **{label}:** {value.strip()}" for label, value in rows if value.strip()]
    sent = meta.date.strftime("%-d %b %Y") if meta.date else "unknown date"
    lines += [f"- **From:** {meta.sender}, sent {sent}",
              f"- [Open the email](https://mail.google.com/mail/u/0/#all/{meta.id})"]
    return "\n".join(lines)


def destination(meta: Meta, d: EmailDigest, folders: list[str]) -> str:
    if config.EMAIL_NOTES_FILING == "by_area" and d.area in folders:
        return os.path.join(d.area, "Email")
    when = meta.date or datetime.now()
    return os.path.join(config.EMAIL_NOTES_DIR, str(when.year), when.strftime("%Y-%m"))


@dataclass
class SyncResult:
    report: RunReport
    listed: int = 0
    dropped: dict = field(default_factory=dict)
    kept: int = 0
    not_relevant: int = 0
    deferred: int = 0            # kept but over today's cap: picked up on the next run

    def text(self) -> str:
        drops = ", ".join(f"{n} {reason}" for reason, n in sorted(self.dropped.items())) or "none"
        lines = [f"email{' (dry run)' if self.report.dry_run else ''}: {self.listed} messages checked, "
                 f"{self.kept} passed the free rules. Dropped without reading: {drops}."]
        if self.report.dry_run:
            lines.append(f"Would read {self.kept - self.deferred} with a model, estimated cost up to "
                         f"{ledger.format_pence(self.report.cost_pence)}.")
        else:
            lines.append(f"Wrote {len(self.report.notes)} digest note(s), {self.not_relevant} judged not relevant, "
                         f"cost {ledger.format_pence(self.report.cost_pence)}.")
        if self.deferred:
            lines.append(f"{self.deferred} left for the next run (daily cap).")
        return "\n".join(lines)


def telegram_summary(result: SyncResult) -> str | None:
    """Where the run filed things, for Telegram. Counts and folder names only: titles,
    senders and subjects come from the emails, so they stay in the vault (the daily
    digest lists them). None when there is nothing worth a message."""
    filed = result.report.notes
    if result.report.dry_run or (not filed and not result.deferred):
        return None
    lines = [f"📧 Email digest: {len(filed)} filed"]
    by_folder = Counter(os.path.dirname(p) for p in filed)
    lines += [f"• {n} → {folder}" for folder, n in sorted(by_folder.items(), key=lambda kv: (-kv[1], kv[0]))]
    left_out = sum(result.dropped.values())
    if left_out:
        lines.append(f"{left_out} left out by your rules")
    if result.not_relevant:
        lines.append(f"{result.not_relevant} read but not worth a note")
    if result.deferred:
        lines.append(f"{result.deferred} waiting for the next run (daily cap)")
    if filed:
        lines.append("Titles and links are in today's Daily Digest.")
    return "\n".join(lines)


def filed_since(since: datetime) -> list[str]:
    """Vault-relative paths of digest notes written since ``since`` that still exist.
    Used by the daily digest note; never sent to Telegram."""
    with state.connect() as conn:
        rows = conn.execute(
            "SELECT note_path FROM seen_items WHERE feed = ? AND note_path IS NOT NULL AND ingested_at >= ? "
            "ORDER BY note_path", (FEED, since.isoformat(timespec="seconds"))).fetchall()
    return [r[0] for r in rows if os.path.isfile(os.path.join(config.OBSIDIAN_VAULT_PATH, r[0]))]


def digest_section(since: datetime, skip_professional: bool = False) -> str:
    """The daily digest note's "Filed from email" section, or "" if nothing was filed.
    Weekend digests leave out Professional areas, as the rest of the digest does."""
    paths = [p for p in filed_since(since) if not (skip_professional and "Professional" in p)]
    if not paths:
        return ""
    lines = ["## 📧 Filed from email (last 24h)"]
    for p in paths:
        stem = os.path.splitext(p)[0]
        lines.append(f"- [[{stem}|{os.path.basename(stem)}]] · `{os.path.dirname(p)}`")
    return "\n".join(lines) + "\n\n"


async def _sync_and_report(dry_run: bool) -> SyncResult:
    result = await sync(dry_run=dry_run)
    summary = telegram_summary(result)
    if summary:
        try:
            await notify.send(summary)
        except Exception as e:      # the notes are written; a missed message is not worth failing for
            print(f"Backend [Email]: Telegram summary not sent ({type(e).__name__}).")
    return result


async def sync(dry_run: bool = False, http_client=None, now: datetime | None = None) -> SyncResult:
    import httpx

    result = SyncResult(RunReport(FEED, dry_run=dry_run))
    if not config.ENABLE_EMAIL_FEED and not dry_run:
        print("Backend [Email]: feed disabled; nothing done.")
        return result
    if not os.path.exists(config.EMAIL_RULES_FILE) and not dry_run:
        os.makedirs(os.path.dirname(config.EMAIL_RULES_FILE), exist_ok=True)
        with open(config.EMAIL_RULES_FILE, "w", encoding="utf-8") as f:
            f.write(RULES_TEMPLATE)
    rules = load_rules() if os.path.exists(config.EMAIL_RULES_FILE) else Rules()

    now = now or datetime.now()
    last = state.get_value("email:last_sync")
    since = (datetime.fromisoformat(last) - timedelta(hours=2) if last
             else now - timedelta(days=config.EMAIL_FIRST_RUN_DAYS))
    query = f"{BASE_QUERY} after:{int(since.timestamp())}"

    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        token = await google_auth.access_token(client)
        folders = candidate_folders()
        system = load_prompt("email_digest", areas="\n".join(f"  - {f}" for f in folders) or "  (none)")
        ids = await list_message_ids(client, token, query)
        result.listed = len(ids)
        done_today = state.get_value(f"email:count:{now.date().isoformat()}", "0")
        processed_today = int(done_today)

        for message_id in reversed(ids):                       # oldest first
            if state.get_seen(FEED, message_id):
                continue
            meta = await fetch_meta(client, token, message_id)
            verdict = decide(meta, rules)
            if verdict != "keep":
                result.dropped[verdict] = result.dropped.get(verdict, 0) + 1
                continue
            result.kept += 1
            if (processed_today >= config.EMAIL_DAILY_MAX_MESSAGES
                    or ledger.spent_today_pence(FEED) + result.report.cost_pence * dry_run
                    >= config.EMAIL_DAILY_MAX_PENCE):
                result.deferred += 1
                continue

            full = await _get(client, token, f"messages/{message_id}", {"format": "full"})
            prompt = build_prompt(meta, body_text(full.get("payload", {})))
            try:
                reply = await llm.generate(FEED, prompt, system, response_schema=EmailDigest,
                                           max_output_tokens=MAX_OUTPUT_TOKENS, report=result.report,
                                           dry_run=dry_run)
            except ledger.BudgetExceeded as e:
                print(f"Backend [Email]: {e}")
                result.deferred += 1
                break
            processed_today += 1
            if dry_run:
                continue
            state.set_value(f"email:count:{now.date().isoformat()}", str(processed_today))
            try:
                digest = EmailDigest.model_validate_json(reply or "")
            except ValidationError:
                digest = EmailDigest()
            if not digest.relevant or not digest.what.strip():
                result.not_relevant += 1
                state.mark_seen(FEED, message_id, "not-relevant")
                continue
            day = (meta.date or now).date()
            extra = {"message_id": message_id}
            if digest.area in folders:
                extra["area"] = digest.area
            try:
                notes.write_feed_note(
                    feed=FEED, source_id=message_id, note_type="email-digest",
                    title=f"{day.isoformat()} {sanitize_filename(digest.what)[:70].rstrip(' .')}",
                    body=render_entry(meta, digest), dest_dir=destination(meta, digest, folders),
                    original_date=day, source_hash=message_id, extra_frontmatter=extra,
                    report=result.report)
            except ledger.BudgetExceeded as e:
                print(f"Backend [Email]: {e}")
                result.deferred += 1
                break

        if not dry_run and not result.deferred:
            state.set_value("email:last_sync", now.isoformat(timespec="seconds"))
    finally:
        if http_client is None:
            await client.aclose()
    return result


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        if args == ["authorize"]:
            google_auth.authorise_interactively()
        elif args and args[0] == "sync" and not set(args[1:]) - {"--dry-run"}:
            print(asyncio.run(_sync_and_report(dry_run="--dry-run" in args)).text())
        else:
            sys.exit("usage: python3 -m backend.feeds.mail authorize | sync [--dry-run]")
    except google_auth.AuthError as e:
        sys.exit(f"Backend [Email]: {e}")
