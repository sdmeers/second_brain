import sqlite3
import os
import json

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "second_brain.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn

def init_queue_db():
    """Initializes the SQLite ingestion queue table."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,         -- 'telegram', 'email', 'file_drop'
            raw_payload TEXT,                 -- JSON dump of the raw input payload
            content_text TEXT,                -- Extracted/transcribed text if available
            file_path TEXT,                   -- Path to saved raw file if applicable (e.g. PDF/DOCX in Sources)
            status TEXT DEFAULT 'pending',     -- 'pending', 'processing', 'completed', 'failed'
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Reset any stuck 'processing' items back to 'pending' on startup
    cursor.execute("UPDATE ingestion_queue SET status = 'pending' WHERE status = 'processing'")
    conn.commit()
    conn.close()

def add_to_queue(source_type: str, raw_payload: dict, content_text: str = None, file_path: str = None) -> int:
    """Adds a new item to the ingestion queue."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ingestion_queue (source_type, raw_payload, content_text, file_path)
        VALUES (?, ?, ?, ?)
    """, (source_type, json.dumps(raw_payload), content_text, file_path))
    item_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return item_id

# Failed items back off exponentially instead of being retried on the very next tick,
# which previously produced three immediate failures and three Telegram error messages.
RETRY_BACKOFF_MINUTES = (1, 5, 30)
MAX_RETRIES = 3


def get_next_pending_item():
    """Retrieves the oldest item eligible for processing.

    Pending items are always eligible. Failed items become eligible again only once
    their backoff window (keyed on retry_count) has elapsed.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    # retry_count is already incremented when the failure is recorded, so the first
    # failed row arrives here with retry_count == 1 and gets the first backoff tier.
    backoff_cases = " ".join(
        f"WHEN {i} THEN '-{minutes} minutes'"
        for i, minutes in enumerate(RETRY_BACKOFF_MINUTES, start=1)
    )
    cursor.execute(f"""
        SELECT * FROM ingestion_queue
        WHERE status = 'pending'
           OR (
                status = 'failed'
                AND retry_count < ?
                AND updated_at <= datetime('now', CASE retry_count {backoff_cases}
                                                  ELSE '-30 minutes' END)
              )
        ORDER BY created_at ASC LIMIT 1
    """, (MAX_RETRIES,))
    row = cursor.fetchone()
    conn.close()
    return row

def update_item_status(item_id: int, status: str, error_message: str = None):
    """Updates the status of an item in the queue.

    Uses SQLite's CURRENT_TIMESTAMP (UTC) rather than Python's local-time now(), so
    updated_at is comparable with the column default that inserts already write and
    with the datetime('now') backoff comparison above. Previously inserts were UTC and
    updates were local, which under BST left the two an hour apart.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if status == 'failed':
        cursor.execute("""
            UPDATE ingestion_queue
            SET status = ?, error_message = ?, retry_count = retry_count + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, error_message, item_id))
    else:
        cursor.execute("""
            UPDATE ingestion_queue
            SET status = ?, error_message = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, item_id))

    conn.commit()
    conn.close()

def get_queue_item(item_id: int):
    """Retrieves a single item from the queue by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ingestion_queue WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    conn.close()
    return row
