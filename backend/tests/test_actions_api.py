"""The actions web app's backend: in-place line edits, list operations and the HTTP API."""

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import actions as actions_api
from backend.utils.tasks_dashboard import maintain_tasks_dashboard
from backend.vault import actions
from backend.vault.tasks import parse_task_line, update_task_line

FULL = ("- [ ] Call the plumber @phone #project/Bathroom_Refit #personal 📅 2026-11-20 "
        "(Captured from Telegram on 2026-11-01 10:00:00) (Source: [[note.md]])")


class TestUpdateTaskLine:
    def test_no_changes_is_a_no_op(self):
        assert update_task_line(FULL) == FULL

    def test_description_change_keeps_every_other_field(self):
        out = update_task_line(FULL, description="Ring the plumber")
        task = parse_task_line(out)
        assert task.display == "Ring the plumber"
        assert task.contexts == ["@phone"]
        assert task.project == "Bathroom_Refit"
        assert task.category == "personal"
        assert task.due == date(2026, 11, 20)
        assert out.endswith("(Captured from Telegram on 2026-11-01 10:00:00) (Source: [[note.md]])")

    def test_none_clears_a_field(self):
        out = update_task_line(FULL, due=None)
        assert "📅" not in out
        assert "@phone" in out

    def test_sets_waiting_and_tickler(self):
        out = update_task_line(FULL, waiting_on="Bob", tickler="2026-12-01")
        task = parse_task_line(out)
        assert task.waiting_on == "Bob"
        assert task.tickler == date(2026, 12, 1)
        assert task.due == date(2026, 11, 20)

    def test_waiting_tag_survives_an_unrelated_edit(self):
        """render_task_line has no waiting field, which is why edits are done in place."""
        line = "- [ ] Quote for the roof #personal #waiting(Ann)"
        assert "#waiting(Ann)" in update_task_line(line, description="Roof quote")

    def test_contexts_are_replaced_and_normalised(self):
        out = update_task_line("- [ ] Thing @home #personal", contexts=["errands", "@phone"])
        assert parse_task_line(out).contexts == ["@errands", "@phone"]

    def test_text_with_brackets_and_other_hashtags_is_preserved(self):
        out = update_task_line("- [ ] [Draft] review #idea @computer #personal", due="2026-12-01")
        assert out.startswith("- [ ] [Draft] review #idea ")

    def test_newlines_cannot_break_the_file(self):
        assert "\n" not in update_task_line(FULL, description="two\nlines")

    def test_empty_description_is_rejected(self):
        with pytest.raises(ValueError):
            update_task_line(FULL, description="   ")

    def test_captured_bullet_keeps_its_suffixes(self):
        line = "- recieve the thing (Captured on 2026-11-01 10:00 via Telegram) (Source: [[a.md]])"
        assert update_task_line(line, description="receive the thing") == (
            "- receive the thing (Captured on 2026-11-01 10:00 via Telegram) (Source: [[a.md]])")


def seed(tasks_dir, name, *lines):
    (tasks_dir / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def texts(list_name, category=None):
    items = actions.read_action_lists()["lists"][list_name]
    return [i["text"] for i in items if category is None or i["category"] == category]


A = "- [ ] Alpha @home #personal"
B = "- [ ] Bravo @home #personal 📅 2020-01-01"
C = "- [ ] Charlie @office #professional"
D = "- [ ] Delta @phone #project/Garden #personal"


class TestReadActionLists:
    def test_lists_come_back_in_file_order_with_parsed_fields(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A, B, C)
        data = actions.read_action_lists()
        nxt = data["lists"]["next"]
        assert [i["text"] for i in nxt] == ["Alpha", "Bravo", "Charlie"]
        assert nxt[1]["due"] == "2020-01-01"
        assert nxt[2]["category"] == "professional"
        assert nxt[0]["contexts"] == ["@home"]
        assert data["today"] == date.today().isoformat()

    def test_ticked_lines_are_left_out(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A, "- [x] Done already #personal")
        assert texts("next") == ["Alpha"]

    def test_future_tickler_is_flagged_deferred(self, tasks_dir):
        later = (date.today() + timedelta(days=30)).isoformat()
        seed(tasks_dir, "Next_Actions.md", f"- [ ] Later #personal 🔔 {later}")
        assert actions.read_action_lists()["lists"]["next"][0]["deferred"] is True

    def test_captured_items_are_plain_text(self, tasks_dir):
        seed(tasks_dir, "Captured.md", "- a raw thought (Captured on 2026-11-01 10:00 via Telegram)")
        item = actions.read_action_lists()["lists"]["captured"][0]
        assert item["text"] == "a raw thought"
        assert item["note"] == "Captured on 2026-11-01 10:00 via Telegram"

    def test_untagged_line_is_addressable_by_its_normalised_text(self, tasks_dir):
        """The sweeper tags untagged lines within seconds; raw must match what it writes."""
        seed(tasks_dir, "Next_Actions.md", "- [ ] Untagged thing")
        raw = actions.read_action_lists()["lists"]["next"][0]["raw"]
        assert raw == "- [ ] Untagged thing #personal"
        actions.complete_action("next", raw)
        assert texts("next") == []


class TestComplete:
    def test_moves_the_line_to_the_archive(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A, B)
        actions.complete_action("next", A)
        assert texts("next") == ["Bravo"]
        archive = (tasks_dir / "z_Completed.md").read_text(encoding="utf-8")
        assert "~~Alpha @home #personal~~ (Completed on" in archive

    def test_reports_the_project_for_step_promotion(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", D)
        assert actions.complete_action("next", D).project == "Garden"

    def test_stale_line_is_skipped_not_guessed(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A)
        with pytest.raises(actions.OpSkipped) as exc:
            actions.complete_action("next", "- [ ] Alpha (old wording) #personal")
        assert exc.value.reason == "not_found"
        assert texts("next") == ["Alpha"]

    def test_captured_cannot_be_completed(self, tasks_dir):
        seed(tasks_dir, "Captured.md", "- a raw thought")
        with pytest.raises(actions.OpSkipped) as exc:
            actions.complete_action("captured", "- a raw thought")
        assert exc.value.reason == "list_not_editable"


class TestEdit:
    def test_edits_in_place_and_returns_the_new_line(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A, B)
        result = actions.edit_action("next", A, {"description": "Alpha prime", "due": "2026-12-24"})
        assert result.new_raw == "- [ ] Alpha prime @home #personal 📅 2026-12-24"
        assert texts("next") == ["Alpha prime", "Bravo"]

    def test_changing_category_moves_to_the_top_of_the_other_side(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A, B, C)
        actions.edit_action("next", B, {"category": "professional"})
        assert texts("next", "personal") == ["Alpha"]
        assert texts("next", "professional") == ["Bravo", "Charlie"]

    def test_captured_allows_only_the_text(self, tasks_dir):
        seed(tasks_dir, "Captured.md", "- recieve parcel (Captured on 2026-11-01 10:00 via Telegram)")
        raw = actions.read_action_lists()["lists"]["captured"][0]["raw"]
        result = actions.edit_action("captured", raw, {"description": "receive parcel", "due": "2026-12-01"})
        assert result.new_raw == "- receive parcel (Captured on 2026-11-01 10:00 via Telegram)"

    @pytest.mark.parametrize("fields,reason", [
        ({"due": "next tuesday"}, "bad_date"),
        ({"contexts": ["two words"]}, "bad_context"),
        ({"category": "hobby"}, "bad_category"),
        ({"description": ""}, "invalid"),
    ])
    def test_bad_values_never_reach_the_file(self, tasks_dir, fields, reason):
        seed(tasks_dir, "Next_Actions.md", A)
        with pytest.raises(actions.OpSkipped) as exc:
            actions.edit_action("next", A, fields)
        assert exc.value.reason == reason
        assert actions.read_action_lists()["lists"]["next"][0]["raw"] == A


class TestMove:
    def test_next_to_waiting_records_who_and_follow_up(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A, B)
        seed(tasks_dir, "Waiting_For.md", "- [ ] Existing #personal #waiting(Zed)")
        result = actions.move_action("next", "waiting", A, waiting_on="Bob", follow_up="2026-12-01")
        assert result.new_raw == "- [ ] Alpha @home #personal #waiting(Bob) 🔔 2026-12-01"
        assert texts("next") == ["Bravo"]
        assert texts("waiting") == ["Alpha", "Existing"]

    def test_waiting_back_to_next_drops_the_waiting_tag(self, tasks_dir):
        seed(tasks_dir, "Waiting_For.md", "- [ ] Alpha @home #personal #waiting(Bob)")
        result = actions.move_action("waiting", "next", "- [ ] Alpha @home #personal #waiting(Bob)")
        assert result.new_raw == A
        assert texts("waiting") == []

    def test_next_to_someday_keeps_the_line_unchanged(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A)
        assert actions.move_action("next", "someday", A).new_raw == A
        assert texts("someday") == ["Alpha"]

    def test_captured_is_off_limits_in_both_directions(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A)
        seed(tasks_dir, "Captured.md", "- a raw thought")
        for src, dst, raw in (("captured", "next", "- a raw thought"), ("next", "captured", A)):
            with pytest.raises(actions.OpSkipped):
                actions.move_action(src, dst, raw)
        assert texts("next") == ["Alpha"]
        assert texts("captured") == ["a raw thought"]


class TestReorder:
    def test_applies_the_given_order_within_one_category(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A, B, D, C)
        actions.reorder_actions("next", "personal", [D, A, B])
        assert texts("next", "personal") == ["Delta", "Alpha", "Bravo"]
        assert texts("next", "professional") == ["Charlie"]

    def test_tolerates_a_stale_client_view(self, tasks_dir):
        """Bravo was completed elsewhere and Delta added since the client last loaded."""
        seed(tasks_dir, "Next_Actions.md", A, D, "- [ ] Echo #personal")
        actions.reorder_actions("next", "personal", ["- [ ] Echo #personal", B, A])
        assert texts("next", "personal") == ["Echo", "Alpha", "Delta"]

    def test_order_survives_the_sweeper(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A, B, D)
        actions.reorder_actions("next", "personal", [D, A, B])

        async def sweep_twice():
            await maintain_tasks_dashboard()
            await maintain_tasks_dashboard()

        import asyncio
        asyncio.run(sweep_twice())
        assert texts("next", "personal") == ["Delta", "Alpha", "Bravo"]


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(actions_api.router)
    actions_api._recent.clear()
    return TestClient(app)


def op(op_id, type_, list_="next", **kw):
    return {"op_id": op_id, "type": type_, "list": list_, **kw}


class TestApi:
    def test_get_returns_every_list(self, client, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A)
        body = client.get("/api/actions").json()
        assert set(body["lists"]) == {"captured", "next", "waiting", "someday"}
        assert body["lists"]["next"][0]["text"] == "Alpha"

    def test_batch_reports_each_op_and_returns_fresh_state(self, client, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A, B)
        body = client.post("/api/actions/ops", json={"ops": [
            op("1", "complete", raw=A),
            op("2", "complete", raw="- [ ] Gone #personal"),
        ]}).json()
        assert [r["status"] for r in body["results"]] == ["applied", "skipped"]
        assert body["results"][1]["reason"] == "not_found"
        assert [i["text"] for i in body["lists"]["next"]] == ["Bravo"]

    def test_offline_edit_then_complete_of_the_same_action(self, client, tasks_dir):
        """The client queued both against the text it knew; the second must follow the first."""
        seed(tasks_dir, "Next_Actions.md", A)
        body = client.post("/api/actions/ops", json={"ops": [
            op("1", "edit", raw=A, fields={"description": "Alpha prime"}),
            op("2", "move", raw=A, to="someday"),
            op("3", "reorder", list_="someday", category="personal", order=[A]),
            op("4", "complete", list_="someday", raw=A),
        ]}).json()
        assert [r["status"] for r in body["results"]] == ["applied"] * 4
        assert "Alpha prime" in (tasks_dir / "z_Completed.md").read_text(encoding="utf-8")

    def test_resending_a_batch_is_harmless(self, client, tasks_dir):
        """A lost response makes the client resend; that must not report a failure."""
        seed(tasks_dir, "Next_Actions.md", A, B)
        batch = {"ops": [op("same-id", "complete", raw=A)]}
        first = client.post("/api/actions/ops", json=batch).json()
        second = client.post("/api/actions/ops", json=batch).json()
        assert first["results"] == second["results"]
        assert second["results"][0]["status"] == "applied"
        archive = (tasks_dir / "z_Completed.md").read_text(encoding="utf-8")
        assert archive.count("Alpha") == 1

    def test_unknown_op_type_is_rejected(self, client):
        assert client.post("/api/actions/ops", json={"ops": [op("1", "delete", raw=A)]}).status_code == 422


class TestFocusStar:
    """The star on next actions is a #focus tag on the line, so every device sees it."""

    STARRED = "- [ ] Alpha @home #personal #focus"

    def test_parses_and_is_hidden_from_the_display_text(self):
        task = parse_task_line("- [ ] Ring the bank @phone #personal #focus 📅 2026-12-01")
        assert task.focus is True
        assert task.display == "Ring the bank"
        assert parse_task_line(A).focus is False

    def test_a_longer_tag_is_not_mistaken_for_the_star(self):
        assert parse_task_line("- [ ] Thing #focusgroup #personal").focus is False

    def test_set_clear_and_survive_an_unrelated_edit(self):
        assert update_task_line(A, focus=True) == self.STARRED
        assert update_task_line(self.STARRED, focus=False) == A
        assert update_task_line(self.STARRED, description="Alpha prime") == "- [ ] Alpha prime @home #personal #focus"
        assert update_task_line(self.STARRED, focus=True) == self.STARRED

    def test_toggle_through_the_api_keeps_the_action_in_place(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", B, A)
        result = actions.edit_action("next", A, {"focus": True})
        assert result.new_raw == self.STARRED
        items = actions.read_action_lists()["lists"]["next"]
        assert [(i["text"], i["focus"]) for i in items] == [("Bravo", False), ("Alpha", True)]

    def test_star_is_dropped_when_the_action_leaves_next(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", self.STARRED)
        assert actions.move_action("next", "someday", self.STARRED).new_raw == A

    def test_star_cannot_be_set_outside_next(self, tasks_dir):
        seed(tasks_dir, "Someday_Maybe.md", A)
        assert actions.edit_action("someday", A, {"focus": True}).new_raw == A

    def test_sweeper_leaves_the_star_alone(self, tasks_dir):
        import asyncio
        seed(tasks_dir, "Next_Actions.md", self.STARRED, B)
        asyncio.run(maintain_tasks_dashboard())
        assert actions.read_action_lists()["lists"]["next"][0]["raw"] == self.STARRED

    def test_completing_does_not_carry_the_star_into_the_archive(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", self.STARRED)
        actions.complete_action("next", self.STARRED)
        archive = (tasks_dir / "z_Completed.md").read_text(encoding="utf-8")
        assert "~~Alpha @home #personal~~" in archive and "#focus" not in archive

    def test_non_boolean_is_rejected(self, tasks_dir):
        seed(tasks_dir, "Next_Actions.md", A)
        with pytest.raises(actions.OpSkipped):
            actions.edit_action("next", A, {"focus": "yes"})
