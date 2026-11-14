"""Shared feed pieces: state store, cost ledger, masking, LLM wrapper, notes, retrieval.

Synthetic data only. The identifiers below are published test values, not real ones.
"""

import sqlite3
from types import SimpleNamespace

import pytest

from backend import config
from backend.feeds import ledger, llm, masking, notes, raw, retrieval, state
from backend.feeds.dryrun import RunReport
from backend.vault.tasks import parse_frontmatter

NHS = "943 476 5919"            # NHS test number with a valid check digit
CARD = "4111 1111 1111 1111"    # standard Luhn-valid test card
NINO = "AB123456C"
LICENCE = "MORGA657054SM9IJ"    # DVLA's published example


@pytest.fixture(autouse=True)
def feeds_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEEDS_DB_PATH", str(tmp_path / "feeds.db"))
    monkeypatch.setattr(config, "FEEDS_MONTHLY_BUDGET_PENCE", 300.0)
    monkeypatch.setattr(config, "FEEDS_APPROVAL_THRESHOLD_PENCE", 25.0)
    monkeypatch.setattr(config, "FEEDS_USD_TO_GBP", 0.80)


@pytest.fixture
def fake_model(monkeypatch):
    """Replaces the provider call; records what was sent and returns a canned reply."""
    calls = []

    async def fake(model, contents, config=None, max_retries=5):
        calls.append(SimpleNamespace(model=model, contents=contents, config=config))
        return SimpleNamespace(
            text=fake.reply(contents) if callable(fake.reply) else fake.reply,
            usage_metadata=SimpleNamespace(prompt_token_count=1000, candidates_token_count=200,
                                           thoughts_token_count=50))
    fake.reply = "summary"
    monkeypatch.setattr("backend.utils.gemini.call_gemini_with_retry", fake)
    return SimpleNamespace(calls=calls, fn=fake)


# --- masking -----------------------------------------------------------------------

@pytest.mark.parametrize("value,label", [(NHS, "NHS"), (CARD, "CARD"), (NINO, "NINO"), (LICENCE, "LICENCE")])
def test_mask_round_trip(value, label):
    text = f"Ref for the form is {value}, please keep it."
    masked, mapping = masking.mask(text)
    assert value not in masked
    assert f"<{label}_1>" in masked
    assert masking.unmask(masked, mapping) == text


def test_keyworded_identifiers_are_masked():
    text = "Sort code 12-34-56, account number 12345678, passport no. 123456789."
    masked, mapping = masking.mask(text)
    assert "12-34-56" not in masked and "12345678" not in masked and "123456789" not in masked
    assert masking.unmask(masked, mapping) == text


def test_dates_phone_numbers_and_amounts_are_left_alone():
    text = "Renewal on 12-11-26, call 01234 567890 or 07700 900123, ref 20261114, paid £1,234.56."
    assert masking.mask(text) == (text, {})


def test_same_identifier_gets_one_placeholder():
    masked, mapping = masking.mask(f"{NHS} and again {NHS}")
    assert len(mapping) == 1 and masked.count("<NHS_1>") == 2


def test_redact_is_one_way():
    out = masking.redact(f"my NHS number is {NHS}")
    assert NHS not in out and "<NHS>" in out


# --- state -------------------------------------------------------------------------

def test_state_tracks_seen_and_changed():
    assert state.get_seen("journal", "a") is None
    state.mark_seen("journal", "a", "h1", note_path="x.md")
    assert state.is_unchanged("journal", "a", "h1")
    assert not state.is_unchanged("journal", "a", "h2")
    assert not state.is_unchanged("email", "a", "h1")
    state.mark_seen("journal", "a", "h2", note_path="x.md")
    assert state.is_unchanged("journal", "a", "h2")


# --- ledger ------------------------------------------------------------------------

def test_cost_uses_published_prices():
    # 1M input at $0.25 + 1M output at $1.50 = $1.75 -> 140p at 0.80
    assert ledger.cost_pence("gemini-3.1-flash-lite", 1_000_000, 1_000_000) == pytest.approx(140.0)
    assert ledger.cost_pence("gemini-3.1-flash-lite", 1_000_000, 0, audio=True) == pytest.approx(40.0)


def test_unknown_model_is_charged_at_the_expensive_fallback():
    assert ledger.cost_pence("mystery-model", 1_000_000, 0) > ledger.cost_pence("gemini-3.1-flash-lite", 1_000_000, 0)


def test_budget_refuses_a_call_that_could_breach_the_cap():
    ledger.record("journal", "gemini-3.1-flash-lite", "generate", 0, 2_000_000)   # 240p
    assert ledger.month_spent_pence() == pytest.approx(240.0)
    ledger.check_budget("journal", 59.0)
    with pytest.raises(ledger.BudgetExceeded):
        ledger.check_budget("journal", 61.0)


def test_cap_is_shared_across_feeds_and_resets_each_month():
    ledger.record("journal", "gemini-3.1-flash-lite", "generate", 0, 1_000_000)
    ledger.record("email", "gemini-3.1-flash-lite", "generate", 0, 1_000_000)
    assert ledger.summary()["total_pence"] == pytest.approx(240.0)
    assert ledger.summary()["remaining_pence"] == pytest.approx(60.0)
    assert ledger.month_spent_pence("1999-01") == 0


def test_run_gate():
    ledger.check_run("journal", 10.0)
    with pytest.raises(ledger.ApprovalRequired):
        ledger.check_run("chats", 30.0)
    with pytest.raises(ledger.ApprovalRequired):
        ledger.check_run("chats", 1.0, backfill=True)
    ledger.check_run("chats", 30.0, backfill=True, approved=True)
    with pytest.raises(ledger.BudgetExceeded):          # approval never overrides the cap
        ledger.check_run("chats", 301.0, approved=True)


# --- LLM wrapper -------------------------------------------------------------------

async def test_generate_masks_before_sending_and_restores_after(fake_model):
    fake_model.fn.reply = lambda sent: f"Patient number <NHS_1> noted. ({'leak' if NHS in sent else 'clean'})"
    out = await llm.generate("email", llm.wrap_untrusted(f"NHS no {NHS}"), "Summarise.")
    sent = fake_model.calls[0]
    assert NHS not in sent.contents and "<NHS_1>" in sent.contents
    assert out == f"Patient number {NHS} noted. (clean)"


async def test_generate_sends_no_tools_and_caps_output(fake_model):
    await llm.generate("email", "hello", "Summarise.", max_output_tokens=256)
    cfg = fake_model.calls[0].config
    assert not cfg.tools
    assert cfg.max_output_tokens == 256
    assert "untrusted" in cfg.system_instruction and cfg.system_instruction.startswith("Summarise.")


async def test_generate_records_real_usage_including_thinking(fake_model):
    report = RunReport("email")
    await llm.generate("email", "hello", report=report)
    expected = ledger.cost_pence(config.LLM_MODEL, 1000, 250)
    assert ledger.month_spent_pence() == pytest.approx(expected)
    assert report.cost_pence == pytest.approx(expected) and report.model_calls == 1


async def test_generate_refuses_when_budget_is_gone(fake_model):
    ledger.record("x", "gemini-3.1-flash-lite", "generate", 0, 2_500_000)   # 300p: cap reached
    with pytest.raises(ledger.BudgetExceeded):
        await llm.generate("email", "hello")
    assert fake_model.calls == []


async def test_generate_dry_run_calls_nothing_and_spends_nothing(fake_model):
    report = RunReport("email", dry_run=True)
    assert await llm.generate("email", "hello " * 1000, report=report, dry_run=True) is None
    assert fake_model.calls == []
    assert ledger.month_spent_pence() == 0
    assert report.cost_pence > 0 and report.model_calls == 1


def test_ledger_and_state_never_hold_content(fake_model):
    """Only IDs, hashes, paths and counts: no column that could carry message text."""
    with state.connect() as conn:
        cols = {r[1] for t in ("ledger", "seen_items") for r in conn.execute(f"PRAGMA table_info({t})")}
    assert cols == {"id", "ts", "month", "feed", "model", "kind", "input_tokens", "output_tokens",
                    "cost_pence", "source_id", "content_hash", "note_path", "original_date", "ingested_at"}


# --- notes -------------------------------------------------------------------------

def _write(vault, **over):
    args = dict(feed="journal", source_id="2026-11-14", note_type="journal", title="Journal 2026-11-14",
                body="Quiet day.", dest_dir="03_Areas/Journal", original_date="2026-11-14")
    args.update(over)
    return notes.write_feed_note(**args)


def test_note_has_required_frontmatter(vault):
    rel = _write(vault)
    text = (vault / rel).read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    assert {"type", "source", "source_id", "date", "ingested"} <= set(fm)
    assert fm["source"] == "journal" and str(fm["date"]) == "2026-11-14"
    assert "Quiet day." in text


def test_second_run_writes_nothing(vault):
    report = RunReport("journal")
    rel = _write(vault, source_id="idem-1", title="Idem one", report=report)
    before = (vault / rel).stat().st_mtime_ns
    spent = ledger.month_spent_pence()
    assert _write(vault, source_id="idem-1", title="Idem one", report=report) is None
    assert (vault / rel).stat().st_mtime_ns == before
    assert ledger.month_spent_pence() == spent
    assert report.notes == [rel] and report.skipped == ["idem-1"]
    assert len(list((vault / "03_Areas/Journal").glob("Idem one*"))) == 1


def test_changed_source_rewrites_the_same_note(vault):
    rel = _write(vault, source_id="chg-1", title="Changing", body="v1")
    assert _write(vault, source_id="chg-1", title="Changing", body="v2") == rel
    assert "v2" in (vault / rel).read_text(encoding="utf-8")
    assert len(list((vault / "03_Areas/Journal").glob("Changing*"))) == 1


def test_two_sources_with_one_title_get_two_files(vault):
    a = _write(vault, source_id="dup-a", title="Same title")
    b = _write(vault, source_id="dup-b", title="Same title")
    assert a != b and (vault / a).exists() and (vault / b).exists()


def test_awkward_title_gives_valid_yaml_and_a_safe_filename(vault):
    rel = _write(vault, source_id="awk-1", title='Re: "Quote": a/b ../x', extra_frontmatter={"topic": 'He said: "hi"'})
    assert rel.startswith("03_Areas/Journal/") and ".." not in rel
    assert parse_frontmatter((vault / rel).read_text(encoding="utf-8"))["topic"] == 'He said: "hi"'


def test_note_dry_run_writes_nothing(vault):
    report = RunReport("journal", dry_run=True)
    rel = _write(vault, source_id="dry-1", title="Dry run note", report=report, dry_run=True)
    assert rel and not (vault / rel).exists()
    assert state.get_seen("journal", "dry-1") is None
    assert ledger.month_spent_pence() == 0
    assert "would write 1 note" in report.text()


def test_raw_store_copies_and_leaves_the_original(vault, tmp_path):
    src = tmp_path / "export.zip"
    src.write_bytes(b"raw")
    dry = raw.store_raw("chats", str(src), dry_run=True)
    assert not (vault / "00_System/Inbox/Raw").exists()
    dest = raw.store_raw("chats", str(src))
    assert dest == dry and dest.startswith(str(vault / "00_System/Inbox/Raw/chats"))
    assert src.read_bytes() == b"raw" and open(dest, "rb").read() == b"raw"


# --- retrieval ---------------------------------------------------------------------

def test_task_chunks_only_for_task_questions(tasks_dir):
    (tasks_dir / "Next_Actions.md").write_text(
        "- [ ] Call the garage @phone\n- [x] Done thing\n- [ ] Send Rosie the form\n", encoding="utf-8")
    assert retrieval.task_chunks("when does the car insurance renew?") == []
    chunks = retrieval.task_chunks("What did I promise people that I haven't done?")
    assert [c["file_path"] for c in chunks] == ["01_Tasks/Next_Actions.md"]
    assert "Call the garage @phone" in chunks[0]["text"] and "Done thing" not in chunks[0]["text"]
    # read-only: the file is byte-for-byte unchanged
    assert (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8").count("\n") == 3


def test_index_columns_and_tagging():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("CREATE TABLE brain_chunks (id INTEGER PRIMARY KEY, file_path TEXT)")
    cur.execute("INSERT INTO brain_chunks (file_path) VALUES ('a.md'), ('b.md')")
    retrieval.ensure_index_columns(cur)
    retrieval.ensure_index_columns(cur)   # idempotent
    retrieval.tag_indexed_file(cur, "a.md", "---\ntype: journal\ndate: 2026-11-14\n---\nbody")
    retrieval.tag_indexed_file(cur, "b.md", "no frontmatter")
    assert cur.execute("SELECT note_type, note_date FROM brain_chunks ORDER BY id").fetchall() == [
        ("journal", "2026-11-14"), (None, None)]


async def test_chat_is_unchanged_with_the_flag_off(monkeypatch, tasks_dir):
    """With ENABLE_FEED_RETRIEVAL off, a task question sends the model no task lines."""
    from backend.rag import chat
    (tasks_dir / "Next_Actions.md").write_text("- [ ] Call the garage @phone\n", encoding="utf-8")
    sent = []

    async def fake_embed(text): return [0.0]
    async def fake_search(vec, top_k=5): return [{"file_path": "n.md", "chunk_index": 0, "text": "note", "distance": 0.1}]
    async def fake_llm(model, contents, config=None):
        sent.append(contents)
        return SimpleNamespace(text="ok")
    monkeypatch.setattr(chat, "get_embedding", fake_embed)
    monkeypatch.setattr(chat, "search_similar_chunks", fake_search)
    monkeypatch.setattr(chat, "call_gemini_with_retry", fake_llm)

    monkeypatch.setattr(config, "ENABLE_FEED_RETRIEVAL", False)
    off = await chat.query_brain("what did I promise that is outstanding?")
    assert "Call the garage" not in sent[-1] and off["sources"] == ["n.md"]

    monkeypatch.setattr(config, "ENABLE_FEED_RETRIEVAL", True)
    on = await chat.query_brain("what did I promise that is outstanding?")
    assert "Call the garage @phone" in sent[-1] and "01_Tasks/Next_Actions.md" in on["sources"]


def test_telegram_dispatch_log_is_redacted():
    import inspect

    from backend.ingestion import telegram
    src = inspect.getsource(telegram.process_telegram_message)
    assert "redact(text)[:120]" in src and "redact(raw_text)" in src
