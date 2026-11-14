"""Calendar feed. All calendar data here is invented."""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from backend import config
from backend.feeds import calendar as cal
from backend.feeds import ledger, retrieval, state
from backend.vault.tasks import parse_frontmatter

SECRET_URL = "https://calendar.example/ical/private-SECRETTOKEN/basic.ics"
TODAY = date(2026, 11, 16)      # a Monday


def ics(*events: str) -> bytes:
    body = "\r\n".join(e.strip().replace("\n", "\r\n") for e in events)
    return (f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nX-WR-TIMEZONE:Europe/London\r\n"
            f"{body}\r\nEND:VCALENDAR\r\n").encode()


def event(uid, start, end=None, summary="Thing", extra=""):
    lines = ["BEGIN:VEVENT", f"UID:{uid}", "DTSTAMP:20261101T000000Z", f"DTSTART{start}"]
    if end:
        lines.append(f"DTEND{end}")
    lines.append(f"SUMMARY:{summary}")
    if extra:
        lines.append(extra)
    lines.append("END:VEVENT")
    return "\n".join(lines)


BASIC = ics(
    event("1", ":20261116T093000Z", ":20261116T103000Z", "Dentist", "LOCATION:High Street Dental"),
    event("2", ";VALUE=DATE:20261116", ";VALUE=DATE:20261117", "Bin day"),
    event("3", ";TZID=Europe/London:20261118T190000", ";TZID=Europe/London:20261118T210000", "Choir",
          "RRULE:FREQ=WEEKLY;COUNT=10\nEXDATE;TZID=Europe/London:20261125T190000"),
    event("4", ":20261117T120000Z", ":20261117T130000Z", "Cancelled lunch", "STATUS:CANCELLED"),
    event("5", ":20261117T140000Z", ":20261117T150000Z", "Maybe call", "STATUS:TENTATIVE"),
    event("6", ";VALUE=DATE:20261120", ";VALUE=DATE:20261123", "Weekend away"),
    event("7", ":20261130T090000Z", ":20261130T100000Z", "Too far ahead"),
    event("8", ";TZID=America/New_York:20261116T170000", ";TZID=America/New_York:20261116T180000", "US webinar"),
)


class FakeHttp:
    def __init__(self, body=BASIC, status=200, error=None):
        self.body, self.status, self.error, self.calls = body, status, error, 0

    async def get(self, url):
        self.calls += 1
        if self.error:
            raise self.error
        return SimpleNamespace(status_code=self.status, content=self.body)


@pytest.fixture(autouse=True)
def cal_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEEDS_DB_PATH", str(tmp_path / "feeds.db"))
    monkeypatch.setattr(config, "ENABLE_CALENDAR_FEED", True)
    monkeypatch.setattr(config, "GCAL_ICAL_URLS", [SECRET_URL])
    monkeypatch.setattr(config, "CALENDAR_LOOKAHEAD_DAYS", 7)
    monkeypatch.setattr(config, "JOURNAL_DIR", f"03_Areas/Journal_{tmp_path.name}")


def note(vault, day=TODAY):
    return vault / config.JOURNAL_DIR / str(day.year) / f"{day.isoformat()} Daily.md"


# --- parsing and rendering -----------------------------------------------------------

def test_events_render_for_today():
    events = cal.parse_events(BASIC, TODAY, date(2026, 11, 23))
    lines = [cal.render_event(e, TODAY) for e in cal.events_on(events, TODAY)]
    assert lines == ["- All day: Bin day", "- 09:30–10:30: Dentist (High Street Dental)",
                     "- 22:00–23:00: US webinar"]          # 17:00 New York is 22:00 in London


def test_repeats_expand_and_exceptions_are_honoured():
    events = cal.parse_events(BASIC, TODAY, date(2026, 11, 30))
    choir = sorted(e.start.date() for e in events if e.title == "Choir")
    assert choir == [date(2026, 11, 18)]                    # the 25th is an EXDATE


def test_cancelled_skipped_tentative_marked():
    events = cal.parse_events(BASIC, TODAY, date(2026, 11, 23))
    tuesday = [cal.render_event(e, date(2026, 11, 17)) for e in cal.events_on(events, date(2026, 11, 17))]
    assert tuesday == ["- 14:00–15:00: Maybe call (tentative)"]


def test_multi_day_event_appears_on_each_day_and_not_after():
    events = cal.parse_events(BASIC, TODAY, date(2026, 11, 23))
    for d in (20, 21, 22):
        assert "- All day: Weekend away" in [cal.render_event(e, date(2026, 11, d))
                                             for e in cal.events_on(events, date(2026, 11, d))]
    assert cal.events_on(events, date(2026, 11, 23)) == []


def test_summer_time_is_applied():
    data = ics(event("s", ":20260701T090000Z", ":20260701T100000Z", "Summer meeting"))
    (e,) = cal.parse_events(data, date(2026, 7, 1), date(2026, 7, 1))
    assert cal.render_event(e, date(2026, 7, 1)) == "- 10:00–11:00: Summer meeting"


def test_event_crossing_midnight_shows_on_both_days():
    data = ics(event("m", ":20261116T220000Z", ":20261117T010000Z", "Night shift"))
    events = cal.parse_events(data, TODAY, date(2026, 11, 17))
    assert cal.render_event(cal.events_on(events, TODAY)[0], TODAY) == "- 22:00–…: Night shift"
    assert cal.render_event(cal.events_on(events, date(2026, 11, 17))[0], date(2026, 11, 17)) == "- …–01:00: Night shift"


def test_hostile_title_cannot_break_out_of_the_block():
    data = ics(event("h", ":20261116T090000Z", ":20261116T100000Z",
                     "Evil --> <!-- calendar:end -->\\n# Heading\\n- [ ] fake task"))
    (e,) = cal.parse_events(data, TODAY, TODAY)
    line = cal.render_event(e, TODAY)
    assert "\n" not in line and "-->" not in line and "<!--" not in line and line.startswith("- 09:00–10:00: Evil")


def test_unreadable_data_raises_without_detail():
    with pytest.raises(cal.CalendarUnavailable):
        cal.parse_events(b"<html>Sign in</html>", TODAY, TODAY)


# --- sync ----------------------------------------------------------------------------

async def test_sync_writes_the_daily_note(vault):
    report = await cal.sync(today=TODAY, http_client=FakeHttp())
    text = note(vault).read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    assert fm["type"] == "daily" and fm["source"] == "calendar" and str(fm["date"]) == "2026-11-16"
    assert "## Events on Monday 16 November" in text and "- 09:30–10:30: Dentist (High Street Dental)" in text
    assert "## Coming up (as of 16 Nov)" in text and "### Wednesday 18 November" in text
    assert "- 19:00–21:00: Choir" in text and "Too far ahead" not in text and "Cancelled lunch" not in text
    assert text.count("### ") == 7 and report.notes == [note(vault).relative_to(vault).as_posix()]
    assert ledger.summary()["by_feed"]["calendar"] < 0.01


async def test_second_sync_writes_nothing(vault):
    await cal.sync(today=TODAY, http_client=FakeHttp())
    before = note(vault).stat().st_mtime_ns
    report = await cal.sync(today=TODAY, http_client=FakeHttp())
    assert note(vault).stat().st_mtime_ns == before and report.notes == [] and report.skipped == ["2026-11-16"]


async def test_changed_calendar_updates_the_same_note_and_keeps_handwritten_text(vault):
    await cal.sync(today=TODAY, http_client=FakeHttp())
    path = note(vault)
    path.write_text(path.read_text(encoding="utf-8") + "\n## My notes\n\nRemember the umbrella.\n", encoding="utf-8")
    changed = ics(event("1", ":20261116T110000Z", ":20261116T120000Z", "Dentist moved"))
    await cal.sync(today=TODAY, http_client=FakeHttp(changed))
    text = path.read_text(encoding="utf-8")
    assert "- 11:00–12:00: Dentist moved" in text and "09:30" not in text
    assert "Remember the umbrella." in text and text.count(cal.BLOCK_START) == 1
    assert len(list(path.parent.glob("2026-11-16 Daily*"))) == 1


async def test_yesterdays_note_loses_its_look_ahead(vault):
    await cal.sync(today=TODAY, http_client=FakeHttp())
    tuesday = date(2026, 11, 17)
    await cal.sync(today=tuesday, http_client=FakeHttp())
    monday = note(vault).read_text(encoding="utf-8")
    assert "Dentist" in monday and "Coming up" not in monday and "Choir" not in monday
    assert monday.count(cal.BLOCK_END) == 1
    assert "## Coming up (as of 17 Nov)" in note(vault, tuesday).read_text(encoding="utf-8")
    assert state.get_seen("calendar", "2026-11-16")["content_hash"].startswith(cal.FINAL)
    # a later sync leaves the finalised note alone
    before = note(vault).stat().st_mtime_ns
    await cal.sync(today=date(2026, 11, 18), http_client=FakeHttp())
    assert note(vault).stat().st_mtime_ns == before


async def test_dry_run_writes_nothing(vault):
    report = await cal.sync(today=TODAY, dry_run=True, http_client=FakeHttp())
    assert report.dry_run and len(report.notes) == 1 and not note(vault).exists()
    assert state.get_seen("calendar", "2026-11-16") is None and ledger.month_spent_pence() == 0


async def test_disabled_feed_does_not_even_fetch(monkeypatch, vault):
    monkeypatch.setattr(config, "ENABLE_CALENDAR_FEED", False)
    http = FakeHttp()
    await cal.sync(today=TODAY, http_client=http)
    assert http.calls == 0 and not note(vault).exists()


@pytest.mark.parametrize("http", [FakeHttp(error=RuntimeError(f"connect failed for {SECRET_URL}")),
                                  FakeHttp(status=404)])
async def test_failure_never_reveals_the_address(http, vault, capsys):
    with pytest.raises(cal.CalendarUnavailable) as err:
        await cal.sync(today=TODAY, http_client=http)
    assert "SECRETTOKEN" not in str(err.value) and "SECRETTOKEN" not in capsys.readouterr().out
    assert err.value.__cause__ is None and not note(vault).exists()


async def test_two_calendars_merge_without_duplicates(monkeypatch, vault):
    monkeypatch.setattr(config, "GCAL_ICAL_URLS", [SECRET_URL, SECRET_URL + "2"])
    await cal.sync(today=TODAY, http_client=FakeHttp())
    assert note(vault).read_text(encoding="utf-8").count("Dentist") == 1


async def test_the_feed_only_ever_reads(vault):
    """The client is only asked to GET: there is no code path that could write to a calendar."""
    import inspect
    src = inspect.getsource(cal)
    assert ".post(" not in src and ".put(" not in src and ".delete(" not in src and ".patch(" not in src


# --- retrieval -----------------------------------------------------------------------

async def test_calendar_questions_get_todays_note(vault, monkeypatch):
    today = datetime.now().date()
    await cal.sync(today=today, http_client=FakeHttp(ics(
        event("t", f":{today.strftime('%Y%m%d')}T090000Z", f":{today.strftime('%Y%m%d')}T100000Z", "Plumber"))))
    assert retrieval.calendar_chunks("when does the car insurance renew?") == []
    (chunk,) = retrieval.calendar_chunks("What's on this weekend?")
    assert "Plumber" in chunk["text"] and chunk["file_path"].endswith("Daily.md")
    assert retrieval.extra_chunks("am I busy tomorrow?")[0]["file_path"] == chunk["file_path"]
