"""Task-line parsing and rendering.

These cover the conventions documented in docs/technical_implementation.md §2, which
were previously re-implemented by ad-hoc regexes in five modules.
"""

from datetime import date, datetime, timedelta

import pytest

from backend.vault.tasks import (
    Task,
    format_completed_line,
    parse_task_line,
    render_task_line,
    strip_task_metadata,
)

FULL_LINE = (
    "- [ ] Call the plumber @phone #project/Bathroom_Refit #personal "
    "📅 2026-09-01 🔔 2026-08-28 (Source: [[00_System/Inbox/Archive/note.md]])"
)


class TestParseTaskLine:
    def test_parses_every_documented_field(self):
        t = parse_task_line(FULL_LINE)
        assert t is not None
        assert t.checked is False
        assert t.description == "Call the plumber @phone #project/Bathroom_Refit #personal 📅 2026-09-01 🔔 2026-08-28"
        assert t.contexts == ["@phone"]
        assert t.project == "Bathroom_Refit"
        assert t.category == "personal"
        assert t.due == date(2026, 9, 1)
        assert t.tickler == date(2026, 8, 28)
        assert t.source == "00_System/Inbox/Archive/note.md"

    def test_raw_is_preserved_verbatim(self):
        assert parse_task_line(FULL_LINE).raw == FULL_LINE

    @pytest.mark.parametrize("mark,expected", [
        (" ", False), ("x", True), ("X", True), ("v", True), ("V", True),
    ])
    def test_checkbox_states(self, mark, expected):
        assert parse_task_line(f"- [{mark}] something").checked is expected

    @pytest.mark.parametrize("line", [
        "", "   ", "not a task", "# Heading", "- plain bullet, no checkbox",
        "> quote", "*None*",
    ])
    def test_non_task_lines_return_none(self, line):
        assert parse_task_line(line) is None

    def test_leading_indentation_is_tolerated(self):
        assert parse_task_line("    - [ ] indented step") is not None

    def test_waiting_owner(self):
        t = parse_task_line("- [ ] Chase invoice #waiting(Alex) #professional")
        assert t.waiting_on == "Alex"
        assert t.category == "professional"

    def test_multiple_contexts(self):
        t = parse_task_line("- [ ] Do thing @home @computer")
        assert t.contexts == ["@home", "@computer"]

    def test_email_is_not_mistaken_for_a_context(self):
        # '@' preceded by a non-space must not be captured
        t = parse_task_line("- [ ] Email steven@example.com about the quote")
        assert t.contexts == []

    def test_missing_optional_fields_are_none(self):
        t = parse_task_line("- [ ] Bare task")
        assert (t.project, t.category, t.due, t.tickler, t.source) == (None, None, None, None, None)

    def test_malformed_date_does_not_raise(self):
        t = parse_task_line("- [ ] Thing 📅 2026-13-45")
        assert t is not None and t.due is None


class TestIsDeferred:
    def test_future_tickler_defers(self):
        future = (datetime.now().date() + timedelta(days=3)).strftime("%Y-%m-%d")
        assert parse_task_line(f"- [ ] Later 🔔 {future}").is_deferred is True

    def test_past_tickler_does_not_defer(self):
        past = (datetime.now().date() - timedelta(days=3)).strftime("%Y-%m-%d")
        assert parse_task_line(f"- [ ] Now 🔔 {past}").is_deferred is False

    def test_today_tickler_surfaces(self):
        today = datetime.now().date().strftime("%Y-%m-%d")
        assert parse_task_line(f"- [ ] Today 🔔 {today}").is_deferred is False

    def test_no_tickler_is_not_deferred(self):
        assert parse_task_line("- [ ] Anytime").is_deferred is False


class TestStripTaskMetadata:
    """Regression tests for the .strip("- [ ]") corruption bug.

    str.strip takes a character *set*, so the old idiom ate leading/trailing '[', ']',
    '-' and ' ' from real task text.
    """

    def test_bracketed_prefix_survives(self):
        assert strip_task_metadata("- [ ] [Draft] review notes") == "[Draft] review notes"

    def test_trailing_bracket_survives(self):
        assert strip_task_metadata("- [ ] Review the spec [v2]") == "Review the spec [v2]"

    def test_leading_hyphen_in_text_survives(self):
        assert strip_task_metadata("- [ ] -1 degree calibration") == "-1 degree calibration"

    def test_strips_captured_suffix(self):
        line = "- [ ] Buy milk (Captured on 2026-08-01 12:00 via Telegram)"
        assert strip_task_metadata(line) == "Buy milk"

    def test_strips_source_link(self):
        line = "- [ ] Buy milk (Source: [[00_System/Inbox/x.md]])"
        assert strip_task_metadata(line) == "Buy milk"

    def test_unwraps_strikethrough_and_completed_marker(self):
        line = "- [x] ~~Buy milk~~ (Completed on 2026-08-01)"
        assert strip_task_metadata(line) == "Buy milk"

    def test_plain_bullet(self):
        assert strip_task_metadata("- just a capture") == "just a capture"

    def test_idempotent(self):
        once = strip_task_metadata("- [ ] Buy milk (Captured on 2026-08-01)")
        assert strip_task_metadata(once) == once


class TestRenderTaskLine:
    def test_field_order_matches_the_documented_schema(self):
        line = render_task_line(
            "Call the plumber", contexts=["@phone"], project="Bathroom Refit",
            category="Personal", due=date(2026, 9, 1), tickler=date(2026, 8, 28),
            source="note.md",
        )
        assert line == (
            "- [ ] Call the plumber @phone #project/Bathroom_Refit #personal "
            "📅 2026-09-01 🔔 2026-08-28 (Source: [[note.md]])"
        )

    def test_minimal_line(self):
        assert render_task_line("Do the thing") == "- [ ] Do the thing"

    def test_checked_line(self):
        assert render_task_line("Done thing", checked=True) == "- [x] Done thing"

    def test_spaces_in_project_become_underscores(self):
        assert "#project/Second_Brain" in render_task_line("x", project="Second Brain")

    def test_round_trip_through_parse(self):
        original = render_task_line(
            "Round trip", contexts=["@home"], project="Garage",
            category="personal", due=date(2026, 12, 25),
        )
        t = parse_task_line(original)
        assert t.project == "Garage"
        assert t.category == "personal"
        assert t.due == date(2026, 12, 25)
        assert t.contexts == ["@home"]


class TestFormatCompletedLine:
    def test_produces_the_format_status_and_reflect_parse(self):
        out = format_completed_line("- [ ] Buy milk", when=date(2026, 8, 27))
        assert out == "- [x] ~~Buy milk~~ (Completed on 2026-08-27)"
        # and the result must parse back as a completion on that date
        t = parse_task_line(out)
        assert t.checked is True
        assert t.completed_on == date(2026, 8, 27)

    def test_already_completed_line_is_left_alone(self):
        existing = "- [x] ~~Old thing~~ (Completed on 2026-01-01)"
        assert format_completed_line(existing, when=date(2026, 8, 27)) == existing

    def test_strips_metadata_before_archiving(self):
        line = "- [ ] Buy milk @errands #personal (Source: [[x.md]])"
        out = format_completed_line(line, when=date(2026, 8, 27))
        assert "Source:" not in out
        assert "Buy milk" in out


class TestTaskDataclass:
    def test_constructible_directly(self):
        t = Task(raw="- [ ] x", description="x")
        assert t.checked is False and t.contexts == []
