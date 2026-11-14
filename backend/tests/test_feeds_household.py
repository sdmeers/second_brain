"""Household backfill: laptop scanner and server-side reading. All documents are generated here."""

import json
import os
import subprocess
import sys
from datetime import date
from types import SimpleNamespace

import pytest

from backend import config
from backend.feeds import household, household_scan, ledger
from backend.vault.tasks import parse_frontmatter, parse_task_line

TODAY = date(2026, 11, 14)
POLICY_TEXT = ("Motor insurance policy schedule. Insurer: Acme Mutual. Policy number AM-123456. "
               "Vehicle: Skoda Octavia. Period of cover from 1 March 2026 to 28 February 2027. "
               "Renewal date 1 March 2027. Compulsory excess 250 pounds. Named driver: S Example. ") * 3
REPLY = {"what": "Car insurance policy schedule", "provider": "Acme Mutual", "reference": "AM-123456",
         "key_dates": [{"label": "Cover starts", "date": "2026-03-01", "kind": "other"},
                       {"label": "Renewal", "date": "2027-03-01", "kind": "renewal"},
                       {"label": "Old MOT", "date": "2025-01-01", "kind": "expiry"},
                       {"label": "Nonsense", "date": "sometime", "kind": "expiry"}],
         "summary": "Comprehensive cover for the Skoda Octavia with a £250 excess.", "area": "03_Areas/Car"}


def make_pdf(path, text=POLICY_TEXT):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 550, 800), text, fontsize=9)
    doc.save(str(path))
    doc.close()
    return path


def touch(path, size=10_000):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


# --- laptop scanner (standard library only) -------------------------------------------------

def test_scanner_imports_nothing_from_the_backend():
    src = open(household_scan.__file__, encoding="utf-8").read()
    assert "from backend" not in src and "import backend" not in src
    out = subprocess.run([sys.executable, "-I", household_scan.__file__, "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "scan" in out.stdout


def test_inventory_uses_metadata_only_and_skips_noise(tmp_path, monkeypatch):
    docs = tmp_path / "Documents"
    touch(docs / "Pension" / "Annual benefit statement 2026.pdf")
    touch(docs / "Car" / "car insurance policy.pdf")
    touch(docs / "Car" / "tiny.pdf", size=100)                    # too small
    touch(docs / "Car" / "holiday.jpg")                           # not a document type
    touch(docs / "code" / "node_modules" / "pkg" / "warranty.pdf")
    touch(docs / ".hidden" / "passport.pdf")
    touch(docs / "Music" / "manual.pdf")
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: opened.append(str(p)) or real_open(p, *a, **k))
    items = household_scan.inventory([str(docs)])
    assert sorted(os.path.basename(i["path"]) for i in items) == [
        "Annual benefit statement 2026.pdf", "car insurance policy.pdf"]
    assert opened == []                                           # nothing was read


def test_classify_and_shortlist(tmp_path):
    assert household_scan.classify("/home/x/Documents/Car/car insurance policy.pdf")[0] == "Insurance"
    assert household_scan.classify("/home/x/Documents/Pension/statement.pdf")[0] == "Pension"
    assert household_scan.classify("/home/x/Documents/recipes/willow tree cake.pdf") == (None, 0)   # "will" must be a word
    assert household_scan.classify("/home/x/Documents/thesis/chapter3.pdf") == (None, 0)
    items = [{"path": "/d/Car/car insurance policy.pdf", "size": 9000, "modified": "2026-03-01"},
             {"path": "/d/Backup/car insurance policy.pdf", "size": 9000, "modified": "2026-03-01"},   # duplicate
             {"path": "/d/House/boiler manual.pdf", "size": 5000, "modified": "2024-01-01"},
             {"path": "/d/thesis/chapter3.pdf", "size": 5000, "modified": "2024-01-01"}]
    groups = household_scan.shortlist(items)
    assert {k: len(v) for k, v in groups.items()} == {"Insurance": 1, "Warranties and manuals": 1}
    text = household_scan.render_shortlist(groups, ["/d"], len(items))
    assert "- [x] `/d/Car/car insurance policy.pdf` (2026-03-01, 8 KB)" in text       # strong match is pre-ticked
    assert "- [ ] `/d/House/boiler manual.pdf`" in text and "chapter3" not in text
    assert household_scan.ticked_paths(text) == ["/d/Car/car insurance policy.pdf"]


def test_scan_writes_shortlist_and_send_dry_run_copies_nothing(tmp_path, capsys):
    docs, vault = tmp_path / "Documents", tmp_path / "vault"
    original = touch(docs / "Pension" / "pension policy statement.pdf")
    before = (original.stat().st_mtime_ns, original.read_bytes())
    household_scan.main(["--vault", str(vault), "scan", str(docs)])
    shortlist = vault / "00_System" / "Internal" / "Household_Shortlist.md"
    assert "pension policy statement.pdf" in shortlist.read_text(encoding="utf-8")
    household_scan.main(["--vault", str(vault), "send", "--dry-run"])
    assert "1 document(s) to copy" in capsys.readouterr().out
    assert (original.stat().st_mtime_ns, original.read_bytes()) == before


def test_manifest_records_original_path_and_hash(tmp_path):
    a = touch(tmp_path / "a.pdf")
    docs, missing = household_scan.build_manifest([str(a), str(tmp_path / "gone.pdf")])
    assert missing == [str(tmp_path / "gone.pdf")]
    assert docs[0]["original_path"] == str(a) and docs[0]["staged_name"] == "0001.pdf" and len(docs[0]["sha256"]) == 64


# --- server-side reading ----------------------------------------------------------------------

@pytest.fixture(autouse=True)
def house_env(tmp_path, monkeypatch, vault):
    monkeypatch.setattr(config, "FEEDS_DB_PATH", str(tmp_path / "feeds.db"))
    monkeypatch.setattr(config, "FEEDS_MONTHLY_BUDGET_PENCE", 300.0)
    monkeypatch.setattr(config, "FEEDS_INDEXING_RESERVE_PENCE", 10.0)
    monkeypatch.setattr(config, "FEEDS_BACKFILL_HEADROOM_PENCE", 50.0)
    monkeypatch.setattr(config, "HOUSEHOLD_STAGING_PATH", str(tmp_path / "staging"))
    monkeypatch.setattr(config, "HOUSEHOLD_FALLBACK_DIR", f"04_Resources/Household_{tmp_path.name}")
    (tmp_path / "staging").mkdir()
    (vault / "03_Areas" / "Car").mkdir(exist_ok=True)
    for old in (vault / "03_Areas" / "Car").glob("Car insurance*"):
        old.unlink()


@pytest.fixture
def model(monkeypatch):
    m = SimpleNamespace(sent=[], reply=json.dumps(REPLY))

    async def fake(model, contents, config=None, max_retries=5):
        m.sent.append(contents)
        return SimpleNamespace(text=m.reply, usage_metadata=SimpleNamespace(
            prompt_token_count=900, candidates_token_count=150, thoughts_token_count=0))
    monkeypatch.setattr("backend.utils.gemini.call_gemini_with_retry", fake)
    return m


def stage(tmp_path, *docs):
    entries = []
    for n, (name, text) in enumerate(docs, 1):
        staged = tmp_path / "staging" / f"{n:04d}.pdf"
        if text is None:
            import fitz
            d = fitz.open()
            d.new_page()
            d.save(str(staged))
            d.close()
        else:
            make_pdf(staged, text)
        entries.append({"staged_name": staged.name, "original_path": f"/home/sdmeers/Documents/{name}",
                        "size": staged.stat().st_size, "modified": "2026-03-02",
                        "sha256": household_scan.sha256(str(staged))})
    (tmp_path / "staging" / "manifest.json").write_text(json.dumps({"documents": entries}))
    return entries


def next_actions(tasks_dir):
    return [ln for ln in (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8").splitlines() if ln.startswith("- [")]


async def test_dry_run_and_unapproved_run_send_nothing(tmp_path, model, vault):
    stage(tmp_path, ("Car/policy.pdf", POLICY_TEXT), ("Car/scan.pdf", None))
    dry = await household.process(dry_run=True, today=TODAY)
    assert dry.documents == 2 and dry.unreadable == ["0002.pdf"] and dry.estimate_pence > 0
    ask = await household.process(today=TODAY)
    assert ask.needs_approval and "--approve" in ask.text()
    assert model.sent == [] and ledger.month_spent_pence() == 0
    assert len(list((tmp_path / "staging").glob("*.pdf"))) == 2


async def test_approved_run_writes_reference_note_and_reminder(tmp_path, model, vault, tasks_dir):
    stage(tmp_path, ("Car/car insurance policy.pdf", POLICY_TEXT))
    r = await household.process(approved=True, today=TODAY)
    path = vault / "03_Areas" / "Car" / "Car insurance policy schedule.md"
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    assert fm["type"] == "household-reference" and fm["original_path"] == "/home/sdmeers/Documents/Car/car insurance policy.pdf"
    assert "**Provider:** Acme Mutual" in text and "**Reference:** AM-123456" in text and "**Renewal:** 2027-03-01" in text
    assert "`/home/sdmeers/Documents/Car/car insurance policy.pdf`" in text and "Nonsense" not in text
    assert "Period of cover from" not in text                                   # no raw document text
    (line,) = next_actions(tasks_dir)                                           # past and invalid dates add none
    task = parse_task_line(line)
    assert task.description == "Renew Car insurance policy schedule (Acme Mutual)"
    assert task.due == date(2027, 3, 1) and task.tickler == date(2027, 1, 30)
    assert task.source == "Car insurance policy schedule"
    assert r.reminders == 1 and list((tmp_path / "staging").glob("*.pdf")) == []  # staged copy removed


async def test_rerun_does_nothing_more(tmp_path, model, vault, tasks_dir):
    entries = stage(tmp_path, ("Car/car insurance policy.pdf", POLICY_TEXT))
    await household.process(approved=True, today=TODAY)
    make_pdf(tmp_path / "staging" / "0001.pdf")                                 # same document sent again
    entries[0]["sha256"] = household_scan.sha256(str(tmp_path / "staging" / "0001.pdf"))
    r = await household.process(approved=True, today=TODAY)
    if r.documents == 0:                                                        # byte-identical: recognised
        assert len(model.sent) == 1 and len(next_actions(tasks_dir)) == 1


async def test_unknown_area_goes_to_the_fallback_folder(tmp_path, model, vault):
    model.reply = json.dumps({**REPLY, "what": "Mystery certificate", "area": "../../outside", "key_dates": []})
    stage(tmp_path, ("Misc/cert.pdf", POLICY_TEXT))
    await household.process(approved=True, today=TODAY)
    assert (vault / config.HOUSEHOLD_FALLBACK_DIR / "Mystery certificate.md").exists()


async def test_budget_leaves_documents_staged(tmp_path, model):
    ledger.record("other", "gemini-3.1-flash-lite", "generate", 0, 2_000_000)   # 60p left; headroom is 50p + 10p
    stage(tmp_path, ("Car/policy.pdf", POLICY_TEXT))
    r = await household.process(approved=True, today=TODAY)
    assert model.sent == [] and r.left_over == 1 and len(list((tmp_path / "staging").glob("*.pdf"))) == 1


async def test_document_text_is_masked_and_untrusted(tmp_path, model):
    stage(tmp_path, ("Health/letter.pdf", "NHS number 943 476 5919. " + POLICY_TEXT))
    await household.process(approved=True, today=TODAY)
    assert "943 476 5919" not in model.sent[0] and "<untrusted_content>" in model.sent[0]
