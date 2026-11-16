"""Daily voice journal: the feed core and its Telegram adapter. Synthetic content only."""

import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from backend import config
from backend.feeds import journal, journal_telegram, ledger, state
from backend.vault.tasks import parse_frontmatter

WHEN = datetime(2026, 11, 14, 22, 14)
TRANSCRIPT = "Went for a run. I told Rosie I'd send her the form. Thinking about the boiler."
EXTRACT = {"summary": "I went for a run and had a quiet day.",
           "commitments": ["Send Rosie the form"], "on_my_mind": ["The boiler"]}


@pytest.fixture(autouse=True)
def journal_env(tmp_path, monkeypatch, vault):
    monkeypatch.setattr(config, "FEEDS_DB_PATH", str(tmp_path / "feeds.db"))
    monkeypatch.setattr(config, "FEEDS_MONTHLY_BUDGET_PENCE", 300.0)
    monkeypatch.setattr(config, "FEEDS_INDEXING_RESERVE_PENCE", 10.0)
    monkeypatch.setattr(config, "ENABLE_JOURNAL_FEED", True)
    monkeypatch.setattr(config, "JOURNAL_DIR", f"03_Areas/Journal_{tmp_path.name}")
    monkeypatch.setattr(config, "FEEDS_RAW_PATH", str(tmp_path / "raw"))
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_CHAT_IDS", [42])
    journal_telegram._pending.pop(42)


@pytest.fixture
def model(monkeypatch):
    """Fakes the summariser and the transcriber."""
    m = SimpleNamespace(reply=json.dumps(EXTRACT), sent=[], transcribed=[], transcript=TRANSCRIPT)

    async def fake_generate(model, contents, config=None, max_retries=5):
        m.sent.append(contents)
        return SimpleNamespace(text=m.reply, usage_metadata=SimpleNamespace(
            prompt_token_count=400, candidates_token_count=100, thoughts_token_count=0))

    async def fake_transcribe(data, mime_type):
        m.transcribed.append((data, mime_type))
        return m.transcript

    monkeypatch.setattr("backend.utils.gemini.call_gemini_with_retry", fake_generate)
    monkeypatch.setattr("backend.processing.voice_capture.transcribe", fake_transcribe)
    return m


def captured(tasks_dir) -> list[str]:
    return [ln for ln in (tasks_dir / "Captured.md").read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]


# --- core ----------------------------------------------------------------------------

async def test_voice_entry_writes_note_and_captures_commitments(model, vault, tasks_dir, tmp_path):
    r = await journal.record_entry(audio=b"x" * 5000, mime_type="audio/ogg", duration_seconds=90,
                                   when=WHEN, entry_id="e1")
    assert r.status == "saved" and r.summarised and r.commitments == ["Send Rosie the form"]
    assert r.note_path.endswith("/2026/2026-11-14 Journal.md")
    text = (vault / r.note_path).read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    assert fm["type"] == "journal" and fm["source"] == "journal" and str(fm["date"]) == "2026-11-14"
    assert "I went for a run" in text and "- The boiler" in text and "> Went for a run." in text
    assert captured(tasks_dir) == [
        "- Send Rosie the form (Captured on 2026-11-14 22:14 via Journal) (Source: [[2026-11-14 Journal]])"]
    assert [p.name for p in (tmp_path / "raw" / "journal").iterdir()] == ["2026-11-14_22-14-00_journal.ogg"]
    assert 0 < ledger.month_spent_pence() < 1          # a real entry costs a fraction of a penny


async def test_captured_line_is_the_format_clarify_parses(model, tasks_dir):
    from backend.vault.clarify_ops import captured_line
    await journal.record_entry(text=TRANSCRIPT, when=WHEN)
    assert captured(tasks_dir)[0] == captured_line("Send Rosie the form", WHEN, "Journal", "2026-11-14 Journal")


async def test_same_entry_id_saves_once(model, vault, tasks_dir):
    first = await journal.record_entry(text=TRANSCRIPT, when=WHEN, entry_id="dup")
    again = await journal.record_entry(text=TRANSCRIPT, when=WHEN, entry_id="dup")
    assert again.status == "duplicate" and len(captured(tasks_dir)) == 1
    assert (vault / first.note_path).read_text(encoding="utf-8").count("entry (via") == 1


async def test_second_entry_extends_the_same_note_and_keeps_edits(model, vault):
    first = await journal.record_entry(text="First thoughts.", when=WHEN, entry_id="a")
    path = vault / first.note_path
    path.write_text(path.read_text(encoding="utf-8") + "\nMy own added line.\n", encoding="utf-8")
    second = await journal.record_entry(text="Later thoughts.", when=WHEN.replace(hour=23), entry_id="b")
    text = path.read_text(encoding="utf-8")
    assert second.note_path == first.note_path
    assert "## 22:14 entry" in text and "## 23:14 entry" in text and "My own added line." in text
    assert text.count("# 2026-11-14 Journal") == 1 and text.count("---") == 2


async def test_after_midnight_belongs_to_the_evening_before(model):
    r = await journal.record_entry(text="Late one.", when=datetime(2026, 11, 15, 0, 40))
    assert r.journal_date == "2026-11-14"
    assert journal.journal_date_for(datetime(2026, 11, 15, 7, 0)) == date(2026, 11, 15)


async def test_no_commitments_captures_nothing(model, tasks_dir):
    model.reply = json.dumps({**EXTRACT, "commitments": []})
    r = await journal.record_entry(text="Nothing promised.", when=WHEN)
    assert r.status == "saved" and captured(tasks_dir) == [] and "commitment" not in r.message()


async def test_budget_spent_still_saves_the_transcript(model, vault, tasks_dir):
    ledger.record("other", "gemini-3.1-flash-lite", "generate", 0, 2_420_000)   # only the indexing reserve left
    r = await journal.record_entry(text=TRANSCRIPT, when=WHEN)
    assert r.status == "saved" and not r.summarised and model.sent == []
    assert "> Went for a run." in (vault / r.note_path).read_text(encoding="utf-8")
    assert captured(tasks_dir) == [] and "transcript only" in r.message()
    assert ledger.month_spent_pence() <= config.FEEDS_MONTHLY_BUDGET_PENCE


async def test_budget_spent_keeps_audio_without_transcribing(model, tmp_path):
    ledger.record("other", "gemini-3.1-flash-lite", "generate", 0, 2_420_000)   # only the indexing reserve left
    r = await journal.record_entry(audio=b"x" * 5000, mime_type="audio/ogg", duration_seconds=90, when=WHEN)
    assert r.status == "audio_only" and model.transcribed == []
    assert len(list((tmp_path / "raw" / "journal").iterdir())) == 1


async def test_cap_fully_spent_saves_nothing_and_says_so(model, vault):
    ledger.record("other", "gemini-3.1-flash-lite", "generate", 0, 2_500_000)   # exactly the cap
    r = await journal.record_entry(text=TRANSCRIPT, when=WHEN)
    assert r.status == "not_saved" and r.note_path is None and "not saved" in r.message()
    assert ledger.month_spent_pence() == pytest.approx(300.0)


async def test_unusable_model_reply_saves_transcript_only(model, vault):
    model.reply = "sorry, not json"
    r = await journal.record_entry(text=TRANSCRIPT, when=WHEN)
    assert r.status == "saved" and not r.summarised and (vault / r.note_path).exists()


async def test_silent_recording_saves_no_note(model, vault):
    model.transcript = "  "
    r = await journal.record_entry(audio=b"x" * 100, mime_type="audio/ogg", duration_seconds=3, when=WHEN)
    assert r.status == "empty" and r.note_path is None


async def test_dry_run_writes_and_spends_nothing(model, vault, tasks_dir, tmp_path):
    r = await journal.record_entry(audio=b"x" * 5000, mime_type="audio/ogg", duration_seconds=90,
                                   when=WHEN, entry_id="dry", dry_run=True)
    assert r.report.dry_run and r.report.cost_pence > 0 and r.report.model_calls == 2
    assert model.sent == [] and model.transcribed == []
    assert not (vault / r.note_path).exists() and captured(tasks_dir) == []
    assert not (tmp_path / "raw").exists() and ledger.month_spent_pence() == 0
    assert state.get_seen(journal.ENTRY_FEED, "dry") is None


async def test_identifiers_are_masked_on_the_way_out_and_restored_in_the_note(model, vault):
    nhs = "943 476 5919"
    model.reply = json.dumps({"summary": "I gave the surgery my number <NHS_1>.", "commitments": [], "on_my_mind": []})
    r = await journal.record_entry(text=f"Gave the surgery my NHS number {nhs}.", when=WHEN)
    assert nhs not in model.sent[0] and "<NHS_1>" in model.sent[0]
    assert f"my number {nhs}" in (vault / r.note_path).read_text(encoding="utf-8")
    assert nhs not in r.message()


def test_confirmation_never_repeats_content():
    r = journal.JournalResult(status="saved", journal_date="2026-11-14", summarised=True,
                              commitments=["Send Rosie the form", "Call the garage"])
    assert r.message() == "📓 Journal saved for 14 Nov. 2 commitments added to Captured."


# --- evening prompt ------------------------------------------------------------------

class FakeTelegram:
    def __init__(self):
        self.sent, self.next_id = [], 100

    async def post(self, url, json=None):
        self.sent.append(json)
        self.next_id += 1
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"message_id": self.next_id}})


async def test_prompt_is_sent_once_per_evening():
    tg = FakeTelegram()
    assert await journal.send_evening_prompt(tg, now=datetime(2026, 11, 14, 22, 0)) == 1
    assert await journal.send_evening_prompt(tg, now=datetime(2026, 11, 14, 22, 5)) == 0
    assert len(tg.sent) == 1 and tg.sent[0]["chat_id"] == 42 and "promise" in tg.sent[0]["text"]
    assert journal.prompt_date(42, 101) == date(2026, 11, 14)
    assert await journal.send_evening_prompt(tg, now=datetime(2026, 11, 15, 22, 0)) == 1


async def test_prompt_not_sent_when_feed_is_off(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_JOURNAL_FEED", False)
    tg = FakeTelegram()
    assert await journal.send_evening_prompt(tg) == 0 and tg.sent == []


# --- Telegram adapter ----------------------------------------------------------------

@pytest.fixture
def tg(monkeypatch):
    fake = FakeTelegram()

    async def fake_download(file_id, token, http_client):
        return b"audio-bytes"
    monkeypatch.setattr("backend.ingestion.telegram.download_telegram_file", fake_download)
    clock = {"now": datetime(2026, 11, 14, 12, 0)}          # midday: outside the journal window
    monkeypatch.setattr(journal_telegram, "_now", lambda: clock["now"])
    monkeypatch.setattr(journal_telegram, "_offers", journal_telegram.SessionStore("t", ttl=3600))
    fake.clock = clock
    return fake


def voice_msg(message_id=7, reply_to=None):
    msg = {"message_id": message_id, "chat": {"id": 42},
           "voice": {"file_id": "f1", "mime_type": "audio/ogg", "duration": 60}}
    if reply_to:
        msg["reply_to_message"] = {"message_id": reply_to}
    return msg


async def test_reply_to_prompt_is_journalled_under_the_prompts_date(model, tg, vault, tasks_dir):
    await journal.send_evening_prompt(tg, now=datetime(2026, 11, 14, 22, 0))
    handled = await journal_telegram.maybe_handle(voice_msg(reply_to=101), "", "tok", tg)
    assert handled and model.transcribed == [(b"audio-bytes", "audio/ogg")]
    assert state.get_seen("journal", "2026-11-14")
    assert tg.sent[-1]["text"].startswith("📓 Journal saved for 14 Nov. 1 commitment added")
    assert TRANSCRIPT not in " ".join(m["text"] for m in tg.sent)


async def test_ordinary_voice_note_is_left_for_the_capture_path(model, tg):
    await journal.send_evening_prompt(tg, now=datetime(2026, 11, 14, 22, 0))
    assert await journal_telegram.maybe_handle(voice_msg(), "", "tok", tg) is False
    assert await journal_telegram.maybe_handle(voice_msg(reply_to=999), "", "tok", tg) is False
    assert model.transcribed == [] and len(tg.sent) == 1


async def test_journal_command_then_voice_note(model, tg):
    cmd = {"message_id": 5, "chat": {"id": 42}, "text": "/journal"}
    assert await journal_telegram.maybe_handle(cmd, "/journal", "tok", tg)
    assert "Go ahead" in tg.sent[-1]["text"]
    assert await journal_telegram.maybe_handle(voice_msg(6), "", "tok", tg)
    assert len(model.transcribed) == 1
    assert await journal_telegram.maybe_handle(voice_msg(8), "", "tok", tg) is False   # pending is used up


async def test_journal_command_with_text_in_one_step(model, tg, tasks_dir):
    msg = {"message_id": 5, "chat": {"id": 42}, "text": "/journal " + TRANSCRIPT}
    assert await journal_telegram.maybe_handle(msg, msg["text"], "tok", tg)
    assert model.transcribed == [] and TRANSCRIPT in model.sent[0] and len(captured(tasks_dir)) == 1


async def test_another_command_abandons_a_pending_journal(model, tg):
    await journal_telegram.maybe_handle({"message_id": 5, "chat": {"id": 42}, "text": "/journal"}, "/journal", "tok", tg)
    nxt = {"message_id": 6, "chat": {"id": 42}, "text": "/next"}
    assert await journal_telegram.maybe_handle(nxt, "/next", "tok", tg) is False
    assert await journal_telegram.maybe_handle(voice_msg(7), "", "tok", tg) is False


async def test_redelivered_message_is_not_saved_twice(model, tg, tasks_dir):
    await journal.send_evening_prompt(tg, now=datetime(2026, 11, 14, 22, 0))
    await journal_telegram.maybe_handle(voice_msg(reply_to=101), "", "tok", tg)
    await journal_telegram.maybe_handle(voice_msg(reply_to=101), "", "tok", tg)
    assert len(captured(tasks_dir)) == 1 and "already saved" in tg.sent[-1]["text"]


async def test_feeds_status(model, tg):
    await journal.record_entry(text=TRANSCRIPT, when=WHEN, entry_id="e1")
    msg = {"message_id": 9, "chat": {"id": 42}, "text": "/feeds"}
    assert await journal_telegram.maybe_handle(msg, "/feeds", "tok", tg)
    text = tg.sent[-1]["text"]
    assert "of £3.00" in text and "• journal: 1 note" in text and "journal-entry" not in text


async def test_flag_off_handles_nothing(monkeypatch, model, tg):
    monkeypatch.setattr(config, "ENABLE_JOURNAL_FEED", False)
    for text in ("/journal hello", "/feeds", ""):
        msg = voice_msg(reply_to=101) if not text else {"message_id": 1, "chat": {"id": 42}, "text": text}
        assert await journal_telegram.maybe_handle(msg, text, "tok", tg) is False
    assert tg.sent == [] and model.sent == []


async def test_dispatcher_voice_capture_unchanged_when_not_a_journal(monkeypatch, model, tg, tasks_dir, tmp_path):
    """End to end through the real dispatcher: an ordinary voice note is still a capture."""
    from backend.ingestion import telegram
    monkeypatch.setattr(config, "INBOX_PATH", str(tmp_path / "inbox"))
    monkeypatch.setattr("backend.processing.voice_capture.INBOX_PATH", str(tmp_path / "inbox"))

    async def fake_transcribe(data, mime):
        return "Buy milk"
    monkeypatch.setattr("backend.processing.transcriber.transcribe_audio_via_gemini", fake_transcribe)
    await telegram.process_telegram_message(voice_msg(), "tok", tg)
    lines = captured(tasks_dir)
    assert len(lines) == 1 and lines[0].startswith("- Buy milk (Captured on") and "via Telegram Voice" in lines[0]
    assert state.get_seen("journal", journal.journal_date_for(datetime.now()).isoformat()) is None


# --- journal window: Journal / Capture buttons ----------------------------------------

def _buttons(sent: dict) -> list[str]:
    return [b["callback_data"] for b in sent["reply_markup"]["inline_keyboard"][0]]


async def test_plain_voice_note_in_the_window_offers_buttons(model, tg):
    await journal.send_evening_prompt(tg, now=datetime(2026, 11, 14, 22, 0))
    tg.clock["now"] = datetime(2026, 11, 14, 23, 40)
    assert await journal_telegram.maybe_handle(voice_msg(7), "", "tok", tg)
    assert _buttons(tg.sent[-1]) == ["jrnl:j:7", "jrnl:c:7"]
    assert model.transcribed == []                       # nothing spent until the user chooses


async def test_window_runs_to_the_rollover_even_without_a_prompt(tg):
    for hour, expected in ((21, False), (22, True), (2, True), (4, False)):
        day = 15 if hour < 12 else 14
        assert journal_telegram.in_journal_window(42, datetime(2026, 11, day, hour, 30)) is expected


async def test_window_opens_with_an_earlier_prompt(tg):
    await journal.send_evening_prompt(tg, now=datetime(2026, 11, 14, 21, 0))
    assert journal_telegram.in_journal_window(42, datetime(2026, 11, 14, 21, 15))
    assert not journal_telegram.in_journal_window(42, datetime(2026, 11, 14, 20, 55))


async def test_journal_button_saves_under_the_evening_it_was_recorded(model, tg, vault, tasks_dir):
    tg.clock["now"] = datetime(2026, 11, 14, 23, 50)
    await journal_telegram.maybe_handle(voice_msg(7), "", "tok", tg)
    tg.clock["now"] = datetime(2026, 11, 15, 8, 0)          # tapped the next morning
    await journal_telegram.handle_callback("jrnl:j:7", 42, 102, "tok", tg)
    assert model.transcribed == [(b"audio-bytes", "audio/ogg")]
    assert state.get_seen("journal", "2026-11-14")
    assert tg.sent[-1]["text"].startswith("📓 Journal saved for 14 Nov.")
    assert TRANSCRIPT not in " ".join(m.get("text", "") for m in tg.sent)
    await journal_telegram.handle_callback("jrnl:j:7", 42, 102, "tok", tg)     # double tap
    assert "expired" in tg.sent[-1]["text"] and len(model.transcribed) == 1


async def test_capture_button_takes_the_ordinary_capture_path(monkeypatch, model, tg, tasks_dir, tmp_path):
    monkeypatch.setattr(config, "INBOX_PATH", str(tmp_path / "inbox"))
    monkeypatch.setattr("backend.processing.voice_capture.INBOX_PATH", str(tmp_path / "inbox"))

    async def fake_transcribe(data, mime):
        return "Buy milk"
    monkeypatch.setattr("backend.processing.transcriber.transcribe_audio_via_gemini", fake_transcribe)
    tg.clock["now"] = datetime(2026, 11, 14, 22, 30)
    await journal_telegram.maybe_handle(voice_msg(7), "", "tok", tg)
    await journal_telegram.handle_callback("jrnl:c:7", 42, 102, "tok", tg)
    lines = captured(tasks_dir)
    assert len(lines) == 1 and lines[0].startswith("- Buy milk (Captured on")
    assert state.get_seen("journal", "2026-11-14") is None


async def test_reply_and_journal_command_still_skip_the_buttons(model, tg):
    await journal.send_evening_prompt(tg, now=datetime(2026, 11, 14, 22, 0))
    tg.clock["now"] = datetime(2026, 11, 14, 22, 30)
    await journal_telegram.maybe_handle(voice_msg(7, reply_to=101), "", "tok", tg)
    assert "reply_markup" not in tg.sent[-1] and len(model.transcribed) == 1


async def test_tap_after_restart_says_expired(tg):
    await journal_telegram.handle_callback("jrnl:j:7", 42, 102, "tok", tg)
    assert "expired" in tg.sent[-1]["text"] and "/journal" in tg.sent[-1]["text"]
