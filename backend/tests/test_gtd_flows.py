"""GTD file maintenance and project resolution, exercised against a real temp vault."""

import os
from datetime import date

from backend.utils.sessions import SessionStore
from backend.utils.tasks_dashboard import append_gtd_item, ensure_category_tag, maintain_tasks_dashboard
from backend.vault.tasks import (
    append_project_step,
    build_project_note,
    ensure_project_note,
    find_project_note,
    iter_task_lines,
    parse_frontmatter,
    read_project,
    remove_step_from_project,
)


class TestAppendGtdItem:
    def test_adds_item_with_header(self, tasks_dir):
        path = str(tasks_dir / "Next_Actions.md")
        append_gtd_item(path, "- [ ] Buy milk @errands")
        content = open(path, encoding="utf-8").read()
        assert content.startswith("# ⚡ Next Actions")
        assert "Buy milk" in content

    def test_preserves_earlier_items(self, tasks_dir):
        path = str(tasks_dir / "Next_Actions.md")
        append_gtd_item(path, "- [ ] First @home")
        append_gtd_item(path, "- [ ] Second @home")
        content = open(path, encoding="utf-8").read()
        assert "First" in content and "Second" in content

    def test_drops_the_all_clear_placeholder(self, tasks_dir):
        path = str(tasks_dir / "Next_Actions.md")
        open(path, "w", encoding="utf-8").write(
            "# ⚡ Next Actions\n\n🎉 *All clear! No active next actions remaining.*\n")
        append_gtd_item(path, "- [ ] Real task @home")
        content = open(path, encoding="utf-8").read()
        assert "All clear" not in content
        assert "Real task" in content

    def test_next_actions_items_get_a_category_tag(self, tasks_dir):
        path = str(tasks_dir / "Next_Actions.md")
        append_gtd_item(path, "- [ ] Untagged task @home")
        line = next(ln for ln in open(path, encoding="utf-8") if "Untagged" in ln)
        assert "#personal" in line or "#professional" in line


class TestEnsureCategoryTag:
    def test_adds_personal_by_default(self):
        assert "#personal" in ensure_category_tag("- [ ] Task @home")

    def test_respects_an_existing_tag(self):
        line = "- [ ] Task @office #professional"
        assert ensure_category_tag(line) == line

    def test_explicit_category_wins(self):
        assert "#professional" in ensure_category_tag("- [ ] Task", category="Professional")

    def test_tag_is_placed_before_the_captured_suffix(self):
        out = ensure_category_tag("- [ ] Task (Captured on 2026-08-01)")
        assert out.index("#personal") < out.index("(Captured")


class TestMaintainTasksDashboard:
    async def test_moves_completed_items_to_the_archive(self, tasks_dir):
        (tasks_dir / "Next_Actions.md").write_text(
            "# ⚡ Next Actions\n\n- [ ] Still open @home #personal\n"
            "- [x] Already done @home #personal\n", encoding="utf-8")
        await maintain_tasks_dashboard()

        remaining = (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8")
        archive = (tasks_dir / "z_Completed.md").read_text(encoding="utf-8")
        assert "Still open" in remaining
        assert "Already done" not in remaining
        assert "Already done" in archive

    async def test_splits_personal_and_professional(self, tasks_dir):
        (tasks_dir / "Next_Actions.md").write_text(
            "# ⚡ Next Actions\n\n- [ ] Home task @home #personal\n"
            "- [ ] Work task @office #professional\n", encoding="utf-8")
        await maintain_tasks_dashboard()
        content = (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8")
        assert "## 🏠 Personal" in content and "## 💼 Professional" in content
        assert content.index("Home task") < content.index("## 💼 Professional")
        assert content.index("Work task") > content.index("## 💼 Professional")

    async def test_keeps_manual_order_regardless_of_due_dates(self, tasks_dir):
        """File order is the priority order set in the actions app; the sweeper used to
        re-sort overdue items to the top every 15 seconds, undoing any drag."""
        (tasks_dir / "Next_Actions.md").write_text(
            "# ⚡ Next Actions\n\n"
            "- [ ] No deadline @home #personal\n"
            "- [ ] Overdue thing @home #personal 📅 2020-01-01\n", encoding="utf-8")
        await maintain_tasks_dashboard()
        content = (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8")
        assert content.index("No deadline") < content.index("Overdue thing")

    async def test_still_resolves_relative_due_markers(self, tasks_dir):
        (tasks_dir / "Next_Actions.md").write_text(
            "# ⚡ Next Actions\n\n- [ ] Ring the bank @phone #personal 📅 today\n", encoding="utf-8")
        await maintain_tasks_dashboard()
        content = (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8")
        assert "📅 today" not in content
        assert f"📅 {date.today().isoformat()}" in content

    async def test_empty_list_gets_the_all_clear_message(self, tasks_dir):
        (tasks_dir / "Next_Actions.md").write_text("# ⚡ Next Actions\n\n", encoding="utf-8")
        await maintain_tasks_dashboard()
        assert "All clear" in (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8")

    async def test_is_idempotent(self, tasks_dir):
        (tasks_dir / "Next_Actions.md").write_text(
            "# ⚡ Next Actions\n\n- [ ] A task @home #personal\n", encoding="utf-8")
        await maintain_tasks_dashboard()
        once = (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8")
        await maintain_tasks_dashboard()
        assert (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8") == once

    async def test_does_not_lose_tasks_whose_text_has_brackets(self, tasks_dir):
        """Regression for the .strip("- [ ]") corruption."""
        (tasks_dir / "Next_Actions.md").write_text(
            "# ⚡ Next Actions\n\n- [ ] [Draft] review the spec @computer #personal\n",
            encoding="utf-8")
        await maintain_tasks_dashboard()
        assert "[Draft] review the spec" in (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8")


class TestProjectNotes:
    def test_build_project_note_has_the_expected_sections(self):
        note = build_project_note("Garage", "Personal", "Clear the garage")
        for section in ("# Garage", "## Desired Outcome", "## Project Steps",
                        "## Active Actions", "```dataview", "## Reference Material"):
            assert section in note
        fm = parse_frontmatter(note)
        assert fm["title"] == "Garage"
        assert fm["category"] == "Personal"
        assert fm["status"] == "active"

    def test_project_tag_uses_underscores(self):
        assert "#project/Second_Brain" in build_project_note("Second Brain")

    def test_ensure_creates_then_finds(self, vault):
        path = ensure_project_note("Test Widget", category="Personal")
        assert os.path.exists(path)
        assert find_project_note("Test Widget") == path
        # second call must not create a duplicate
        assert ensure_project_note("Test Widget") == path

    def test_find_is_tolerant_of_underscores_and_case(self, vault):
        ensure_project_note("Bathroom Refit", category="Personal")
        for variant in ("Bathroom Refit", "bathroom refit", "Bathroom_Refit"):
            assert find_project_note(variant) is not None, variant

    def test_find_returns_none_when_absent(self):
        assert find_project_note("No Such Project Anywhere") is None

    def test_read_project_exposes_frontmatter_and_steps(self, vault):
        path = ensure_project_note("Roadmap Test", category="Professional")
        append_project_step("Roadmap Test", "First step")
        append_project_step("Roadmap Test", "Second step")
        project = read_project(path)
        assert project.name == "Roadmap Test"
        assert project.status == "active"
        assert set(project.steps) == {"First step", "Second step"}

    def test_remove_step(self, vault):
        path = ensure_project_note("Removal Test", category="Personal")
        append_project_step("Removal Test", "Keep me")
        append_project_step("Removal Test", "Delete me")
        assert remove_step_from_project(path, "Delete me") is True
        steps = read_project(path).steps
        assert "Keep me" in steps and "Delete me" not in steps

    def test_remove_step_that_is_absent_returns_false(self, vault):
        path = ensure_project_note("Removal Test 2", category="Personal")
        append_project_step("Removal Test 2", "Only step")
        assert remove_step_from_project(path, "Nonexistent step") is False

    def test_project_name_with_a_colon_stays_valid_yaml(self, vault):
        path = ensure_project_note("Phase 1: Discovery", category="Personal")
        project = read_project(path)
        assert project is not None
        assert project.frontmatter["title"] == "Phase 1: Discovery"


class TestIterTaskLines:
    def test_yields_only_task_lines(self, tasks_dir):
        (tasks_dir / "Next_Actions.md").write_text(
            "# Heading\n\n> A quote\n- [ ] Real task\n- [x] Done task\nplain text\n",
            encoding="utf-8")
        tasks = list(iter_task_lines(str(tasks_dir / "Next_Actions.md")))
        assert len(tasks) == 2

    def test_can_exclude_completed(self, tasks_dir):
        (tasks_dir / "Next_Actions.md").write_text(
            "- [ ] Open\n- [x] Closed\n", encoding="utf-8")
        tasks = list(iter_task_lines(str(tasks_dir / "Next_Actions.md"), include_checked=False))
        assert len(tasks) == 1 and tasks[0].description == "Open"

    def test_missing_file_yields_nothing(self, tasks_dir):
        assert list(iter_task_lines(str(tasks_dir / "Nope.md"))) == []


class TestSessionStore:
    def test_set_and_get(self):
        s = SessionStore("t")
        s["a"] = 1
        assert s.get("a") == 1 and "a" in s

    def test_missing_key(self):
        s = SessionStore("t")
        assert s.get("nope") is None
        assert s.get("nope", "fallback") == "fallback"
        assert "nope" not in s

    def test_delete(self):
        s = SessionStore("t")
        s["a"] = 1
        del s["a"]
        assert "a" not in s

    def test_pop(self):
        s = SessionStore("t")
        s["a"] = 1
        assert s.pop("a") == 1
        assert s.pop("a", "gone") == "gone"

    def test_entries_expire(self):
        s = SessionStore("t", ttl=0)
        s["a"] = 1
        assert s.get("a") is None, "a zero TTL entry must be treated as expired"

    def test_unexpired_entries_survive(self):
        s = SessionStore("t", ttl=3600)
        s["a"] = 1
        assert s.get("a") == 1

    def test_len_reflects_live_entries(self):
        s = SessionStore("t", ttl=3600)
        s["a"], s["b"] = 1, 2
        assert len(s) == 2


class TestReflectTicklers:
    """The reflection hub has always been labelled 'Deadlines & Ticklers', but only
    project due_dates were counted, so a deferred task appeared nowhere."""

    def _summary(self):
        from backend.ingestion.handlers.engage import get_reflect_summary_data
        return get_reflect_summary_data()

    def test_upcoming_tickler_is_reported(self, tasks_dir):
        from datetime import datetime, timedelta
        soon = (datetime.now().date() + timedelta(days=5)).strftime("%Y-%m-%d")
        (tasks_dir / "Next_Actions.md").write_text(
            f"# ⚡ Next Actions\n\n- [ ] Buy anniversary card @errands #personal 🔔 {soon}\n",
            encoding="utf-8")
        ticklers = self._summary()["upcoming_ticklers"]
        assert len(ticklers) == 1
        desc, days_until, disp = ticklers[0]
        assert desc == "Buy anniversary card", "display form should strip tags and the 🔔 stamp"
        assert days_until == 5

    def test_tickler_due_today_is_included(self, tasks_dir):
        from datetime import datetime
        today = datetime.now().date().strftime("%Y-%m-%d")
        (tasks_dir / "Next_Actions.md").write_text(
            f"# ⚡ Next Actions\n\n- [ ] Surfacing now @home #personal 🔔 {today}\n", encoding="utf-8")
        assert self._summary()["upcoming_ticklers"][0][1] == 0

    def test_tickler_beyond_14_days_is_excluded(self, tasks_dir):
        from datetime import datetime, timedelta
        far = (datetime.now().date() + timedelta(days=40)).strftime("%Y-%m-%d")
        (tasks_dir / "Next_Actions.md").write_text(
            f"# ⚡ Next Actions\n\n- [ ] Far future @home #personal 🔔 {far}\n", encoding="utf-8")
        assert self._summary()["upcoming_ticklers"] == []

    def test_past_tickler_is_excluded(self, tasks_dir):
        from datetime import datetime, timedelta
        past = (datetime.now().date() - timedelta(days=3)).strftime("%Y-%m-%d")
        (tasks_dir / "Next_Actions.md").write_text(
            f"# ⚡ Next Actions\n\n- [ ] Already surfaced @home #personal 🔔 {past}\n", encoding="utf-8")
        assert self._summary()["upcoming_ticklers"] == []

    def test_ticklers_are_sorted_soonest_first(self, tasks_dir):
        from datetime import datetime, timedelta
        d = datetime.now().date()
        (tasks_dir / "Next_Actions.md").write_text(
            f"# ⚡ Next Actions\n"
            f"- [ ] Later @home #personal 🔔 {(d + timedelta(days=9)).strftime('%Y-%m-%d')}\n"
            f"- [ ] Sooner @home #personal 🔔 {(d + timedelta(days=2)).strftime('%Y-%m-%d')}\n",
            encoding="utf-8")
        got = [t[0] for t in self._summary()["upcoming_ticklers"]]
        assert got == ["Sooner", "Later"]

    def test_hub_reports_both_counts(self, tasks_dir):
        from datetime import datetime, timedelta
        from backend.ingestion.handlers.engage import _build_reflect_hub
        soon = (datetime.now().date() + timedelta(days=3)).strftime("%Y-%m-%d")
        (tasks_dir / "Next_Actions.md").write_text(
            f"# ⚡ Next Actions\n\n- [ ] Deferred thing @home #personal 🔔 {soon}\n", encoding="utf-8")
        text, _ = _build_reflect_hub(self._summary())
        assert "1 tickler(s) due" in text
