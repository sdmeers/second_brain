"""Natural-date parsing.

Regression cover for the missing `timedelta` import, which made every relative-date
branch of parse_natural_date_str raise NameError -- including its own fallback, so
/holiday and clarify's defer could not handle anything but an ISO date.
"""

from datetime import date, datetime, timedelta

import pytest

from backend.ingestion.handlers.clarify import parse_natural_date_str
from backend.utils.tasks_dashboard import parse_deadline
from backend.vault.tasks import parse_flexible_date

TODAY = datetime.now().date()


class TestRelativeDates:
    """Every one of these raised NameError before the import fix."""

    @pytest.mark.parametrize("text,days", [
        ("tomorrow", 1), ("+1d", 1), ("1d", 1),
        ("next week", 7), ("+7d", 7), ("7d", 7), ("week", 7),
        ("next month", 30), ("+30d", 30), ("30d", 30), ("month", 30),
        ("14d", 14), ("3d", 3),
    ])
    def test_relative_offsets(self, text, days):
        assert parse_natural_date_str(text) == TODAY + timedelta(days=days)

    def test_unparseable_input_falls_back_to_a_week(self):
        # The fallback `return today + timedelta(days=7)` itself used to raise.
        assert parse_natural_date_str("sometime whenever") == TODAY + timedelta(days=7)

    def test_case_and_whitespace_insensitive(self):
        assert parse_natural_date_str("  ToMoRRoW  ") == TODAY + timedelta(days=1)


class TestAbsoluteDates:
    def test_iso(self):
        assert parse_natural_date_str("2027-03-14") == date(2027, 3, 14)

    @pytest.mark.parametrize("text", ["23rd Aug 2027", "23 Aug 2027", "23 August 2027"])
    def test_day_month_year_with_ordinals(self, text):
        assert parse_natural_date_str(text) == date(2027, 8, 23)

    @pytest.mark.parametrize("text", ["Aug 23 2027", "August 23 2027"])
    def test_month_day_year(self, text):
        assert parse_natural_date_str(text) == date(2027, 8, 23)

    def test_slash_format_with_year(self):
        assert parse_natural_date_str("14/03/2027") == date(2027, 3, 14)

    def test_bare_day_month_rolls_forward_when_already_past(self):
        result = parse_natural_date_str("1 Jan")
        assert result >= TODAY, "a bare date already gone this year should roll to next year"

    def test_invalid_calendar_date_does_not_raise(self):
        assert parse_natural_date_str("31 Feb 2027") is not None


class TestParseFlexibleDate:
    @pytest.mark.parametrize("value,expected", [
        ("2026-09-01", date(2026, 9, 1)),
        ("01-09-2026", date(2026, 9, 1)),
        ('"2026-09-01"', date(2026, 9, 1)),
        (date(2026, 9, 1), date(2026, 9, 1)),
        (datetime(2026, 9, 1, 10, 30), date(2026, 9, 1)),
    ])
    def test_accepted_forms(self, value, expected):
        assert parse_flexible_date(value) == expected

    @pytest.mark.parametrize("value", ["", None, "not a date", "   "])
    def test_rejected_forms_return_none(self, value):
        assert parse_flexible_date(value) is None


class TestParseDeadline:
    def test_explicit_calendar_emoji_date(self):
        assert parse_deadline("- [ ] Thing 📅 2026-09-01") == date(2026, 9, 1)

    def test_due_prefix(self):
        assert parse_deadline("- [ ] Thing Due: 2026-09-01") == date(2026, 9, 1)

    def test_no_deadline_returns_none(self):
        assert parse_deadline("- [ ] Thing with no date") is None

    def test_relative_tomorrow_resolves_against_capture_date(self):
        line = "- [ ] Thing 📅 tomorrow (Captured from Telegram on 2026-08-01)"
        assert parse_deadline(line) == date(2026, 8, 2)

    def test_relative_today_resolves_against_capture_date(self):
        line = "- [ ] Thing 📅 today (Captured from Telegram on 2026-08-01)"
        assert parse_deadline(line) == date(2026, 8, 1)
