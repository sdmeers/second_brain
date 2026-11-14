"""Personal calendar feed (read-only).

Reads one or more secret iCal addresses and writes a daily note with that day's events
and the days ahead. No model is involved. The addresses are secrets: nothing here logs
them, including on failure.

Run as a module:  python3 -m backend.feeds.calendar sync [--dry-run]
"""

import asyncio
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend import config
from backend.feeds import notes, state
from backend.feeds.dryrun import RunReport

FEED = "calendar"
LOCAL_TZ = ZoneInfo("Europe/London")
BLOCK_START, BLOCK_END = "<!-- calendar:start -->", "<!-- calendar:end -->"
UPCOMING_HEADING = "## Coming up"
_BLOCK_RE = re.compile(re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END), re.DOTALL)
FINAL = "final:"      # prefix on the stored hash once a past day's note has been trimmed


class CalendarUnavailable(RuntimeError):
    """A calendar could not be fetched or read. The message never contains the address."""


@dataclass(frozen=True)
class Event:
    start: datetime          # local time; midnight for all-day events
    end: datetime            # exclusive
    all_day: bool
    title: str
    location: str = ""
    tentative: bool = False

    def covers(self, day: date) -> bool:
        day_start = datetime.combine(day, time.min, LOCAL_TZ)
        day_end = day_start + timedelta(days=1)
        if self.start == self.end:                      # zero-length event
            return day_start <= self.start < day_end
        return self.start < day_end and self.end > day_start


def _clean(value) -> str:
    """One line of plain text: event titles are untrusted and may contain anything."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.replace("<!--", "").replace("-->", "")[:200]


def _local(value) -> tuple[datetime, bool]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=LOCAL_TZ), False
        return value.astimezone(LOCAL_TZ), False
    return datetime.combine(value, time.min, LOCAL_TZ), True


def parse_events(ics: bytes, first_day: date, last_day: date) -> list[Event]:
    """Events (repeats expanded) that touch any day from ``first_day`` to ``last_day``."""
    import icalendar
    import recurring_ical_events

    try:
        cal = icalendar.Calendar.from_ical(ics)
        occurrences = recurring_ical_events.of(cal).between(first_day, last_day + timedelta(days=1))
    except Exception as e:
        raise CalendarUnavailable(f"calendar data could not be read ({type(e).__name__})") from None

    events = []
    for occ in occurrences:
        status = str(occ.get("STATUS", "")).upper()
        if status == "CANCELLED" or "DTSTART" not in occ:
            continue
        start, all_day = _local(occ.decoded("DTSTART"))
        if "DTEND" in occ:
            end, _ = _local(occ.decoded("DTEND"))
        elif "DURATION" in occ:
            end = start + occ.decoded("DURATION")
        else:
            end = start + timedelta(days=1) if all_day else start
        events.append(Event(start=start, end=end, all_day=all_day,
                            title=_clean(occ.get("SUMMARY")) or "(no title)",
                            location=_clean(occ.get("LOCATION")), tentative=status == "TENTATIVE"))
    return events


def events_on(events: list[Event], day: date) -> list[Event]:
    todays = {e for e in events if e.covers(day)}       # a set: two calendars may share an event
    return sorted(todays, key=lambda e: (not e.all_day, e.start, e.title))


def render_event(event: Event, day: date) -> str:
    day_start = datetime.combine(day, time.min, LOCAL_TZ)
    if event.all_day or (event.start <= day_start and event.end >= day_start + timedelta(days=1)):
        when = "All day"
    else:
        begins = event.start.strftime("%H:%M") if event.start >= day_start else "…"
        ends = event.end.strftime("%H:%M") if event.end <= day_start + timedelta(days=1) else "…"
        when = begins if event.start == event.end else f"{begins}–{ends}"
    line = f"- {when}: {event.title}"
    if event.location:
        line += f" ({event.location})"
    if event.tentative:
        line += " (tentative)"
    return line


def _day_name(day: date) -> str:
    return day.strftime("%A %-d %B")


def render_block(events: list[Event], today: date, lookahead: int, final: bool = False) -> str:
    lines = [BLOCK_START, f"## Events on {_day_name(today)}", ""]
    lines += [render_event(e, today) for e in events_on(events, today)] or ["- Nothing in the calendar."]
    if not final:
        lines += ["", f"{UPCOMING_HEADING} (as of {today.strftime('%-d %b')})"]
        for offset in range(1, lookahead + 1):
            day = today + timedelta(days=offset)
            days_events = events_on(events, day)
            lines += ["", f"### {_day_name(day)}", ""]
            lines += [render_event(e, day) for e in days_events] or ["- Nothing in the calendar."]
    lines.append(BLOCK_END)
    return "\n".join(lines)


def merge_block(existing_body: str, block: str) -> str:
    """Put the calendar block into a note body, replacing an earlier block if there is one
    and leaving everything else (hand-written notes) alone."""
    if _BLOCK_RE.search(existing_body):
        return _BLOCK_RE.sub(lambda _: block, existing_body, count=1)
    return f"{block}\n\n{existing_body}".strip() if existing_body else block


def trim_upcoming(body: str) -> str:
    """Drop the "coming up" list from a past day's calendar block."""
    def trim(m):
        block = m.group(0)
        cut = block.find(UPCOMING_HEADING)
        return block if cut == -1 else block[:cut].rstrip() + "\n" + BLOCK_END
    return _BLOCK_RE.sub(trim, body, count=1)


async def fetch_calendars(urls: list[str], http_client=None) -> list[bytes]:
    import httpx

    client = http_client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        bodies = []
        for n, url in enumerate(urls, 1):
            try:
                res = await client.get(url)
            except Exception as e:      # httpx puts the URL in its messages: report the type only
                raise CalendarUnavailable(f"calendar {n} could not be fetched ({type(e).__name__})") from None
            if res.status_code != 200:
                raise CalendarUnavailable(f"calendar {n} returned HTTP {res.status_code}")
            bodies.append(res.content)
        return bodies
    finally:
        if http_client is None:
            await client.aclose()


def _write_day(day: date, body: str, report: RunReport, dry_run: bool, source_hash: str | None = None):
    return notes.write_feed_note(
        feed=FEED, source_id=day.isoformat(), note_type="daily", title=f"{day.isoformat()} Daily",
        body=body, dest_dir=os.path.join(config.JOURNAL_DIR, str(day.year)), original_date=day,
        source_hash=source_hash, report=report, dry_run=dry_run)


def finalise_past_days(today: date, report: RunReport, dry_run: bool) -> int:
    """Trim the look-ahead from earlier daily notes so only each day's own events remain."""
    trimmed = 0
    for row in state.list_seen(FEED):
        if row["source_id"] >= today.isoformat() or row["content_hash"].startswith(FINAL):
            continue
        body = notes.existing_body(row["note_path"])
        new_body = trim_upcoming(body)
        if not body or dry_run:
            continue
        if new_body != body:
            _write_day(date.fromisoformat(row["source_id"]), new_body, report, dry_run,
                       source_hash=FINAL + notes.content_hash(new_body))
            trimmed += 1
        else:
            state.mark_seen(FEED, row["source_id"], FINAL + row["content_hash"],
                            note_path=row["note_path"], original_date=row["original_date"])
    return trimmed


async def sync(today: date | None = None, dry_run: bool = False, http_client=None) -> RunReport:
    """Write today's daily note from the calendar. Safe to run any number of times."""
    report = RunReport(FEED, dry_run=dry_run)
    if not config.ENABLE_CALENDAR_FEED and not dry_run:
        print("Backend [Calendar]: feed disabled; nothing done.")
        return report
    if not config.GCAL_ICAL_URLS:
        raise CalendarUnavailable("no calendar address is configured (GCAL_ICAL_URL)")

    today = today or datetime.now(LOCAL_TZ).date()
    last_day = today + timedelta(days=config.CALENDAR_LOOKAHEAD_DAYS)
    events: list[Event] = []
    for ics in await fetch_calendars(config.GCAL_ICAL_URLS, http_client):
        events += parse_events(ics, today, last_day)

    finalise_past_days(today, report, dry_run)

    block = render_block(events, today, config.CALENDAR_LOOKAHEAD_DAYS)
    seen = state.get_seen(FEED, today.isoformat())
    body = merge_block(notes.existing_body(seen["note_path"] if seen else None), block)
    _write_day(today, body, report, dry_run)
    return report


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] != "sync" or set(args[1:]) - {"--dry-run"}:
        sys.exit("usage: python3 -m backend.feeds.calendar sync [--dry-run]")
    try:
        print(asyncio.run(sync(dry_run="--dry-run" in args)).text())
    except CalendarUnavailable as e:
        sys.exit(f"Backend [Calendar]: {e}")
