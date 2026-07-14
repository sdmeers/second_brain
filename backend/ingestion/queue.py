import sqlite3
import os
import json
from datetime import datetime
from backend.config import OBSIDIAN_VAULT_PATH

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

def get_next_pending_item():
    """Retrieves the oldest pending item from the queue."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM ingestion_queue
        WHERE status = 'pending' OR (status = 'failed' AND retry_count < 3)
        ORDER BY created_at ASC LIMIT 1
    """)
    row = cursor.fetchone()
    conn.close()
    return row

def update_item_status(item_id: int, status: str, error_message: str = None):
    """Updates the status of an item in the queue."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if status == 'failed':
        cursor.execute("""
            UPDATE ingestion_queue
            SET status = ?, error_message = ?, retry_count = retry_count + 1, updated_at = ?
            WHERE id = ?
        """, (status, error_message, now, item_id))
    else:
        cursor.execute("""
            UPDATE ingestion_queue
            SET status = ?, error_message = NULL, updated_at = ?
            WHERE id = ?
        """, (status, now, item_id))
        
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
