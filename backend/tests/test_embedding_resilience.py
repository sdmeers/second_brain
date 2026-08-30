"""Guards for the embedding/indexing path that a 429 used to corrupt.

`index_file` deleted a file's existing chunks and committed *before* calling the embedding
API. A rate-limited re-index therefore committed the delete, raised on the embed, and left
the file permanently absent from the vector store -- silently, since the failure is caught
and logged per-file. These tests fail against that pre-fix ordering.
"""

import asyncio
import sqlite3

import pytest
import sqlite_vss

from backend.rag import embeddings as emb
from backend.rag import vectorstore


DIMS = 3072


def _make_db() -> sqlite3.Connection:
    """An in-memory mirror of the real vector schema."""
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    sqlite_vss.load(conn)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE brain_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT, chunk_index INTEGER, text_content TEXT,
            file_hash TEXT, file_mtime REAL, file_size INTEGER
        )
    """)
    cur.execute(f"CREATE VIRTUAL TABLE vss_brain_chunks USING vss0(vector({DIMS}))")
    conn.commit()
    return conn


def _chunk_count(conn, path: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM brain_chunks WHERE file_path = ?", (path,)
    ).fetchone()[0]


class TestIndexReplaceIsAtomic:

    def test_failed_embedding_preserves_existing_index(self, tmp_path, monkeypatch):
        """A 429 mid-reindex must leave the previously indexed chunks intact."""
        conn = _make_db()
        src = tmp_path / "note.md"
        src.write_text("First body paragraph.\n\nSecond body paragraph.\n")
        rel = "04_Resources/note.md"

        async def ok_embedder(texts, batch_size=None):
            return [[0.01] * DIMS for _ in texts]

        monkeypatch.setattr(vectorstore, "get_embeddings_batch", ok_embedder)
        asyncio.run(vectorstore.index_file(rel, str(src), "hash-1", conn, mtime=1.0, size=10))
        indexed = _chunk_count(conn, rel)
        assert indexed > 0, "precondition: the file should index cleanly"

        # Now the file changes and the re-index hits a rate limit.
        src.write_text("Rewritten body paragraph.\n\nAnother rewritten paragraph.\n")

        async def rate_limited(texts, batch_size=None):
            raise RuntimeError("429 RESOURCE_EXHAUSTED. Quota exceeded for embed_content")

        monkeypatch.setattr(vectorstore, "get_embeddings_batch", rate_limited)
        asyncio.run(vectorstore.index_file(rel, str(src), "hash-2", conn, mtime=2.0, size=20))

        assert _chunk_count(conn, rel) == indexed, (
            "a rate-limited re-index destroyed the existing index instead of leaving it alone"
        )
        # The surviving rows must still be the *old* content, not a half-written replacement.
        texts = [r[0] for r in conn.execute(
            "SELECT text_content FROM brain_chunks WHERE file_path = ?", (rel,))]
        assert any("First body" in t for t in texts)
        assert not any("Rewritten" in t for t in texts)

    def test_successful_reindex_still_replaces(self, tmp_path, monkeypatch):
        """The atomic path must not accidentally keep stale chunks around."""
        conn = _make_db()
        src = tmp_path / "note.md"
        src.write_text("Original content here.\n")
        rel = "04_Resources/note2.md"

        async def ok_embedder(texts, batch_size=None):
            return [[0.02] * DIMS for _ in texts]

        monkeypatch.setattr(vectorstore, "get_embeddings_batch", ok_embedder)
        asyncio.run(vectorstore.index_file(rel, str(src), "h1", conn, mtime=1.0, size=10))

        src.write_text("Replacement content here.\n")
        asyncio.run(vectorstore.index_file(rel, str(src), "h2", conn, mtime=2.0, size=11))

        texts = [r[0] for r in conn.execute(
            "SELECT text_content FROM brain_chunks WHERE file_path = ?", (rel,))]
        assert texts and all("Replacement" in t for t in texts)
        assert not any("Original" in t for t in texts)

    def test_delete_file_index_still_commits_by_default(self, tmp_path, monkeypatch):
        """The standalone cleanup caller relies on the default commit behaviour."""
        conn = _make_db()
        conn.execute(
            "INSERT INTO brain_chunks (file_path, chunk_index, text_content) VALUES (?,?,?)",
            ("gone.md", 0, "x"))
        conn.commit()
        asyncio.run(vectorstore.delete_file_index("gone.md", conn))
        assert _chunk_count(conn, "gone.md") == 0


class TestEmbeddingPacing:

    def setup_method(self):
        emb._recent_texts.clear()

    def teardown_method(self):
        emb._recent_texts.clear()

    def test_stays_silent_while_under_ceiling(self, monkeypatch):
        monkeypatch.setattr("backend.config.EMBEDDING_TEXTS_PER_MINUTE", 1000, raising=False)
        slept = []

        async def fake_sleep(s):
            slept.append(s)

        monkeypatch.setattr(emb.asyncio, "sleep", fake_sleep)
        asyncio.run(emb._reserve_capacity(100))
        asyncio.run(emb._reserve_capacity(100))
        assert slept == [], "pacing should not delay traffic that is within budget"

    def test_throttles_once_ceiling_is_reached(self, monkeypatch):
        monkeypatch.setattr("backend.config.EMBEDDING_TEXTS_PER_MINUTE", 150, raising=False)
        slept = []

        async def fake_sleep(s):
            slept.append(s)
            # Simulate the window ageing out so the loop can make progress.
            emb._recent_texts.clear()

        monkeypatch.setattr(emb.asyncio, "sleep", fake_sleep)
        asyncio.run(emb._reserve_capacity(100))
        asyncio.run(emb._reserve_capacity(100))
        assert slept, "second batch exceeded the ceiling and should have waited"
        assert slept[0] <= 60.5

    def test_batch_larger_than_ceiling_does_not_deadlock(self, monkeypatch):
        """A single oversized batch must pass once the window drains, not hang forever."""
        monkeypatch.setattr("backend.config.EMBEDDING_TEXTS_PER_MINUTE", 10, raising=False)

        async def fake_sleep(s):
            emb._recent_texts.clear()

        monkeypatch.setattr(emb.asyncio, "sleep", fake_sleep)
        asyncio.run(asyncio.wait_for(emb._reserve_capacity(500), timeout=5))


class TestRetryDelayParsing:

    @pytest.mark.parametrize("payload,expected", [
        ("429 RESOURCE_EXHAUSTED ... {'retryDelay': '30s'} ...", 30.0),
        ("Please retry in 30.469006128s.", 30.469006128),
        ("429 with no hint at all", None),
    ])
    def test_extracts_server_suggested_delay(self, payload, expected):
        assert emb._retry_delay_from_error(payload) == expected
