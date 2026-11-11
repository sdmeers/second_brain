"""Filing captured items: the writes shared by Telegram /clarify and the GTD web app."""

import shutil
from datetime import date

import pytest

from backend.vault import actions, clarify_ops
from backend.vault.tasks import parse_task_line, read_project

VOICE = "- chase the electrician (Captured on 2026-11-09 18:42 via Telegram Voice) (Source: [[2026-11-09_18-42-00_voice.ogg]])"
TYPED = "- idea for dads birthday present (Captured on 2026-11-10 08:15 via Telegram)"


@pytest.fixture(autouse=True)
def clean_vault(vault, tasks_dir):
    for sub in ("02_Projects/Personal", "02_Projects/Professional", "00_System/Inbox"):
        shutil.rmtree(vault / sub, ignore_errors=True)
        (vault / sub).mkdir(parents=True)
    (tasks_dir / "Captured.md").write_text(f"# 📥 Captured\n\n{VOICE}\n{TYPED}\n", encoding="utf-8")
    (vault / "00_System/Inbox/2026-11-09_18-42-00_voice.ogg").write_bytes(b"audio")
    yield


def captured():
    return [i["text"] for i in actions.read_action_lists()["lists"]["captured"]]


def only_line(tasks_dir, name):
    lines = [ln for ln in (tasks_dir / name).read_text(encoding="utf-8").splitlines() if ln.startswith("- [")]
    assert len(lines) == 1, lines
    return lines[0]


class TestRemoveCaptured:
    def test_removes_exactly_that_line_and_archives_its_source(self, vault):
        assert clarify_ops.remove_captured(VOICE) == "2026-11-09_18-42-00_voice.ogg"
        assert captured() == ["idea for dads birthday present"]
        assert not (vault / "00_System/Inbox/2026-11-09_18-42-00_voice.ogg").exists()
        assert (vault / "00_System/Inbox/Archive/2026-11-09_18-42-00_voice.ogg").exists()

    def test_does_not_cut_the_same_words_out_of_a_longer_item(self, tasks_dir):
        """It used to str.replace() the line's text anywhere in the file."""
        longer = "- note: - buy milk and also bread"
        (tasks_dir / "Captured.md").write_text(f"{longer}\n- buy milk\n", encoding="utf-8")
        clarify_ops.remove_captured("- buy milk")
        assert captured() == ["note: - buy milk and also bread"]

    def test_last_item_leaves_the_inbox_zero_message(self, tasks_dir):
        clarify_ops.remove_captured(VOICE)
        clarify_ops.remove_captured(TYPED)
        assert "Inbox Zero" in (tasks_dir / "Captured.md").read_text(encoding="utf-8")

    def test_item_without_a_source_returns_none(self):
        assert clarify_ops.remove_captured(TYPED) is None


class TestDestinations:
    def test_next_action_with_context_and_project(self, tasks_dir, vault):
        (vault / "02_Projects/Personal/House").mkdir()
        (vault / "02_Projects/Personal/House/House.md").write_text("---\nstatus: active\n---\n# House\n", encoding="utf-8")
        clarify_ops.to_next(VOICE, category="Personal", contexts=["@phone"], project="House")
        task = parse_task_line(only_line(tasks_dir, "Next_Actions.md"))
        assert (task.display, task.contexts, task.project, task.category) == (
            "chase the electrician", ["@phone"], "House", "personal")
        assert "Captured on" not in task.raw and "Source:" not in task.raw
        assert captured() == ["idea for dads birthday present"]

    def test_defer_is_a_next_action_hidden_until_a_date(self, tasks_dir):
        clarify_ops.to_next(TYPED, category="Professional", tickler="2027-01-05")
        task = parse_task_line(only_line(tasks_dir, "Next_Actions.md"))
        assert task.tickler == date(2027, 1, 5) and task.category == "professional"

    def test_waiting_records_who(self, tasks_dir):
        clarify_ops.to_waiting(VOICE, waiting_on="Dave", category="Personal")
        task = parse_task_line(only_line(tasks_dir, "Waiting_For.md"))
        assert task.waiting_on == "Dave" and task.display == "chase the electrician"

    def test_someday(self, tasks_dir):
        clarify_ops.to_someday(TYPED, category="Personal")
        assert parse_task_line(only_line(tasks_dir, "Someday_Maybe.md")).display == "idea for dads birthday present"

    def test_reworded_text_is_what_gets_filed(self, tasks_dir):
        clarify_ops.to_next(VOICE, category="Personal", text="Chase the electrician about the lights")
        assert parse_task_line(only_line(tasks_dir, "Next_Actions.md")).display == "Chase the electrician about the lights"
        assert captured() == ["idea for dads birthday present"]

    def test_done_goes_to_the_archive(self, tasks_dir):
        clarify_ops.to_done(TYPED)
        assert "~~idea for dads birthday present~~ (Completed on" in (tasks_dir / "z_Completed.md").read_text(encoding="utf-8")
        assert captured() == ["chase the electrician"]

    def test_new_project_from_telegram_keeps_the_item_as_first_step(self, vault):
        path = clarify_ops.to_new_project(TYPED, name="dads birthday", category="Personal", outcome="A present he loves")
        project = read_project(path)
        assert project.name == "Dads Birthday"
        assert project.outcome == "A present he loves"
        assert project.steps == ["idea for dads birthday present"]

    def test_new_project_with_a_first_action_starts_unstuck(self, tasks_dir):
        path = clarify_ops.to_new_project(TYPED, name="Dads Birthday", category="Personal",
                                          outcome="A present he loves",
                                          first_action="Ask mum what he wants", contexts=["@phone"])
        task = parse_task_line(only_line(tasks_dir, "Next_Actions.md"))
        assert (task.display, task.project, task.contexts) == ("Ask mum what he wants", "Dads_Birthday", ["@phone"])
        assert read_project(path).steps == []
        assert captured() == ["chase the electrician"]

    def test_project_step(self, vault):
        path = clarify_ops.to_new_project(TYPED, name="Lights", category="Personal", first_action="x")
        clarify_ops.to_project_step(VOICE, project="Lights")
        assert read_project(path).steps == ["chase the electrician"]
        assert captured() == []


class TestCaptureOp:
    def test_capture_appends_a_line_the_client_can_predict(self):
        result = actions.capture("  buy   stamps ", "2026-11-11 09:30")
        assert result.new_raw == "- buy stamps (Captured on 2026-11-11 09:30 via GTD app)"
        assert captured()[-1] == "buy stamps"

    def test_sending_the_same_capture_twice_adds_it_once(self):
        actions.capture("buy stamps", "2026-11-11 09:30")
        actions.capture("buy stamps", "2026-11-11 09:30")
        assert captured().count("buy stamps") == 1

    @pytest.mark.parametrize("text,stamp,reason", [("   ", None, "invalid"), ("x", "yesterday", "bad_date")])
    def test_rejects_bad_input(self, text, stamp, reason):
        with pytest.raises(actions.OpSkipped) as exc:
            actions.capture(text, stamp)
        assert exc.value.reason == reason


class TestClarifyOp:
    def test_files_and_returns_the_new_line(self, tasks_dir):
        result = actions.clarify(VOICE, "next", {"category": "personal", "contexts": ["@phone"]})
        assert result.new_raw == only_line(tasks_dir, "Next_Actions.md") == "- [ ] chase the electrician @phone #personal"

    def test_item_already_clarified_elsewhere_is_skipped(self, tasks_dir):
        clarify_ops.trash(VOICE)  # e.g. /clarify in Telegram got there first
        with pytest.raises(actions.OpSkipped) as exc:
            actions.clarify(VOICE, "next", {"category": "personal"})
        assert exc.value.reason == "not_found"
        assert (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8") == ""

    def test_unknown_project_is_refused_rather_than_created(self, vault):
        with pytest.raises(actions.OpSkipped) as exc:
            actions.clarify(VOICE, "next", {"category": "personal", "project": "Typo Project"})
        assert exc.value.reason == "unknown_project"
        assert not (vault / "02_Projects/Personal/Typo Project").exists()
        assert "chase the electrician" in captured()

    @pytest.mark.parametrize("outcome,fields,reason", [
        ("waiting", {"category": "personal"}, "invalid"),
        ("new_project", {"category": "personal"}, "invalid"),
        ("project_step", {}, "unknown_project"),
        ("next", {"tickler": "soon"}, "bad_date"),
        ("shred", {}, "bad_outcome"),
    ])
    def test_incomplete_requests_leave_the_item_in_captured(self, outcome, fields, reason):
        with pytest.raises(actions.OpSkipped) as exc:
            actions.clarify(VOICE, outcome, fields)
        assert exc.value.reason == reason
        assert "chase the electrician" in captured()

    def test_trash_and_done(self, tasks_dir):
        actions.clarify(VOICE, "trash", {})
        actions.clarify(TYPED, "done", {})
        assert captured() == []
        assert "idea for dads birthday present" in (tasks_dir / "z_Completed.md").read_text(encoding="utf-8")

    def test_project_names_are_listed_for_the_picker(self):
        actions.clarify(TYPED, "new_project", {"name": "Dads Birthday", "category": "personal", "first_action": "Ask mum"})
        assert actions.read_action_lists()["projects"] == {"personal": ["Dads Birthday"], "professional": []}

    def test_capture_then_clarify_in_one_offline_batch(self, tasks_dir):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.api import actions as actions_api
        app = FastAPI()
        app.include_router(actions_api.router)
        actions_api._recent.clear()
        raw = "- buy stamps (Captured on 2026-11-11 09:30 via GTD app)"
        body = TestClient(app).post("/api/actions/ops", json={"ops": [
            {"op_id": "a", "type": "capture", "text": "buy stamps", "captured_at": "2026-11-11 09:30"},
            {"op_id": "b", "type": "clarify", "raw": raw, "outcome": "someday", "fields": {"category": "personal"}},
        ]}).json()
        assert [r["status"] for r in body["results"]] == ["applied", "applied"]
        assert [i["text"] for i in body["lists"]["someday"]] == ["buy stamps"]
