"""Writes feed notes: required frontmatter, idempotent, dry-run aware."""

import hashlib
import os
import re
from datetime import date, datetime

from backend import config
from backend.feeds import ledger, state
from backend.feeds.dryrun import RunReport
from backend.utils.sanitize import sanitize_filename
from backend.vault.tasks import FRONTMATTER_RE, dump_frontmatter
from backend.vault.writer import atomic_write


def content_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def existing_body(rel_path: str | None) -> str:
    """A note's body without frontmatter or title, so a rewrite can keep what is there
    (earlier entries, and anything added by hand in Obsidian)."""
    if not rel_path:
        return ""
    try:
        with open(os.path.join(config.OBSIDIAN_VAULT_PATH, rel_path), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    text = FRONTMATTER_RE.sub("", text, count=1).lstrip("\n")
    return re.sub(r"\A# [^\n]*\n+", "", text).strip()


def write_feed_note(*, feed: str, source_id: str, note_type: str, title: str, body: str,
                    dest_dir: str, original_date: date | str, source_hash: str | None = None,
                    extra_frontmatter: dict | None = None, report: RunReport | None = None,
                    dry_run: bool = False) -> str | None:
    """Write one feed note and return its vault-relative path, or None if nothing changed.

    ``dest_dir`` is vault-relative (for example ``03_Areas/Car``). ``source_hash`` identifies
    the source content; when it matches what was ingested before, nothing is written. A
    changed source rewrites the same note rather than creating a second one.
    """
    source_hash = source_hash or content_hash(title, body)
    if state.is_unchanged(feed, source_id, source_hash):
        if report:
            report.skipped.append(source_id)
        return None

    seen = state.get_seen(feed, source_id)
    if seen and seen["note_path"]:
        rel_path = seen["note_path"]
    else:
        rel_path = os.path.join(dest_dir, f"{sanitize_filename(title)}.md")
        n = 1
        while os.path.exists(os.path.join(config.OBSIDIAN_VAULT_PATH, rel_path)):
            n += 1
            rel_path = os.path.join(dest_dir, f"{sanitize_filename(title)} ({n}).md")

    original = original_date.isoformat() if isinstance(original_date, date) else str(original_date)
    frontmatter = {
        "type": note_type,
        "source": feed,
        "source_id": source_id,
        "date": original,
        "ingested": datetime.now().date().isoformat(),
        **(extra_frontmatter or {}),
    }
    text = f"{dump_frontmatter(frontmatter)}\n# {title}\n\n{body.strip()}\n"

    # The indexer embeds every new note; count that against the feed budget too.
    embed_tokens = ledger.estimate_tokens(text)
    if dry_run:
        if report:
            report.notes.append(rel_path)
            report.cost_pence += ledger.cost_pence(config.EMBEDDING_MODEL, embed_tokens, 0)
        return rel_path

    # Raises BudgetExceeded rather than write a note whose indexing would breach the cap.
    ledger.check_budget(feed, ledger.cost_pence(config.EMBEDDING_MODEL, embed_tokens, 0), use_reserve=True)
    atomic_write(os.path.join(config.OBSIDIAN_VAULT_PATH, rel_path), text)
    state.mark_seen(feed, source_id, source_hash, note_path=rel_path, original_date=original)
    if report:
        report.notes.append(rel_path)
    pence = ledger.record(feed, config.EMBEDDING_MODEL, "embed-estimate", embed_tokens, 0)
    if report:
        report.cost_pence += pence
    return rel_path
