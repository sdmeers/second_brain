"""Ingestion state and cost ledger storage (SQLite, outside the vault).

Never store message text, transcripts or identifiers here: source IDs, hashes, paths,
token counts and costs only.
"""

import os
import sqlite3
from datetime import datetime

from backend import config


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.FEEDS_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.FEEDS_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_items (
            feed TEXT NOT NULL,
            source_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            note_path TEXT,
            original_date TEXT,
            ingested_at TEXT NOT NULL,
            PRIMARY KEY (feed, source_id)
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            month TEXT NOT NULL,
            feed TEXT NOT NULL,
            model TEXT NOT NULL,
            kind TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cost_pence REAL NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_month ON ledger(month)")
    return conn


def get_seen(feed: str, source_id: str) -> dict | None:
    """The stored record for an item, or None if the feed has never ingested it."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM seen_items WHERE feed = ? AND source_id = ?", (feed, source_id)).fetchone()
    return dict(row) if row else None


def is_unchanged(feed: str, source_id: str, content_hash: str) -> bool:
    seen = get_seen(feed, source_id)
    return bool(seen) and seen["content_hash"] == content_hash


def mark_seen(feed: str, source_id: str, content_hash: str, note_path: str | None = None,
              original_date: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO seen_items (feed, source_id, content_hash, note_path, original_date, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(feed, source_id) DO UPDATE SET content_hash = excluded.content_hash, "
            "note_path = excluded.note_path, original_date = excluded.original_date, "
            "ingested_at = excluded.ingested_at",
            (feed, source_id, content_hash, note_path, original_date,
             datetime.now().isoformat(timespec="seconds")))
