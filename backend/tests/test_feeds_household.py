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
    touch(docs / "Car" / "tiny.pdf", size=100)                    # too small to be a real PDF
    touch(docs / "Car" / "claim number.txt", size=200)            # short, but a note may be
    touch(docs / "Car" / "holiday.jpg")                           # listed, never read
    touch(docs / "Car" / "empty.eml", size=0)
    touch(docs / "code" / "node_modules" / "pkg" / "warranty.pdf")
    touch(docs / ".hidden" / "passport.pdf")
    touch(docs / "Music" / "manual.pdf")
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: opened.append(str(p)) or real_open(p, *a, **k))
    items = household_scan.inventory([str(docs)])
    assert sorted((os.path.basename(i["path"]), i["kind"]) for i in items) == [
        ("Annual benefit statement 2026.pdf", "PDF"), ("car insurance policy.pdf", "PDF"),
        ("claim number.txt", "note"), ("holiday.jpg", "photo")]
    assert opened == []                                           # nothing was read


def test_classify():
    assert household_scan.classify("/home/x/Documents/Car/car insurance policy.pdf")[0] == "Insurance"
    assert household_scan.classify("/home/x/Documents/Pension/statement.pdf")[0] == "Pension"
    assert household_scan.classify("/home/x/Documents/recipes/willow tree cake.pdf") == (None, 0)   # "will" must be a word
    assert household_scan.classify("/home/x/Documents/thesis/chapter3.pdf") == (None, 0)


def claim_folder(docs):
    """A synthetic folder shaped like a real claim: emails, a note, a link, photos and a video."""
    claim = docs / "Car" / "Claim Jun 2026"
    touch(claim / "Your Motor Claim.eml", 5000)
    touch(claim / "Car crash details.eml", 1500)
    touch(claim / "Insurer claim number.txt", 200)
    touch(claim / "Service Centre.url", 68)
    for n in range(3):
        touch(claim / f"boot_damage{n}.jpg", 50_000)
    touch(claim / "faulty_sensor.mp4", 90_000)
    touch(docs / "Car" / "Car research.xlsx", 18_000)
    touch(docs / "Car" / "car insurance policy.pdf")
    return claim


def test_shortlist_offers_folders_and_files(tmp_path):
    docs = tmp_path / "Documents"
    claim = claim_folder(docs)
    car = docs / "Car"
    text = household_scan.render_shortlist(household_scan.inventory([str(car)]), [str(car)])
    assert "Scanned 10 files in 1 folder(s)" in text and "6 can be read; 4 photos, videos" in text
    assert f"- [ ] 📁 `{car}` (10 files with subfolders: " in text
    assert f"- [x] 📁 `{claim}` (8 files: 3 photos, 2 emails, 1 note, 1 link, 1 video)" in text  # named like a subject
    assert f"  - [x] `{car / 'car insurance policy.pdf'}` (PDF," in text            # strong match, pre-ticked
    assert f"  - [ ] `{car / 'Car research.xlsx'}` (spreadsheet," in text
    assert f"  - [ ] `{claim / 'Your Motor Claim.eml'}` (email," in text            # its folder covers it
    assert ".jpg" not in text and ".mp4" not in text                               # counted, not offered
    folders, files = household_scan.ticked(text)
    assert folders == [str(claim)] and files == [str(car / "car insurance policy.pdf")]


def test_long_folders_list_only_likely_documents(tmp_path):
    folder = tmp_path / "Bank"
    for n in range(household_scan.LIST_ALL_UP_TO + 1):
        touch(folder / f"letter {n:02d}.pdf")
    touch(folder / "loan agreement.pdf")
    text = household_scan.render_shortlist(household_scan.inventory([str(folder)]), [str(folder)])
    assert "loan agreement.pdf" in text and "letter 00.pdf" not in text
    assert f"{household_scan.LIST_ALL_UP_TO + 1} more readable files here; tick the folder" in text


def test_bundles_from_ticks(tmp_path):
    docs = tmp_path / "Documents"
    claim = claim_folder(docs)
    car = docs / "Car"
    loose = str(car / "Car research.xlsx")
    inside = str(claim / "Your Motor Claim.eml")
    bundles, missing = household_scan.build_bundles([str(claim)], [loose, inside, str(car / "gone.pdf")])
    assert missing == [str(car / "gone.pdf")]
    folder, single = bundles                                      # the ticked email is already in the folder
    assert folder["kind"] == "folder" and folder["original_path"] == str(claim)
    assert sorted(f["name"] for f in folder["files"]) == [
        "Car crash details.eml", "Insurer claim number.txt", "Service Centre.url", "Your Motor Claim.eml"]
    assert sorted(f["kind"] for f in folder["listed"]) == ["photo", "photo", "photo", "video"]
    assert "staged_name" not in folder["listed"][0]                # never copied
    assert folder["files"][0]["staged_name"].startswith("b001/") and len(folder["files"][0]["sha256"]) == 64
    assert single == {**single, "kind": "file", "original_path": loose}
    assert single["files"][0]["staged_name"] == "b002/0001.xlsx"
    # A ticked subfolder gets its own note; its parent's note leaves those files out.
    bundles, _ = household_scan.build_bundles([str(car), str(claim)], [])
    assert sorted(f["name"] for f in bundles[0]["files"]) == ["Car research.xlsx", "car insurance policy.pdf"]
    assert bundles[0]["listed"] == [] and len(bundles[1]["files"]) == 4


def test_scan_writes_shortlist_and_send_dry_run_copies_nothing(tmp_path, capsys):
    docs, vault = tmp_path / "Documents", tmp_path / "vault"
    claim = claim_folder(docs)
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in claim.iterdir()}
    household_scan.main(["--vault", str(vault), "scan", str(docs)])
    assert f"[x] 📁 `{claim}`" in (vault / "00_System" / "Internal" / "Household_Shortlist.md").read_text(encoding="utf-8")
    household_scan.main(["--vault", str(vault), "send", "--dry-run"])
    out = capsys.readouterr().out
    assert "4 to copy; listed only: 3 photos, 1 video" in out and "5 file(s) in 2 item(s)" in out
    assert {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in claim.iterdir()} == before


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
    assert dry.documents == 1 and dry.empty == 1 and dry.unreadable == ["0002.pdf"] and dry.estimate_pence > 0
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
    assert line.startswith("- [ ] Renew Car insurance policy schedule (Acme Mutual) 📅 2027-03-01 🔔 2027-01-30")
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


# --- readers ----------------------------------------------------------------------------------

def make_eml(subject="Your motor claim", sent="Fri, 19 Jun 2026 12:57:00 +0100", html=False, attach_pdf=None):
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Date"] = "Claims Team <claims@example.com>", "me@example.com", subject, sent
    body = "Your claim reference is CL-4455. We will call you within 5 working days."
    if html:
        msg.set_content(f"<html><body><p>{body}</p><script>x()</script></body></html>", subtype="html")
    else:
        msg.set_content(body)
    if attach_pdf:
        msg.add_attachment(attach_pdf.read_bytes(), maintype="application", subtype="pdf", filename="Policy.pdf")
    return msg.as_bytes()


def make_xlsx(path):
    import zipfile
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rns}><sheets><sheet name="Cars" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}><si><t>Model</t></si><si><t>Price</t></si><si><t>Octavia</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<worksheet {ns}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                   f'<row r="2"><c r="A2" t="s"><v>2</v></c><c r="C2"><v>31500</v></c></row></sheetData></worksheet>')
    return path


def test_readers_by_type(tmp_path):
    from backend.feeds import readers
    pdf = make_pdf(tmp_path / "p.pdf")
    (tmp_path / "a.eml").write_bytes(make_eml(html=True, attach_pdf=pdf))
    text = readers.read_file(str(tmp_path / "a.eml")).text
    assert "Subject: Your motor claim" in text and "claim reference is CL-4455" in text
    assert "<p>" not in text and "x()" not in text
    assert "--- Attachment: Policy.pdf ---" in text and "Acme Mutual" in text
    (tmp_path / "n.txt").write_bytes("Claim no 4504 – call £0".encode("cp1252"))
    assert readers.read_file(str(tmp_path / "n.txt")).text == "Claim no 4504 – call £0"
    (tmp_path / "s.url").write_text("[InternetShortcut]\r\nURL=https://example.com/track\r\n")
    assert readers.read_file(str(tmp_path / "s.url")).text == "Link: https://example.com/track"
    (tmp_path / "c.csv").write_text("Model,Price\nOctavia,31500\n,\n")
    assert readers.read_file(str(tmp_path / "c.csv")).text == "Model | Price\nOctavia | 31500"
    assert readers.read_file(str(make_xlsx(tmp_path / "x.xlsx"))).text == "Sheet: Cars\nModel | Price\nOctavia |  | 31500"
    (tmp_path / "bad.msg").write_bytes(b"not an outlook file")
    (tmp_path / "bad.xlsx").write_bytes(b"not a zip")
    assert readers.read_file(str(tmp_path / "bad.msg")).text == "" == readers.read_file(str(tmp_path / "bad.xlsx")).text
    assert readers.readable_enough("x.txt", "short") and not readers.readable_enough("x.pdf", "short")


# --- folders on the server ----------------------------------------------------------------------

FOLDER_REPLY = {
    "title": "Car accident claim June 2026",
    "summary": "A claim after a minor collision. The insurer is arranging repairs.",
    "status": "open",
    "key_facts": ["Claim reference: CL-4455", "Insurer: Example Mutual"],
    "timeline": [{"date": "2026-06-20", "event": "Repairs booked"}, {"date": "2026-06-19", "event": "Claim made"},
                 {"date": "soon", "event": "Ignored"}],
    "outstanding": [{"item": "Send photos of the boot damage", "due": "2026-12-01"},
                    {"item": "Wait for the repair invoice", "due": ""}],
    "key_dates": [{"label": "Policy ends", "date": "2027-01-09", "kind": "expiry"}],
    "files": [{"file": 1, "about": "Claim confirmation from the insurer"}, {"file": 3, "about": "The claim number"}],
    "area": "03_Areas/Car",
}
CLAIM = "/home/sdmeers/Documents/Car/Claim Jun 2026"


def stage_folder(tmp_path, extra=()):
    folder = tmp_path / "staging" / "b001"
    folder.mkdir(exist_ok=True)
    files = [("Your Motor Claim.eml", "email", make_eml()),
             ("Same email.eml", "email", make_eml()),                    # a second copy of the same email
             ("Insurer claim number.txt", "note", b"Claim number CL-4455"),
             *extra]
    entries = []
    for n, (name, kind, data) in enumerate(files, 1):
        staged = folder / f"{n:04d}{os.path.splitext(name)[1]}"
        staged.write_bytes(data)
        entries.append({"staged_name": f"b001/{staged.name}", "original_path": f"{CLAIM}/{name}", "name": name,
                        "kind": kind, "size": len(data), "modified": "2026-06-19",
                        "sha256": household_scan.sha256(str(staged))})
    listed = [{"original_path": f"{CLAIM}/boot{n}.jpg", "name": f"boot{n}.jpg", "kind": "photo", "size": 9,
               "modified": "2026-09-26"} for n in range(2)]
    manifest = {"version": 2, "bundles": [{"id": "b001", "kind": "folder", "original_path": CLAIM,
                                           "files": entries, "listed": listed}]}
    (tmp_path / "staging" / "manifest.json").write_text(json.dumps(manifest))


def claim_note(vault):
    return vault / "03_Areas" / "Car" / "Car accident claim June 2026.md"


@pytest.fixture
def no_claim_note(vault):
    claim_note(vault).unlink(missing_ok=True)
    yield


async def test_folder_becomes_one_note(tmp_path, model, vault, tasks_dir, no_claim_note):
    model.reply = json.dumps(FOLDER_REPLY)
    stage_folder(tmp_path)
    dry = await household.process(dry_run=True, today=TODAY)
    assert (dry.folders, dry.files_to_read) == (1, 2) and model.sent == []      # the duplicate email is not read twice
    r = await household.process(approved=True, today=TODAY)
    (prompt,) = model.sent
    assert "File 1: Your Motor Claim.eml (email, last changed 2026-06-19)" in prompt and "File 2:" not in prompt
    assert "boot0.jpg (photo, last changed 2026-09-26): not read" in prompt and "<untrusted_content>" in prompt
    text = claim_note(vault).read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    assert fm["type"] == "household-folder" and fm["status"] == "open" and fm["original_path"] == CLAIM
    assert "- Claim reference: CL-4455" in text
    assert text.index("**2026-06-19:** Claim made") < text.index("**2026-06-20:** Repairs booked") and "Ignored" not in text
    assert "- Send photos of the boot damage (by 2026-12-01)" in text and "- Wait for the repair invoice" in text
    assert "- `Your Motor Claim.eml` (email, 2026-06-19): Claim confirmation from the insurer" in text
    assert "- `Insurer claim number.txt` (note, 2026-06-19): The claim number" in text
    assert "- `boot0.jpg` (photo, 2026-09-26)" in text
    assert "- 2 photos: listed above and kept on the laptop, never copied or read" in text
    assert "- `Same email.eml` (email, 2026-06-19): the same email as another file here" in text
    assert f"Folder on the laptop: `{CLAIM}`" in text and "Folder: Car/Claim Jun 2026" in prompt
    assert "## Key dates\n- **Policy ends:** 2027-01-09" in text
    expiry, photos = next_actions(tasks_dir)                                  # dated items only
    assert expiry.startswith("- [ ] Check expiry of: Policy ends (Car accident claim June 2026) 📅 2027-01-09")
    assert photos.startswith("- [ ] Send photos of the boot damage (Car accident claim June 2026) 📅 2026-12-01")
    assert r.reminders == 2 and not (tmp_path / "staging" / "b001").exists()


async def test_resending_a_folder_refreshes_its_note_and_keeps_hand_written_text(tmp_path, model, vault, tasks_dir,
                                                                              no_claim_note):
    model.reply = json.dumps(FOLDER_REPLY)
    stage_folder(tmp_path)
    await household.process(approved=True, today=TODAY)
    note = claim_note(vault)
    note.write_text(note.read_text(encoding="utf-8") + "\nMy own note: rang them on Tuesday.\n", encoding="utf-8")

    stage_folder(tmp_path)                                                    # unchanged: nothing to do
    again = await household.process(approved=True, today=TODAY)
    assert again.unchanged == 1 and len(model.sent) == 1

    model.reply = json.dumps({**FOLDER_REPLY, "summary": "Repairs are finished.", "status": "closed"})
    stage_folder(tmp_path, extra=[("Repair invoice.txt", "note", b"Invoice paid in full.")])
    await household.process(approved=True, today=TODAY)
    text = note.read_text(encoding="utf-8")
    assert "Repairs are finished." in text and "insurer is arranging" not in text
    assert "My own note: rang them on Tuesday." in text and text.count(household.START) == 1
    assert parse_frontmatter(text)["status"] == "closed"
    assert len(list(note.parent.glob("Car accident claim*"))) == 1
    assert len(next_actions(tasks_dir)) == 2                                  # reminders aren't added twice


async def test_a_large_folder_is_condensed_file_by_file(tmp_path, model, vault, monkeypatch, no_claim_note):
    monkeypatch.setattr(config, "HOUSEHOLD_MAX_CHARS", 300)
    model.reply = json.dumps(FOLDER_REPLY)
    stage_folder(tmp_path)
    dry = await household.process(dry_run=True, today=TODAY)
    await household.process(approved=True, today=TODAY)
    assert len(model.sent) == 3                                               # two files, then the folder
    assert "Claim reference" in model.sent[2]                                 # the condensed text went in
    assert dry.estimate_pence > 0 and claim_note(vault).exists()


async def test_staged_names_cannot_leave_the_staging_folder(tmp_path, model):
    (tmp_path / "secret.txt").write_text("not for the model " * 20)
    manifest = {"version": 2, "bundles": [{"kind": "file", "original_path": "/x/secret.txt", "listed": [],
                                           "files": [{"staged_name": "../secret.txt", "original_path": "/x/secret.txt",
                                                      "sha256": "0" * 64}]}]}
    (tmp_path / "staging" / "manifest.json").write_text(json.dumps(manifest))
    r = await household.process(approved=True, today=TODAY)
    assert r.documents == 0 and model.sent == []
