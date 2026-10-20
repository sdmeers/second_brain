"""Regression tests for the 2026-10-18 misfiling: an emailed car-insurance schedule PDF,
correctly classified 'reference', was forced through Deep Research into
Research_Brain/Summaries (because it was a PDF) and then ingested a second time via the
inbox scanner (because Gmail staged attachments in the watched inbox folder)."""

import json
import os
from types import SimpleNamespace

import pytest

from backend.ingestion import gmail
from backend.ingestion import watcher
from backend.ingestion.classifier import ClassificationResult
from backend.utils import librarian
from backend.vault.paths import list_active_projects, list_areas
from backend.vault.tasks import parse_frontmatter


def _classified(category):
    async def fake_classify(text):
        fake_classify.seen = text
        return ClassificationResult(category=category, confidence=0.95, reasoning="test")
    return fake_classify


@pytest.fixture
def worker(monkeypatch, tmp_path):
    """Run _process_one_item with the LLM, queue DB and side-effecting pipelines stubbed."""
    calls = {}

    async def fake_prepare(item_id, file_path, content_text):
        return "POLICY SCHEDULE Policyholder: Steven Meers Vehicle: Skoda Octavia Policy no 123"

    async def fake_capture(**kwargs):
        calls["capture"] = kwargs
        return {"title": "captured", "node_path": None}

    async def fake_route(*args, routing_text=None):
        calls["route"] = args
        calls["routing_text"] = routing_text
        return "Filed", None, "PARA Area (Car)"

    async def fake_finalise(*args):
        calls["pipeline"] = args[-1]

    monkeypatch.setattr(watcher, "prepare_content", fake_prepare)
    monkeypatch.setattr(watcher, "process_capture_core", fake_capture)
    monkeypatch.setattr(watcher, "route_para_note", fake_route)
    monkeypatch.setattr(watcher, "finalise_item", fake_finalise)
    monkeypatch.setattr(watcher, "update_item_status", lambda *a, **k: None)

    pdf = tmp_path / "email_2026-10-18_09-35-15_0_schedule.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    async def run(category, subject="Insurance schedule"):
        monkeypatch.setattr(watcher, "classify_input", _classified(category))
        item = {"id": 1, "source_type": "email", "content_text": "", "file_path": str(pdf),
                "raw_payload": json.dumps({"subject": subject, "filename": "schedule.pdf"})}
        await watcher._process_one_item(item)
        return calls

    return run


class TestPdfRouting:
    @pytest.mark.parametrize("category", ["reference", "personal_record", "quick_note"])
    async def test_pdf_follows_the_classifier_not_deep_research(self, worker, category):
        calls = await worker(category)
        assert "capture" not in calls, "a PDF must not be forced into Deep Research"
        assert "route" in calls

    @pytest.mark.parametrize("category", ["task", "instruction", "voice_dump"])
    async def test_pdf_classified_as_text_only_category_is_still_filed(self, worker, category):
        """Those handlers keep only text; the document itself would be lost."""
        calls = await worker(category)
        assert "route" in calls

    async def test_deep_research_verdict_still_goes_deep(self, worker):
        calls = await worker("deep_research")
        assert calls["capture"]["deep_extraction"] is True
        assert calls["pipeline"] == "Deep Research"

    async def test_research_trigger_in_subject_still_goes_deep(self, worker):
        calls = await worker("reference", subject="/research this paper")
        assert calls["capture"]["deep_extraction"] is True

    async def test_subject_and_filename_reach_the_classifier_and_matcher(self, worker):
        calls = await worker("personal_record")
        assert watcher.classify_input.seen.startswith("Subject: Insurance schedule\nFilename: schedule.pdf")
        assert calls["routing_text"].startswith("Subject: Insurance schedule")
        # ...but are not saved into the note body (clean_input)
        assert not calls["route"][3].startswith("Subject:")


class TestBuildRoutingText:
    def test_no_provenance_leaves_text_alone(self):
        assert watcher.build_routing_text("hello", {}) == "hello"

    def test_email_body_is_included(self):
        out = watcher.build_routing_text("pdf text", {"subject": "S", "body": "file under car"}, "/x/a.pdf")
        assert out == "Subject: S\nFilename: a.pdf\nEmail note: file under car\n\npdf text"


class TestRouteParaNote:
    @pytest.fixture
    def no_match(self, monkeypatch):
        async def fake_match(text):
            fake_match.seen = text
            return (watcher.PARARoutingResult(matched_type="None", matched_name="None",
                                              note_title="Aviva Car Insurance Schedule", reasoning="t"),
                    [], list_areas())

        async def boom(**kwargs):
            raise AssertionError("personal records must not reach the capture pipeline")

        monkeypatch.setattr(watcher, "match_para_folder", fake_match)
        monkeypatch.setattr(watcher, "process_capture_core", boom)
        return fake_match

    async def test_unmatched_personal_record_lands_in_finance_and_legal(self, vault, no_match):
        cls = SimpleNamespace(category="personal_record")
        title, path, pipeline = await watcher.route_para_note(
            1, cls, "text", "text", None, None, {}, "Email Input", routing_text="Subject: x\n\ntext")
        assert path.startswith(str(vault / "03_Areas" / "Finance_and_legal"))
        assert "Research_Brain" not in path
        assert "record" in parse_frontmatter(open(path, encoding="utf-8").read())["tags"]
        assert no_match.seen == "Subject: x\n\ntext"

    async def test_area_match_uses_nested_area_path(self, vault, monkeypatch):
        (vault / "03_Areas" / "Car").mkdir(parents=True, exist_ok=True)

        async def fake_match(text):
            return (watcher.PARARoutingResult(matched_type="area", matched_name="Car",
                                              note_title="Insurance Schedule", reasoning="t"),
                    [], list_areas())
        monkeypatch.setattr(watcher, "match_para_folder", fake_match)
        _, path, pipeline = await watcher.route_para_note(
            1, SimpleNamespace(category="reference"), "t", "t", None, None, {}, "Email Input")
        assert os.path.dirname(path) == str(vault / "03_Areas" / "Car")
        assert pipeline == "PARA Area (Car)"


class TestLiveFolderLists:
    def test_projects_are_found_under_category_folders(self, vault):
        proj = vault / "02_Projects" / "Personal" / "Garage"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "Garage.md").write_text("---\nstatus: active\n---\n", encoding="utf-8")
        projects = list_active_projects()
        assert projects["Garage"] == "Personal/Garage"
        assert "Personal" not in projects and "Professional" not in projects

    def test_areas_include_one_nested_level(self, vault):
        (vault / "03_Areas" / "Work" / "Dstl").mkdir(parents=True, exist_ok=True)
        (vault / "03_Areas" / "Work" / "Sources").mkdir(parents=True, exist_ok=True)
        areas = list_areas()
        assert areas["Work"] == "Work"
        assert areas["Dstl"] == "Work/Dstl"
        assert "Sources" not in areas


class TestDetermineParaDestination:
    @pytest.fixture
    def model_says(self, monkeypatch):
        def set_reply(category, subfolder):
            async def fake_call(**kwargs):
                fake_call.system = kwargs["config"].system_instruction
                return SimpleNamespace(text=json.dumps(
                    {"category": category, "subfolder": subfolder, "reasoning": "t"}))
            monkeypatch.setattr(librarian, "call_gemini_with_retry", fake_call)
            return fake_call
        return set_reply

    async def test_pdf_no_longer_short_circuits_to_manuals(self, vault, model_says):
        (vault / "03_Areas" / "Car").mkdir(parents=True, exist_ok=True)
        fake = model_says("03_Areas", "Car")
        target, _ = await librarian.determine_para_destination("Insurance Schedule", "text", is_pdf=True)
        assert target == str(vault / "03_Areas" / "Car")
        assert "Car" in fake.system, "the live Area list must be in the prompt"

    async def test_unknown_subfolder_is_refused(self, vault, model_says):
        model_says("03_Areas", "Made_Up_Area")
        target, _ = await librarian.determine_para_destination("x", "text")
        assert not os.path.exists(vault / "03_Areas" / "Made_Up_Area")
        assert "Articles_and_News" in target


class TestGmailStaging:
    async def test_attachments_are_staged_outside_the_watched_inbox(self, vault, monkeypatch, tmp_path):
        queued = []
        monkeypatch.setattr("backend.config.STAGING_PATH", str(tmp_path / "staging"))
        monkeypatch.setattr(gmail, "add_to_queue", lambda **kw: queued.append(kw))

        async def no_notify(*a, **k):
            pass
        monkeypatch.setattr(gmail, "notify_via_telegram", no_notify)

        inbox_before = set(os.listdir(vault / "00_System" / "Inbox"))
        await gmail._ingest_email("Insurance schedule", "see attached", [
            {"filename": "schedule.pdf", "content_type": "application/pdf", "bytes": b"%PDF a"},
            {"filename": "certificate.pdf", "content_type": "application/pdf", "bytes": b"%PDF b"},
        ])

        assert set(os.listdir(vault / "00_System" / "Inbox")) == inbox_before
        assert len(queued) == 2, "one queue item per attachment"
        for item in queued:
            assert item["file_path"].startswith(str(tmp_path / "staging"))
            assert os.path.exists(item["file_path"])
            # body must not masquerade as extracted document text
            assert item["content_text"] == ""
            assert item["raw_payload"]["body"] == "see attached"
        assert {i["raw_payload"]["filename"] for i in queued} == {"schedule.pdf", "certificate.pdf"}

    async def test_body_only_email_is_queued_as_text(self, monkeypatch, tmp_path):
        queued = []
        monkeypatch.setattr("backend.config.STAGING_PATH", str(tmp_path / "staging"))
        monkeypatch.setattr(gmail, "add_to_queue", lambda **kw: queued.append(kw))

        async def no_notify(*a, **k):
            pass
        monkeypatch.setattr(gmail, "notify_via_telegram", no_notify)

        await gmail._ingest_email("Note", "remember the boiler code 1234", [])
        assert len(queued) == 1
        assert queued[0]["content_text"] == "remember the boiler code 1234"
        assert queued[0]["file_path"] is None
