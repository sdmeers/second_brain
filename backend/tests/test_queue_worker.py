"""The queue worker helpers extracted from the old 580-line process_queue_worker,
plus the SQLite queue's retry/backoff behaviour."""

import os
import tempfile

import pytest

from backend.ingestion import queue as q
from backend.ingestion.watcher import (
    detect_research_request,
    is_sync_conflict_file,
    looks_like_document,
    strip_research_triggers,
    write_para_note,
)
from backend.vault.tasks import parse_frontmatter


class TestDetectResearchRequest:
    @pytest.mark.parametrize("text", [
        "/research this paper", "/research_brain something", "#research topic",
        "please add to research brain", "add to research", "/centaur this",
        "#centaur", "centaur brain please", "add to centaur brain",
    ])
    def test_each_documented_trigger(self, text):
        assert detect_research_request(text) is True

    def test_case_insensitive(self):
        assert detect_research_request("/RESEARCH This") is True

    @pytest.mark.parametrize("text", [
        "just a normal note", "researching options for a holiday", "",
    ])
    def test_non_triggers(self, text):
        assert detect_research_request(text) is False

    def test_subject_line_can_trigger(self):
        assert detect_research_request("body text", "add to research brain") is True

    def test_trigger_cannot_span_the_text_subject_boundary(self):
        """Text and subject must be searched separately."""
        assert detect_research_request("please add to", "research brain now") is False


class TestStripResearchTriggers:
    def test_removes_the_trigger(self):
        assert strip_research_triggers("/research Understanding transformers") == \
            "Understanding transformers"

    def test_leaves_ordinary_text_untouched(self):
        assert strip_research_triggers("Just a normal note") == "Just a normal note"

    def test_case_insensitive_removal(self):
        assert "RESEARCH" not in strip_research_triggers("/RESEARCH thing").upper().replace("THING", "")


class TestLooksLikeDocument:
    @pytest.mark.parametrize("path", ["/tmp/a.pdf", "/tmp/a.pptx", "/tmp/A.PDF"])
    async def test_local_document_paths(self, path):
        assert await looks_like_document(None, path) is True

    @pytest.mark.parametrize("url", [
        "https://x.com/paper.pdf",
        "https://x.com/deck.pptx",
        "https://arxiv.org/pdf/2401.00001",
        "https://vendor.com/manuals/thing",
        "https://x.com/paper.pdf?download=1",
    ])
    async def test_document_urls(self, url):
        assert await looks_like_document(url, None) is True

    async def test_ordinary_note_path(self):
        assert await looks_like_document(None, "/tmp/note.md") is False

    async def test_no_url_and_no_file(self):
        assert await looks_like_document(None, None) is False

    async def test_unreachable_host_is_not_a_document(self):
        """The HEAD probe must fail closed, not raise."""
        assert await looks_like_document("http://127.0.0.1:9/none", None) is False


class TestWriteParaNote:
    def test_creates_a_note_with_valid_frontmatter(self, tmp_path):
        dest = write_para_note(str(tmp_path), "My Note", "Body text",
                               tags=["area", "health"], source_label="Telegram Input")
        content = open(dest, encoding="utf-8").read()
        fm = parse_frontmatter(content)
        assert fm["title"] == "My Note"
        assert fm["tags"] == ["area", "health"]
        assert "Body text" in content

    def test_awkward_title_still_produces_valid_yaml(self, tmp_path):
        dest = write_para_note(str(tmp_path), 'Note: with "quotes"', "Body",
                               tags=["idea"], source_label="Telegram Input")
        fm = parse_frontmatter(open(dest, encoding="utf-8").read())
        assert fm["title"] == 'Note: with "quotes"'

    def test_extra_frontmatter_is_merged(self, tmp_path):
        dest = write_para_note(str(tmp_path), "N", "B", tags=["area"],
                               source_label="x", extra_frontmatter={"area": "Health"})
        assert parse_frontmatter(open(dest, encoding="utf-8").read())["area"] == "Health"

    def test_name_collisions_get_a_suffix(self, tmp_path):
        a = write_para_note(str(tmp_path), "Same", "one", tags=[], source_label="x")
        b = write_para_note(str(tmp_path), "Same", "two", tags=[], source_label="x")
        assert a != b
        assert os.path.exists(a) and os.path.exists(b)
        assert "one" in open(a, encoding="utf-8").read()
        assert "two" in open(b, encoding="utf-8").read()

    def test_unsafe_title_does_not_escape_the_target_directory(self, tmp_path):
        dest = write_para_note(str(tmp_path), "../../escape", "B", tags=[], source_label="x")
        assert os.path.realpath(dest).startswith(os.path.realpath(str(tmp_path)))

    def test_raw_source_file_is_copied_alongside(self, tmp_path):
        src = tmp_path / "incoming.pdf"
        src.write_bytes(b"%PDF-1.4 fake")
        target = tmp_path / "out"
        dest = write_para_note(str(target), "With Attachment", "Body", tags=[],
                               source_label="Telegram Input", file_path=str(src))
        copied = [p for p in os.listdir(str(target)) if p.endswith("_raw.pdf")]
        assert copied, "raw source should be copied next to the note"
        assert "[[" in parse_frontmatter(open(dest, encoding="utf-8").read())["source"]

    def test_markdown_source_is_not_copied(self, tmp_path):
        src = tmp_path / "incoming.md"
        src.write_text("note", encoding="utf-8")
        target = tmp_path / "out2"
        write_para_note(str(target), "No Copy", "Body", tags=[],
                        source_label="Telegram Input", file_path=str(src))
        assert not [p for p in os.listdir(str(target)) if p.endswith("_raw.md")]


class TestQueueBackoff:
    @pytest.fixture
    def scratch_queue(self, monkeypatch):
        db = os.path.join(tempfile.mkdtemp(), "queue.db")
        monkeypatch.setattr(q, "DB_PATH", db)
        q.init_queue_db()
        return q

    def _age(self, qq, item_id, minutes):
        conn = qq.get_db_connection()
        conn.execute(
            f"UPDATE ingestion_queue SET updated_at = datetime('now','-{minutes} minutes') WHERE id = ?",
            (item_id,))
        conn.commit()
        conn.close()

    def test_pending_item_is_picked_up(self, scratch_queue):
        item_id = scratch_queue.add_to_queue("telegram", {"a": 1}, content_text="hi")
        assert scratch_queue.get_next_pending_item()["id"] == item_id

    def test_failed_item_is_not_retried_immediately(self, scratch_queue):
        item_id = scratch_queue.add_to_queue("telegram", {}, content_text="hi")
        scratch_queue.update_item_status(item_id, "failed", "boom")
        assert scratch_queue.get_next_pending_item() is None

    def test_first_backoff_tier_is_one_minute(self, scratch_queue):
        item_id = scratch_queue.add_to_queue("telegram", {}, content_text="hi")
        scratch_queue.update_item_status(item_id, "failed", "boom")
        self._age(scratch_queue, item_id, 2)
        assert scratch_queue.get_next_pending_item()["id"] == item_id

    def test_second_backoff_tier_is_five_minutes(self, scratch_queue):
        item_id = scratch_queue.add_to_queue("telegram", {}, content_text="hi")
        for _ in range(2):
            scratch_queue.update_item_status(item_id, "failed", "boom")
        self._age(scratch_queue, item_id, 2)
        assert scratch_queue.get_next_pending_item() is None, "2 min < the 5 min tier"
        self._age(scratch_queue, item_id, 6)
        assert scratch_queue.get_next_pending_item()["id"] == item_id

    def test_item_is_dropped_after_max_retries(self, scratch_queue):
        item_id = scratch_queue.add_to_queue("telegram", {}, content_text="hi")
        for _ in range(scratch_queue.MAX_RETRIES):
            scratch_queue.update_item_status(item_id, "failed", "boom")
        self._age(scratch_queue, item_id, 9999)
        assert scratch_queue.get_next_pending_item() is None

    def test_completed_item_is_not_picked_up_again(self, scratch_queue):
        item_id = scratch_queue.add_to_queue("telegram", {}, content_text="hi")
        scratch_queue.update_item_status(item_id, "completed")
        assert scratch_queue.get_next_pending_item() is None

    def test_timestamps_are_comparable_across_insert_and_update(self, scratch_queue):
        """Inserts used UTC and updates used local time, leaving them an hour apart."""
        item_id = scratch_queue.add_to_queue("telegram", {}, content_text="hi")
        conn = scratch_queue.get_db_connection()
        created = conn.execute("SELECT created_at FROM ingestion_queue WHERE id=?",
                               (item_id,)).fetchone()[0]
        conn.close()
        scratch_queue.update_item_status(item_id, "completed")
        conn = scratch_queue.get_db_connection()
        updated = conn.execute("SELECT updated_at FROM ingestion_queue WHERE id=?",
                               (item_id,)).fetchone()[0]
        conn.close()
        assert updated >= created, "updated_at must not appear to precede created_at"

    def test_stuck_processing_items_reset_on_startup(self, scratch_queue):
        item_id = scratch_queue.add_to_queue("telegram", {}, content_text="hi")
        scratch_queue.update_item_status(item_id, "processing")
        scratch_queue.init_queue_db()
        assert scratch_queue.get_next_pending_item()["id"] == item_id


class TestSyncConflictDetection:
    def test_marker_file(self):
        assert is_sync_conflict_file("a.sync-conflict-20260101-120000-ABCDEFG.md", "/tmp")

    def test_device_suffix_needs_the_original(self, tmp_path):
        (tmp_path / "Base.md").write_text("x", encoding="utf-8")
        assert is_sync_conflict_file("Base-ABCDEFG.md", str(tmp_path)) is True
        assert is_sync_conflict_file("Other-ABCDEFG.md", str(tmp_path)) is False
