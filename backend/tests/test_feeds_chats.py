"""AI conversation summaries. The export below is invented, shaped like a Claude data export."""

import json
import os
import time
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from backend import config
from backend.feeds import approvals, chats, ledger, state
from backend.vault.tasks import parse_frontmatter

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
LONG = "I am choosing between the Bosch and the Miele dishwasher for the kitchen refit. " * 12
REPLY = {"topic": "Choosing a dishwasher", "summary": "I compared two dishwashers and picked one.",
         "decisions": ["Buy the Bosch because it is quieter"], "facts_and_preferences": ["The gap is 60cm wide"],
         "open_actions": ["Order the Bosch dishwasher"], "area": "03_Areas/House"}


def msg(sender, text, **extra):
    return {"uuid": f"m-{abs(hash(text)) % 10**8}", "sender": sender, "text": text,
            "content": [{"type": "text", "text": text}], "created_at": "2026-11-10T10:00:00Z", **extra}


def conv(uuid, name, updated, messages, **extra):
    return {"uuid": uuid, "name": name, "created_at": "2026-01-01T09:00:00.000000Z",
            "updated_at": updated, "chat_messages": messages, **extra}


def make_export(path, conversations, projects=None, inner="conversations.json"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(inner, json.dumps(conversations))
        zf.writestr("users.json", "[]")
        if projects is not None:
            zf.writestr("projects.json", json.dumps(projects))
    old = time.time() - 300
    os.utime(path, (old, old))
    return str(path)


def standard(path):
    return make_export(path, [
        conv("c-recent", "Dishwasher choice", "2026-11-10T10:00:00.000000Z",
             [msg("human", LONG), msg("assistant", "The Bosch is quieter. " * 20)]),
        conv("c-old", "Holiday ideas: Spain?", "2026-03-02T10:00:00.000000Z",
             [msg("human", LONG.replace("dishwasher", "villa")), msg("assistant", "Consider Andalusia. " * 20)]),
        conv("c-short", "Quick question", "2026-11-12T10:00:00.000000Z",
             [msg("human", "hi"), msg("assistant", "Hello!")]),
        conv("c-secret", "Surprise party plan", "2026-11-11T10:00:00.000000Z",
             [msg("human", LONG), msg("assistant", "Great idea. " * 30)]),
        conv("c-proj", "Refactor notes", "2026-11-09T10:00:00.000000Z",
             [msg("human", LONG), msg("assistant", "Done. " * 60)], project_uuid="p1"),
    ], projects=[{"uuid": "p1", "name": "Private Project"}])


@pytest.fixture(autouse=True)
def chats_env(tmp_path, monkeypatch, vault):
    monkeypatch.setattr(config, "FEEDS_DB_PATH", str(tmp_path / "feeds.db"))
    monkeypatch.setattr(config, "FEEDS_MONTHLY_BUDGET_PENCE", 300.0)
    monkeypatch.setattr(config, "FEEDS_INDEXING_RESERVE_PENCE", 10.0)
    monkeypatch.setattr(config, "FEEDS_APPROVAL_THRESHOLD_PENCE", 25.0)
    monkeypatch.setattr(config, "FEEDS_BACKFILL_HEADROOM_PENCE", 50.0)
    monkeypatch.setattr(config, "ENABLE_CHATS_FEED", True)
    monkeypatch.setattr(config, "CHAT_EXPORTS_PATH", str(tmp_path / "exports"))
    monkeypatch.setattr(config, "FEEDS_RAW_PATH", str(tmp_path / "raw"))
    monkeypatch.setattr(config, "CHAT_EXCLUSIONS_FILE", str(tmp_path / "Chat_Exclusions.md"))
    monkeypatch.setattr(config, "CHAT_NOTES_DIR", f"04_Resources/AI_Conversations_{tmp_path.name}")
    monkeypatch.setattr(config, "CHAT_NOTES_FILING", "single")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_CHAT_IDS", [42])
    (tmp_path / "exports").mkdir()
    (vault / "03_Areas" / "House").mkdir(exist_ok=True)


@pytest.fixture
def model(monkeypatch):
    m = SimpleNamespace(sent=[], reply=json.dumps(REPLY))

    async def fake(model, contents, config=None, max_retries=5):
        m.sent.append(contents)
        return SimpleNamespace(text=m.reply, usage_metadata=SimpleNamespace(
            prompt_token_count=800, candidates_token_count=150, thoughts_token_count=0))
    monkeypatch.setattr("backend.utils.gemini.call_gemini_with_retry", fake)
    return m


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def post(self, url, json=None):
        self.sent.append(json)
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 1}})


def captured(tasks_dir):
    return [ln for ln in (tasks_dir / "Captured.md").read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]


def notes_dir(vault):
    return vault / config.CHAT_NOTES_DIR


# --- parser ----------------------------------------------------------------------------

def test_claude_parser_reads_conversations(tmp_path):
    convs = {c.id: c for c in chats.parse_claude_export(standard(tmp_path / "e.zip"))}
    c = convs["c-recent"]
    assert c.title == "Dishwasher choice" and c.provider == "claude" and c.updated == datetime(2026, 11, 10, 10, tzinfo=UTC)
    assert [m.role for m in c.messages] == ["user", "assistant"] and c.url.endswith("/chat/c-recent")
    assert c.transcript().startswith("Steven: I am choosing") and "\n\nAssistant: The Bosch" in c.transcript()
    assert convs["c-proj"].project == "Private Project"


def test_parser_notes_attachments_and_nested_export_folder(tmp_path):
    path = make_export(tmp_path / "e.zip", [conv("a", "With file", "2026-11-10T10:00:00Z", [
        msg("human", "See attached", attachments=[{"file_name": "quote.pdf", "extracted_content": "SECRET BODY"}])])],
        inner="data-2026/conversations.json")
    (c,) = chats.parse_claude_export(path)
    assert "[attachment: quote.pdf]" in c.messages[0].text and "SECRET BODY" not in c.transcript()


@pytest.mark.parametrize("bad", [
    {"not": "a list"},
    [{"uuid": "x", "name": "n", "created_at": "2026-01-01T00:00:00Z"}],                       # no chat_messages
    [{"uuid": "x", "name": "n", "created_at": "2026-01-01T00:00:00Z",
      "chat_messages": [{"sender": "system", "text": "?"}]}],                                # unknown sender
    [{"uuid": "x", "name": "n", "created_at": "not a date", "chat_messages": []}],
])
def test_parser_refuses_an_unexpected_shape_rather_than_guess(tmp_path, bad):
    with pytest.raises(chats.UnrecognisedExport):
        chats.parse_claude_export(make_export(tmp_path / "e.zip", bad))


def test_provider_detection(tmp_path):
    assert chats.detect_provider(standard(tmp_path / "c.zip")) == "claude"
    g = make_export(tmp_path / "g.zip", [], inner="Takeout/My Activity/Gemini Apps/MyActivity.json")
    assert chats.detect_provider(g) == "gemini"
    with pytest.raises(chats.UnrecognisedExport):
        chats.parse_gemini_export(g)
    (tmp_path / "junk.zip").write_bytes(b"not a zip")
    with pytest.raises(chats.UnrecognisedExport):
        chats.detect_provider(str(tmp_path / "junk.zip"))


# --- free filters ----------------------------------------------------------------------

def test_selection_applies_exclusions_and_length(tmp_path):
    (tmp_path / "Chat_Exclusions.md").write_text(
        chats.EXCLUSIONS_TEMPLATE + "title: surprise party\n- project: Private Project\n", encoding="utf-8")
    sel = chats.select(chats.parse_claude_export(standard(tmp_path / "e.zip")))
    assert [c.id for c in sel.to_process] == ["c-recent", "c-old"]        # newest first
    assert (sel.excluded, sel.trivial, sel.unchanged) == (2, 1, 0)


def test_template_alone_excludes_nothing_and_keywords_match_text(tmp_path):
    (tmp_path / "Chat_Exclusions.md").write_text(chats.EXCLUSIONS_TEMPLATE, encoding="utf-8")
    assert chats.load_exclusions() == []
    (tmp_path / "Chat_Exclusions.md").write_text(chats.EXCLUSIONS_TEMPLATE + "andalusia\n", encoding="utf-8")
    sel = chats.select(chats.parse_claude_export(standard(tmp_path / "e.zip")))
    assert "c-old" not in [c.id for c in sel.to_process] and sel.excluded == 1


# --- runs ------------------------------------------------------------------------------

async def test_dry_run_reports_cost_and_writes_nothing(tmp_path, model, vault):
    r = await chats.process_export(standard(tmp_path / "e.zip"), dry_run=True)
    assert len(r.selection.to_process) == 4 and r.estimate_pence > 0 and model.sent == []
    assert not notes_dir(vault).exists() and ledger.month_spent_pence() == 0
    assert "4 to summarise" in r.text() and "1 too short" in r.text()


async def test_first_import_waits_for_approval(tmp_path, model, vault):
    r = await chats.process_export(standard(tmp_path / "e.zip"))
    assert r.needs_approval == "first import for this provider" and model.sent == []
    assert not notes_dir(vault).exists()


async def test_approved_backfill_writes_one_note_per_conversation(tmp_path, model, vault, tasks_dir):
    r = await chats.process_export(standard(tmp_path / "e.zip"), approved=True, now=NOW)
    assert len(r.report.notes) == 4 and r.left_over == 0
    path = notes_dir(vault) / "2026" / "2026-11-10 Dishwasher choice.md"
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    assert fm["type"] == "ai-conversation" and fm["provider"] == "claude" and fm["conversation_id"] == "c-recent"
    assert fm["source_id"] == "claude:c-recent" and fm["area"] == "03_Areas/House" and str(fm["date"]) == "2026-11-10"
    assert "## Decisions" in text and "Buy the Bosch because it is quieter" in text
    assert "https://claude.ai/chat/c-recent" in text and "I am choosing between" not in text    # no raw transcript
    assert (notes_dir(vault) / "2026" / "2026-03-02 Holiday ideas Spain.md").exists()


async def test_open_actions_only_from_the_last_14_days(tmp_path, model, tasks_dir):
    await chats.process_export(standard(tmp_path / "e.zip"), approved=True, now=NOW)
    lines = captured(tasks_dir)
    assert len(lines) == 3 and all("via Claude chat" in ln for ln in lines)     # c-old (March) adds none
    assert any("(Source: [[2026-11-10 Dishwasher choice]])" in ln for ln in lines)
    assert not any("Holiday" in ln for ln in lines)


async def test_second_run_with_the_same_export_writes_nothing(tmp_path, model, vault, tasks_dir):
    path = standard(tmp_path / "e.zip")
    await chats.process_export(path, approved=True, now=NOW)
    calls, spent, lines = len(model.sent), ledger.month_spent_pence(), captured(tasks_dir)
    r = await chats.process_export(path, now=NOW)
    assert r.selection.to_process == [] and r.selection.unchanged == 4 and not r.needs_approval
    assert len(model.sent) == calls and ledger.month_spent_pence() == spent and captured(tasks_dir) == lines


async def test_changed_conversation_is_resummarised_into_the_same_note(tmp_path, model, vault):
    await chats.process_export(standard(tmp_path / "e.zip"), approved=True, now=NOW)
    newer = make_export(tmp_path / "e2.zip", [conv(
        "c-recent", "Dishwasher choice", "2026-11-13T10:00:00.000000Z",
        [msg("human", LONG), msg("assistant", "The Bosch is quieter. " * 20), msg("human", "Ordered it. " * 10)])])
    model.reply = json.dumps({**REPLY, "summary": "I ordered the Bosch.", "open_actions": []})
    r = await chats.process_export(newer, now=NOW)
    assert len(r.selection.to_process) == 1 and not r.needs_approval            # small and not a first import
    files = list((notes_dir(vault) / "2026").glob("*Dishwasher choice*"))
    assert len(files) == 1 and "I ordered the Bosch." in files[0].read_text(encoding="utf-8")


async def test_backfill_stops_before_the_budget_and_resumes(tmp_path, model, vault, monkeypatch):
    path = standard(tmp_path / "e.zip")
    ledger.record("other", "gemini-3.1-flash-lite", "generate", 0, 2_000_000)       # 240p spent, 60p left
    r = await chats.process_export(path, approved=True, now=NOW)
    assert r.report.notes == [] and r.left_over == 4 and model.sent == []           # 50p headroom + 10p reserve
    assert "left for next month" in r.text()
    monkeypatch.setattr(config, "FEEDS_MONTHLY_BUDGET_PENCE", 600.0)                # "next month"
    r2 = await chats.process_export(path, approved=True, now=NOW)
    assert len(r2.report.notes) == 4 and r2.left_over == 0


async def test_identifiers_are_masked_and_content_is_marked_untrusted(tmp_path, model):
    path = make_export(tmp_path / "e.zip", [conv("n", "GP letter", "2026-11-10T10:00:00Z", [
        msg("human", "My NHS number is 943 476 5919. " + LONG), msg("assistant", "Noted. " * 40)])])
    await chats.process_export(path, approved=True, now=NOW)
    assert "943 476 5919" not in model.sent[0] and "<NHS_1>" in model.sent[0]
    assert "<untrusted_content>" in model.sent[0]


async def test_very_long_conversation_is_cut_in_the_middle(tmp_path, model, monkeypatch):
    monkeypatch.setattr(config, "CHAT_MAX_CHARS", 3000)
    path = make_export(tmp_path / "e.zip", [conv("big", "Long one", "2026-11-10T10:00:00Z", [
        msg("human", "START " + "x" * 20000 + " END")])])
    await chats.process_export(path, approved=True, now=NOW)
    assert len(model.sent[0]) < 3500 and "START" in model.sent[0] and "END" in model.sent[0]
    assert "middle of the conversation left out" in model.sent[0]


async def test_filing_by_area_when_chosen(tmp_path, model, vault, monkeypatch):
    monkeypatch.setattr(config, "CHAT_NOTES_FILING", "by_area")
    await chats.process_export(standard(tmp_path / "e.zip"), approved=True, now=NOW)
    assert (vault / "03_Areas/House/AI_Conversations/2026-11-10 Dishwasher choice.md").exists()
    model.reply = json.dumps({**REPLY, "area": "../../etc"})                        # an invented folder is ignored
    other = make_export(tmp_path / "o.zip", [conv("z", "Other", "2026-11-10T10:00:00Z", [msg("human", LONG * 2)])])
    await chats.process_export(other, now=NOW)
    assert (notes_dir(vault) / "2026" / "2026-11-10 Other.md").exists()


# --- polling and approval ----------------------------------------------------------------

async def test_poll_takes_in_export_asks_then_processes_after_approval(tmp_path, model, vault):
    standard(tmp_path / "exports" / "claude-export.zip")
    tg = FakeTelegram()
    await chats.poll(http_client=tg)
    assert os.listdir(tmp_path / "exports") == [] and len(os.listdir(tmp_path / "raw" / "chats")) == 1
    ask = tg.sent[-1]
    assert "4 to summarise" in ask["text"] and "Nothing has been sent to the model yet" in ask["text"]
    data = ask["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert model.sent == [] and (tmp_path / "Chat_Exclusions.md").exists()

    await chats.poll(http_client=tg)                      # still waiting: no second message, no calls
    assert len(tg.sent) == 1 and model.sent == []

    await approvals.handle_callback(data, 42, 1, "t", tg)
    assert "Approved" in tg.sent[-1]["text"]
    await approvals.handle_callback(data, 42, 1, "t", tg)
    assert "already been dealt with" in tg.sent[-1]["text"]

    await chats.poll(http_client=tg)
    assert len(model.sent) == 4 and "Wrote 4 note(s)" in tg.sent[-1]["text"]
    with state.connect() as conn:
        assert conn.execute("SELECT status FROM chat_exports").fetchone()[0] == "done"
    await chats.poll(http_client=tg)
    assert len(model.sent) == 4


async def test_cancel_sends_nothing_to_the_model(tmp_path, model):
    standard(tmp_path / "exports" / "claude-export.zip")
    tg = FakeTelegram()
    await chats.poll(http_client=tg)
    cancel = tg.sent[-1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"]
    await approvals.handle_callback(cancel, 42, 1, "t", tg)
    await chats.poll(http_client=tg)
    assert model.sent == [] and "Cancelled" in tg.sent[-1]["text"]


async def test_unreadable_file_is_set_aside_and_reported(tmp_path, model):
    bad = tmp_path / "exports" / "holiday-photos.zip"
    bad.write_bytes(b"nope")
    os.utime(bad, (time.time() - 300,) * 2)
    tg = FakeTelegram()
    await chats.poll(http_client=tg)
    assert os.listdir(tmp_path / "exports") == ["holiday-photos.zip.unrecognised"]
    assert "could not be read" in tg.sent[-1]["text"]


async def test_file_still_being_copied_is_left_alone(tmp_path, model):
    path = standard(tmp_path / "exports" / "claude-export.zip")
    os.utime(path, None)
    await chats.poll(http_client=FakeTelegram())
    assert os.listdir(tmp_path / "exports") == ["claude-export.zip"]


async def test_disabled_feed_touches_nothing(tmp_path, model, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_CHATS_FEED", False)
    standard(tmp_path / "exports" / "claude-export.zip")
    tg = FakeTelegram()
    await chats.poll(http_client=tg)
    assert os.listdir(tmp_path / "exports") == ["claude-export.zip"] and tg.sent == []


def test_oversized_archive_member_is_refused(tmp_path, monkeypatch):
    """A zip bomb must be refused before it is unpacked into memory."""
    import zipfile

    from backend.feeds import chats

    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("conversations.json", "[" + " " * 5000 + "]")
    monkeypatch.setattr(chats, "MAX_MEMBER_BYTES", 1000)
    with pytest.raises(chats.UnrecognisedExport):
        chats.parse_claude_export(str(path))


# --- Gemini (Google Takeout) ----------------------------------------------------------

def _gemini_entry(prompt, when, conv="abc123def4567890", html="<p>Use a <strong>ring main</strong>.</p>", **extra):
    entry = {"header": "Gemini Apps", "title": f"Prompted {prompt}", "time": when, "products": ["Gemini Apps"],
             "activityControls": ["Gemini Apps Activity"]}
    if conv:
        entry["details"] = [{"name": "https://gemini.google.com", "url": f"https://gemini.google.com/app/{conv}"}]
    if html:
        entry["safeHtmlItem"] = [{"html": html}]
    return {**entry, **extra}


def _gemini_zip(path, entries):
    return make_export(path, entries, inner="Takeout/My Activity/Gemini Apps/My Activity.json")


def test_gemini_entries_are_grouped_into_conversations_in_time_order(tmp_path):
    convs = chats.parse_gemini_export(_gemini_zip(tmp_path / "g.zip", [
        _gemini_entry("And the fuse rating?", "2026-03-02T10:05:00.000Z", html="<p>32 amp.</p>"),
        _gemini_entry("How should I wire the garage sockets?\nDetails follow", "2026-03-02T10:00:00.000Z"),
        _gemini_entry("Plan a week of dinners", "2026-03-03T18:00:00Z", conv="ffff000011112222"),
    ]))
    by_id = {c.id: c for c in convs}
    assert set(by_id) == {"abc123def4567890", "ffff000011112222"}
    garage = by_id["abc123def4567890"]
    assert [m.role for m in garage.messages] == ["user", "assistant", "user", "assistant"]
    assert garage.messages[0].text.startswith("How should I wire")
    assert "ring main" in garage.messages[1].text
    assert "<" not in garage.transcript()
    assert garage.title == "How should I wire the garage sockets?"
    assert garage.provider == "gemini" and garage.url == "https://gemini.google.com/app/abc123def4567890"
    assert garage.created < garage.updated


def test_gemini_images_and_attachments_become_markers_only(tmp_path):
    convs = chats.parse_gemini_export(_gemini_zip(tmp_path / "g.zip", [
        _gemini_entry("Draw a robin on a spade", "2026-03-02T10:00:00Z", html="",
                      subtitles=[{"name": "1 generated image."}], imageFile="robin-1a2b.png",
                      attachedFiles=["robin-1a2b.png"]),
        _gemini_entry("Summarise this", "2026-03-02T10:01:00Z", attachedFiles=["notes.docx"],
                      subtitles=[{"name": "Attached 1 file."}]),
    ]))
    texts = [m.text for m in convs[0].messages]
    assert texts[0] == "Draw a robin on a spade"
    assert texts[1] == "[1 generated image; not kept]"
    assert "[attachment: notes.docx]" in texts[2]


def test_gemini_non_prompt_activity_is_skipped_and_unlinked_prompts_group_by_day(tmp_path):
    entries = [
        {"header": "Gemini Apps", "title": "Created a Gem", "time": "2026-03-02T09:00:00Z"},
        _gemini_entry("first", "2026-03-02T10:00:00Z", conv=""),
        _gemini_entry("second", "2026-03-02T11:00:00Z", conv=""),
    ]
    convs = chats.parse_gemini_export(_gemini_zip(tmp_path / "g.zip", entries))
    assert len(convs) == 1 and convs[0].id == "day-2026-03-02" and convs[0].url == ""
    assert len(convs[0].messages) == 4


def test_gemini_unexpected_shapes_are_refused(tmp_path):
    with pytest.raises(chats.UnrecognisedExport):
        chats.parse_gemini_export(_gemini_zip(tmp_path / "a.zip", {"not": "a list"}))
    with pytest.raises(chats.UnrecognisedExport):
        chats.parse_gemini_export(_gemini_zip(tmp_path / "b.zip", [{"title": "Prompted x"}]))   # no time
    html_only = make_export(tmp_path / "c.zip", [], inner="Takeout/My Activity/Gemini Apps/My Activity.html")
    with pytest.raises(chats.UnrecognisedExport, match="JSON"):
        chats.parse_gemini_export(html_only)


def test_gemini_reimport_is_idempotent_by_fingerprint(tmp_path):
    entries = [_gemini_entry("How should I wire the garage sockets?", "2026-03-02T10:00:00Z")]
    a = chats.parse_gemini_export(_gemini_zip(tmp_path / "a.zip", entries))[0]
    b = chats.parse_gemini_export(_gemini_zip(tmp_path / "b.zip", entries))[0]
    assert a.fingerprint() == b.fingerprint()
    entries.append(_gemini_entry("And the fuse?", "2026-03-02T10:05:00Z"))
    c = chats.parse_gemini_export(_gemini_zip(tmp_path / "c.zip", entries))[0]
    assert c.fingerprint() != a.fingerprint()
