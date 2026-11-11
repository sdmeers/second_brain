"""Project management for the GTD web app, and the pieces Telegram shares with it."""

import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import actions as actions_api
from backend.vault import actions, projects
from backend.vault.tasks import parse_frontmatter, parse_task_line, read_project

NOTE = """---
title: Bike Overhaul
type: project
category: Personal
outcome: Bike rides like new
due_date: '2026-12-01'
status: active
---
# Bike Overhaul

## Desired Outcome
Bike rides like new

## Project Steps
- [ ] True the rear wheel (Source: [[idea.md]])
- [ ] Replace the chain
- [x] Buy degreaser
Some note the user wrote here.

## Active Actions
> dataview goes here

## Reference Material
"""


@pytest.fixture(autouse=True)
def vault_with_a_project(vault, tasks_dir):
    for sub in ("02_Projects", "05_Archive/02_Projects"):
        shutil.rmtree(vault / sub, ignore_errors=True)
    for cat in ("Personal", "Professional"):
        (vault / "02_Projects" / cat).mkdir(parents=True)
    folder = vault / "02_Projects/Personal/Bike Overhaul"
    folder.mkdir()
    (folder / "Bike Overhaul.md").write_text(NOTE, encoding="utf-8")
    (folder / "receipt.pdf").write_bytes(b"pdf")
    (tasks_dir / "Next_Actions.md").write_text(
        "- [ ] Order brake pads @computer #project/Bike_Overhaul #personal\n"
        "- [ ] Unrelated thing @home #personal\n", encoding="utf-8")
    yield


def note(vault) -> str:
    return (vault / "02_Projects/Personal/Bike Overhaul/Bike Overhaul.md").read_text(encoding="utf-8")


def bike():
    return next(p for p in projects.list_projects()["active"] if p["name"] == "Bike Overhaul")


class TestListing:
    def test_project_carries_its_roadmap_and_open_actions(self):
        p = bike()
        assert p["category"] == "personal"
        assert p["outcome"] == "Bike rides like new"
        assert p["due"] == "2026-12-01"
        assert p["steps"] == ["True the rear wheel", "Replace the chain"]
        assert [(a["text"], a["list"]) for a in p["actions"]] == [("Order brake pads", "next")]
        assert p["stuck"] is False

    def test_project_with_no_open_action_is_stuck(self, tasks_dir):
        (tasks_dir / "Next_Actions.md").write_text("- [ ] Unrelated thing @home #personal\n", encoding="utf-8")
        assert bike()["stuck"] is True

    def test_a_waiting_for_keeps_a_project_unstuck(self, tasks_dir):
        (tasks_dir / "Next_Actions.md").write_text("", encoding="utf-8")
        (tasks_dir / "Waiting_For.md").write_text(
            "- [ ] Wheel back from the shop #project/Bike_Overhaul #personal #waiting(Shop)\n", encoding="utf-8")
        assert bike()["stuck"] is False
        assert bike()["actions"][0]["list"] == "waiting"


class TestCreateAndUpdate:
    def test_create_keeps_the_name_as_typed(self, vault):
        projects.create_project({"name": "TF RAID integration", "category": "professional",
                                 "outcome": "Arrays: migrated", "due_date": "2027-01-31"})
        path = vault / "02_Projects/Professional/TF RAID integration/TF RAID integration.md"
        fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        assert (fm["title"], fm["outcome"], fm["status"]) == ("TF RAID integration", "Arrays: migrated", "active")

    def test_duplicate_name_is_refused(self):
        with pytest.raises(actions.OpSkipped) as exc:
            projects.create_project({"name": "bike_overhaul", "category": "personal"})
        assert exc.value.reason == "project_exists"

    @pytest.mark.parametrize("fields,reason", [
        ({"name": "  "}, "invalid"), ({"name": "X", "due_date": "soon"}, "bad_date"), ({}, "invalid")])
    def test_bad_input(self, fields, reason):
        with pytest.raises(actions.OpSkipped) as exc:
            projects.create_project(fields)
        assert exc.value.reason == reason

    def test_a_name_cannot_escape_the_projects_folder(self, vault):
        projects.create_project({"name": "../../etc/Sneaky", "category": "personal"})
        assert (vault / "02_Projects/Personal/Sneaky/Sneaky.md").exists()

    def test_update_changes_frontmatter_and_the_matching_body_line(self, vault):
        projects.update_project("Bike Overhaul", {"outcome": "Rides: like new", "due_date": None})
        fm = parse_frontmatter(note(vault))
        assert fm["outcome"] == "Rides: like new" and fm["due_date"] == ""
        assert "## Desired Outcome\nRides: like new\n" in note(vault)
        assert "Some note the user wrote here." in note(vault)

    def test_unknown_project_is_skipped(self):
        with pytest.raises(actions.OpSkipped) as exc:
            projects.update_project("Nope", {"outcome": "x"})
        assert exc.value.reason == "project_not_found"


class TestRoadmap:
    def test_add_goes_to_the_bottom_and_leaves_the_rest_of_the_note_alone(self, vault):
        projects.add_step("Bike Overhaul", "  Fit new   tyres ")
        assert bike()["steps"] == ["True the rear wheel", "Replace the chain", "Fit new tyres"]
        text = note(vault)
        assert "- [x] Buy degreaser" in text and "Some note the user wrote here." in text
        assert "## Active Actions\n> dataview goes here" in text
        assert text.index("- [ ] Fit new tyres") < text.index("- [x] Buy degreaser")

    def test_reorder_keeps_each_steps_original_line(self, vault):
        projects.reorder_steps("Bike Overhaul", ["Replace the chain", "True the rear wheel"])
        assert bike()["steps"] == ["Replace the chain", "True the rear wheel"]
        assert "- [ ] True the rear wheel (Source: [[idea.md]])" in note(vault)

    def test_reorder_tolerates_a_stale_view(self):
        projects.add_step("Bike Overhaul", "Fit new tyres")
        projects.reorder_steps("Bike Overhaul", ["Replace the chain", "A step deleted elsewhere", "True the rear wheel"])
        assert bike()["steps"] == ["Replace the chain", "True the rear wheel", "Fit new tyres"]

    def test_edit_and_remove(self):
        projects.edit_step("Bike Overhaul", "Replace the chain", "Replace chain and cassette")
        projects.remove_step("Bike Overhaul", "True the rear wheel")
        assert bike()["steps"] == ["Replace chain and cassette"]

    def test_stale_step_is_skipped(self):
        for call in (lambda: projects.edit_step("Bike Overhaul", "Gone", "x"),
                     lambda: projects.remove_step("Bike Overhaul", "Gone"),
                     lambda: projects.promote_step("Bike Overhaul", "Gone", {})):
            with pytest.raises(actions.OpSkipped) as exc:
                call()
            assert exc.value.reason == "not_found"

    def test_promote_moves_the_step_into_next_actions(self, tasks_dir):
        result = projects.promote_step("Bike Overhaul", "Replace the chain", {"contexts": ["@home"]})
        task = parse_task_line(result.new_raw)
        assert (task.display, task.contexts, task.project, task.category) == (
            "Replace the chain", ["@home"], "Bike_Overhaul", "personal")
        assert result.new_raw in (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8")
        assert bike()["steps"] == ["True the rear wheel"]
        assert len(bike()["actions"]) == 2

    def test_a_note_without_a_roadmap_section_gains_one(self, vault):
        path = vault / "02_Projects/Personal/Bike Overhaul/Bike Overhaul.md"
        path.write_text("---\nstatus: active\n---\n# Bike Overhaul\n", encoding="utf-8")
        projects.add_step("Bike Overhaul", "First step")
        assert read_project(str(path)).steps == ["First step"]


class TestArchiveAndRestore:
    def test_round_trip_moves_the_whole_folder(self, vault):
        projects.archive_project("Bike Overhaul")
        archived = vault / "05_Archive/02_Projects/Personal/Bike Overhaul"
        assert (archived / "receipt.pdf").exists()
        assert not (vault / "02_Projects/Personal/Bike Overhaul").exists()
        fm = parse_frontmatter((archived / "Bike Overhaul.md").read_text(encoding="utf-8"))
        assert fm["status"] == "completed" and fm["date_completed"]
        listing = projects.list_projects()
        assert listing["active"] == []
        assert [(p["name"], p["category"]) for p in listing["archived"]] == [("Bike Overhaul", "personal")]

        projects.restore_project("Bike Overhaul", "personal")
        fm = parse_frontmatter(note(vault))
        assert fm["status"] == "active" and "date_completed" not in fm
        assert (vault / "02_Projects/Personal/Bike Overhaul/receipt.pdf").exists()
        assert bike()["steps"] == ["True the rear wheel", "Replace the chain"]

    def test_an_existing_archive_of_the_same_name_is_never_overwritten(self, vault):
        """/archive used to rmtree whatever was already there."""
        earlier = vault / "05_Archive/02_Projects/Personal/Bike Overhaul"
        earlier.mkdir(parents=True)
        (earlier / "old notes.md").write_text("precious", encoding="utf-8")
        with pytest.raises(actions.OpSkipped) as exc:
            projects.archive_project("Bike Overhaul")
        assert exc.value.reason == "archive_exists"
        assert (earlier / "old notes.md").read_text(encoding="utf-8") == "precious"
        assert bike()["name"] == "Bike Overhaul"  # still active, untouched
        assert parse_frontmatter(note(vault))["status"] == "active"

    def test_restore_refuses_to_land_on_an_active_project(self, vault):
        projects.archive_project("Bike Overhaul")
        projects.create_project({"name": "Bike Overhaul", "category": "personal"})
        with pytest.raises(actions.OpSkipped) as exc:
            projects.restore_project("Bike Overhaul", "personal")
        assert exc.value.reason == "project_exists"

    def test_single_file_project_archives_as_a_file(self, vault):
        (vault / "02_Projects/Professional/Quick One.md").write_text("---\nstatus: active\n---\n# Quick One\n", encoding="utf-8")
        projects.archive_project("Quick One")
        assert (vault / "05_Archive/02_Projects/Professional/Quick One.md").exists()


class TestApi:
    @pytest.fixture
    def client(self):
        app = FastAPI()
        app.include_router(actions_api.router)
        actions_api._recent.clear()
        return TestClient(app)

    def test_state_includes_projects(self, client):
        body = client.get("/api/actions").json()
        assert body["projects"]["active"][0]["name"] == "Bike Overhaul"
        assert body["projects"]["archived"] == []

    def test_project_ops_in_a_batch(self, client):
        body = client.post("/api/actions/ops", json={"ops": [
            {"op_id": "1", "type": "project_create", "fields": {"name": "Garden", "category": "personal"}},
            {"op_id": "2", "type": "step_add", "project": "Garden", "text": "Measure the fence"},
            {"op_id": "3", "type": "step_promote", "project": "Garden", "step": "Measure the fence",
             "fields": {"contexts": ["@home"]}},
            {"op_id": "4", "type": "project_archive", "project": "Bike Overhaul"},
            {"op_id": "5", "type": "step_add", "project": "Bike Overhaul", "text": "too late"},
        ]}).json()
        assert [r["status"] for r in body["results"]] == ["applied"] * 4 + ["skipped"]
        assert body["results"][4]["reason"] == "project_not_found"
        garden = body["projects"]["active"][0]
        assert (garden["name"], garden["steps"], garden["stuck"]) == ("Garden", [], False)
        assert [p["name"] for p in body["projects"]["archived"]] == ["Bike Overhaul"]

    def test_completing_a_project_action_reports_the_project(self, client):
        body = client.post("/api/actions/ops", json={"ops": [
            {"op_id": "1", "type": "complete", "list": "next",
             "raw": "- [ ] Order brake pads @computer #project/Bike_Overhaul #personal"}]}).json()
        assert body["results"][0]["project"] == "Bike_Overhaul"
        assert body["projects"]["active"][0]["stuck"] is True

    def test_clarify_to_a_new_project_keeps_the_typed_name(self, client, tasks_dir, vault):
        (tasks_dir / "Captured.md").write_text("- sort out the NAS\n", encoding="utf-8")
        body = client.post("/api/actions/ops", json={"ops": [
            {"op_id": "1", "type": "clarify", "raw": "- sort out the NAS", "outcome": "new_project",
             "fields": {"name": "NAS rebuild", "category": "professional", "first_action": "Order disks"}}]}).json()
        assert body["results"][0]["status"] == "applied"
        assert (vault / "02_Projects/Professional/NAS rebuild/NAS rebuild.md").exists()
        assert any(i["project"] == "NAS_rebuild" for i in body["lists"]["next"])
