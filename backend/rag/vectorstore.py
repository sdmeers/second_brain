import os
import sqlite3
import hashlib
import json
import sqlite_vss
from backend.config import OBSIDIAN_VAULT_PATH
from backend.rag.embeddings import get_embeddings_batch

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "brain_vectors.db")

def get_vector_db_connection():
    """Establishes connection to the vector database and loads the sqlite-vss extension."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.enable_load_extension(True)
    sqlite_vss.load(conn)
    return conn

def init_vector_db():
    """Initializes the SQLite and VSS vector tables."""
    conn = get_vector_db_connection()
    cursor = conn.cursor()
    
    # 1. Standard SQLite table for chunks metadata
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS brain_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT,
            chunk_index INTEGER,
            text_content TEXT,
            file_hash TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create indexes for fast lookup
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_path ON brain_chunks(file_path)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_hash ON brain_chunks(file_hash)")

    # Migration: cache stat() metadata so the incremental pass can skip unchanged files
    # without reading and hashing every byte in the vault on every run.
    existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(brain_chunks)")}
    if "file_mtime" not in existing_cols:
        cursor.execute("ALTER TABLE brain_chunks ADD COLUMN file_mtime REAL")
    if "file_size" not in existing_cols:
        cursor.execute("ALTER TABLE brain_chunks ADD COLUMN file_size INTEGER")
    
    # 2. Virtual VSS table for vector search (using vss0 module)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vss_brain_chunks'")
    if not cursor.fetchone():
        print("RAG [VectorStore]: Creating virtual VSS table 'vss_brain_chunks'...")
        cursor.execute("CREATE VIRTUAL TABLE vss_brain_chunks USING vss0(vector(3072))")
        
    conn.commit()
    conn.close()
    print("RAG [VectorStore]: Database initialized successfully.")

def chunk_text(text: str, max_chars: int = 800, overlap: int = 150) -> list[str]:
    """Splits text into chunks preserving paragraph structures where possible."""
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        # If adding this paragraph fits, add it
        if len(current_chunk) + len(para) + 2 <= max_chars:
            current_chunk += ("\n\n" if current_chunk else "") + para
        else:
            if current_chunk:
                chunks.append(current_chunk)
            
            # If paragraph itself is too large, split it by characters/sentences
            if len(para) > max_chars:
                start = 0
                while start < len(para):
                    chunks.append(para[start:start + max_chars])
                    start += (max_chars - overlap)
                current_chunk = ""
            else:
                current_chunk = para
                
    if current_chunk:
        chunks.append(current_chunk)
        
    return chunks

def calculate_file_hash(file_path: str) -> str:
    """Calculates the MD5 hash of a file's contents."""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        hasher.update(f.read())
    return hasher.hexdigest()

async def delete_file_index(file_path: str, conn: sqlite3.Connection):
    """Deletes all chunks and corresponding vector rows for a file."""
    cursor = conn.cursor()
    
    # Get all rowids from brain_chunks
    cursor.execute("SELECT id FROM brain_chunks WHERE file_path = ?", (file_path,))
    row_ids = [row[0] for row in cursor.fetchall()]
    
    if row_ids:
        # Delete from vss_brain_chunks (rowid maps directly)
        placeholders = ",".join(["?"] * len(row_ids))
        cursor.execute(f"DELETE FROM vss_brain_chunks WHERE rowid IN ({placeholders})", row_ids)
        # Delete from brain_chunks
        cursor.execute("DELETE FROM brain_chunks WHERE file_path = ?", (file_path,))
        conn.commit()

async def index_file(vault_relative_path: str, full_file_path: str, file_hash: str, conn: sqlite3.Connection,
                     mtime: float = None, size: int = None):
    """Chunks, embeds, and indexes a single file (Markdown, PDF, DOCX, or PPTX) in the database."""
    print(f"RAG [VectorStore]: Indexing file: {vault_relative_path}...")
    try:
        if full_file_path.endswith(".pdf"):
            from backend.processing.extractor import extract_pdf_text
            content, _ = await extract_pdf_text(full_file_path)
        elif full_file_path.endswith(".docx"):
            from backend.processing.extractor import extract_docx_text
            content, _ = await extract_docx_text(full_file_path)
        elif full_file_path.endswith(".pptx"):
            from backend.processing.extractor import extract_pptx_text
            content = extract_pptx_text(full_file_path)
        else:
            with open(full_file_path, encoding="utf-8") as f:
                content = f.read()
            
        # Parse and strip frontmatter so it doesn't skew similarity search
        body_content = content
        if content.strip().startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                body_content = parts[2].strip()
                
        # Split into chunks
        chunks = chunk_text(body_content)
        if not chunks:
            return
            
        # Clear existing indices for this file
        await delete_file_index(vault_relative_path, conn)
        
        # Batch embed all chunks in one call
        embeddings = await get_embeddings_batch(chunks)
        
        cursor = conn.cursor()
        
        # Insert metadata and vectors
        for idx, (chunk_text_val, vector) in enumerate(zip(chunks, embeddings, strict=False)):
            # 1. Insert into SQLite table
            cursor.execute("""
                INSERT INTO brain_chunks (file_path, chunk_index, text_content, file_hash, file_mtime, file_size)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (vault_relative_path, idx, chunk_text_val, file_hash, mtime, size))
            
            row_id = cursor.lastrowid
            
            # 2. Insert into VSS virtual table
            # sqlite-vss expects JSON serialization of the vector array
            cursor.execute("""
                INSERT INTO vss_brain_chunks (rowid, vector)
                VALUES (?, ?)
            """, (row_id, json.dumps(vector)))
            
        conn.commit()
        print(f"RAG [VectorStore]: Successfully indexed {len(chunks)} chunks for {vault_relative_path}")
    except Exception as e:
        print(f"RAG [VectorStore]: Error indexing file {vault_relative_path}: {e}")
        # Rollback transaction on failure
        conn.rollback()

async def index_vault(force_reindex: bool = False):
    """Scans the vault and incrementally indexes new/modified files."""
    init_vector_db()
    
    if not OBSIDIAN_VAULT_PATH:
        print("RAG [VectorStore]: OBSIDIAN_VAULT_PATH is not set. Skipping indexing.")
        return
        
    print("\n--- Starting Incremental Vector Indexing ---")
    conn = get_vector_db_connection()
    cursor = conn.cursor()
    
    # 1. Scan for all markdown and document files in active directories
    target_dirs = ["00_System", "02_Projects", "03_Areas", "04_Resources", "05_Archive"]
    indexed_files = set()
    
    for folder in target_dirs:
        folder_path = os.path.join(OBSIDIAN_VAULT_PATH, folder)
        if not os.path.exists(folder_path):
            continue
            
        for root, _, files in os.walk(folder_path):
            for file in files:
                if file.endswith((".md", ".pdf", ".docx", ".pptx")):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, OBSIDIAN_VAULT_PATH)
                    rel_path_normalized = rel_path.replace("\\", "/")
                    
                    # Strict Safety Filter: Exclude internal system directories (Internal, Logs, Templates, Inbox, Librarian_Activity)
                    if rel_path_normalized.startswith("00_System/"):
                        if "Librarian_Activity.md" in rel_path_normalized or any(skip_part in rel_path_normalized for skip_part in ["/Internal/", "/Logs/", "/Templates/", "/Inbox/", "/.git"]):
                            continue
                    
                    try:
                        indexed_files.add(rel_path)
                        stat = os.stat(full_path)

                        # Empty files produce no chunks, so index_file stores no rows and
                        # no cached stat -- which meant they were re-read and re-hashed on
                        # every single pass, forever. Skip them outright.
                        if stat.st_size == 0:
                            continue

                        cursor.execute(
                            "SELECT file_hash, file_mtime, file_size FROM brain_chunks "
                            "WHERE file_path = ? LIMIT 1",
                            (rel_path,)
                        )
                        row = cursor.fetchone()

                        # Fast path: mtime and size both unchanged means the content is
                        # unchanged, so skip without reading the file at all. This is what
                        # keeps the 2-minute sweep cheap as the vault grows.
                        if not force_reindex and row and row[1] is not None:
                            if row[1] == stat.st_mtime and row[2] == stat.st_size:
                                continue

                        # Slow path: stat changed (or we have no cached stat), so hash to
                        # confirm the content really differs before re-embedding.
                        file_hash = calculate_file_hash(full_path)
                        if not force_reindex and row and row[0] == file_hash:
                            # Content identical despite a touched mtime - refresh the
                            # cached stat so the next pass takes the fast path.
                            cursor.execute(
                                "UPDATE brain_chunks SET file_mtime = ?, file_size = ? WHERE file_path = ?",
                                (stat.st_mtime, stat.st_size, rel_path)
                            )
                            conn.commit()
                            continue

                        # File is new, modified, or forced to reindex
                        await index_file(rel_path, full_path, file_hash, conn,
                                         mtime=stat.st_mtime, size=stat.st_size)
                    except Exception as e:
                        print(f"RAG [VectorStore]: Skipping {rel_path} due to error: {e}")
                        
    # 2. Cleanup stale files (deleted from the vault)
    cursor.execute("SELECT DISTINCT file_path FROM brain_chunks")
    db_files = [row[0] for row in cursor.fetchall()]
    
    deleted_count = 0
    for db_file in db_files:
        if db_file not in indexed_files:
            print(f"RAG [VectorStore]: Cleaning up deleted file index: {db_file}...")
            await delete_file_index(db_file, conn)
            deleted_count += 1
            
    conn.close()
    print(f"--- Indexing Complete. Stale files removed: {deleted_count} ---\n")

async def search_similar_chunks(query_vector: list[float], top_k: int = 5) -> list[dict]:
    """Queries the VSS database for the top_k most similar chunks."""
    conn = get_vector_db_connection()
    cursor = conn.cursor()
    
    # Query using vss_search
    try:
        cursor.execute("""
            SELECT c.file_path, c.chunk_index, c.text_content, v.distance
            FROM vss_brain_chunks v
            JOIN brain_chunks c ON c.id = v.rowid
            WHERE vss_search(v.vector, vss_search_params(?, ?))
            ORDER BY v.distance ASC
        """, (json.dumps(query_vector), top_k))
        
        rows = cursor.fetchall()
        results = []
        for r in rows:
            results.append({
                "file_path": r[0],
                "chunk_index": r[1],
                "text": r[2],
                "distance": r[3]
            })
        return results
    except Exception as e:
        print(f"RAG [VectorStore]: Search error: {e}")
        return []
    finally:
        conn.close()
