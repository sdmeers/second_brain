#!/usr/bin/env python3
"""Household document scan. Runs on the laptop, where the files are.

Uses only the Python standard library, so it runs with plain ``python3`` and needs no
virtual environment or .env. It never opens, moves, renames or changes an original:
``scan`` looks only at names, sizes and dates, and ``send`` reads a ticked file once to
copy it to the home server for reading.

  python3 backend/feeds/household_scan.py scan ~/Documents/Pension ~/Documents/Car ...
      Builds the inventory and writes a shortlist into the vault for you to tick. Tick a
      folder for one note about everything in it, or a file for a note about that file.

  python3 backend/feeds/household_scan.py send
      Copies the ticked files to the home server. Nothing is read by a model until you
      then approve the cost there. Photos and videos are never copied: the note lists
      them by name, date and place on the laptop.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime

DEFAULT_VAULT = os.environ.get("OBSIDIAN_VAULT_PATH") or os.path.expanduser("~/Documents/second_brain")
SHORTLIST_REL = os.path.join("00_System", "Internal", "Household_Shortlist.md")

# extension -> kind. Readable kinds are copied and read on the server (feeds/readers.py).
READABLE = {".pdf": "PDF", ".docx": "Word", ".eml": "email", ".msg": "email", ".txt": "note", ".md": "note",
            ".url": "link", ".csv": "spreadsheet", ".xlsx": "spreadsheet"}
# Listed in the note so it knows they exist, never copied or read.
MEDIA = {**dict.fromkeys((".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp", ".tif", ".tiff", ".bmp"), "photo"),
         **dict.fromkeys((".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".webm"), "video"),
         **dict.fromkeys((".m4a", ".mp3", ".wav", ".ogg", ".aac"), "recording")}
READABLE_KINDS, MEDIA_KINDS = set(READABLE.values()), set(MEDIA.values())
NAMES = {"PDF": ("PDF", "PDFs"), "Word": ("Word file", "Word files"), "email": ("email", "emails"),
         "note": ("note", "notes"), "link": ("link", "links"), "spreadsheet": ("spreadsheet", "spreadsheets"),
         "photo": ("photo", "photos"), "video": ("video", "videos"), "recording": ("recording", "recordings"),
         "other": ("other file", "other files")}
SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "site-packages", "dist", "build", "target",
             "music", "pictures", "photos", "videos", "movies", "games", "steam", "snap", "cache", "tmp",
             "second_brain", "vault", "onedrivesafebackup"}
MIN_DOC_BYTES, MAX_BYTES = 4_000, 40_000_000
# In a folder with more readable files than this, only likely documents are listed one
# by one; ticking the folder still covers all of them.
LIST_ALL_UP_TO = 15
# A folder whose own name suggests a subject ("Claim Jan 2025", "Pension") is pre-ticked
# as a whole, unless it is too big to make sense as one note.
PRETICK_FOLDER_UP_TO = 30

# category -> words looked for in the file name and its folders (lower case)
CATEGORIES = {
    "Insurance": ["insurance", "policy", "schedule of cover", "certificate of motor", "renewal", "cover note", "claim"],
    "Vehicle": ["v5", "logbook", "mot", "vehicle", "car tax", "ved", "service history", "dvla"],
    "Pension": ["pension", "annuity", "retirement", "sipp", "annual benefit"],
    "Mortgage and property": ["mortgage", "deed", "title register", "land registry", "epc", "survey", "completion",
                              "council tax", "buildings"],
    "Warranties and manuals": ["warranty", "guarantee", "manual", "instructions", "user guide"],
    "Certificates": ["certificate", "gas safety", "electrical", "eicr", "boiler", "birth", "marriage", "passport",
                     "licence", "license"],
    "Tax and legal": ["p60", "p45", "tax return", "self assessment", "hmrc", "will", "power of attorney", "probate"],
    "Money": ["finance agreement", "credit", "loan", "statement", "invoice", "receipt"],
}
STRONG = {"insurance", "policy", "pension", "mortgage", "warranty", "guarantee", "certificate", "v5", "mot",
          "p60", "will", "deed", "passport"}


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    return READABLE.get(ext) or MEDIA.get(ext) or "other"


def _skip_name(name):
    return name.startswith((".", "~$"))


def inventory(folders):
    """Every file under the folders, from metadata alone (no file is opened)."""
    items = []
    for folder in folders:
        folder = os.path.abspath(os.path.expanduser(folder))
        for root, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in SKIP_DIRS]
            for name in files:
                if _skip_name(name):
                    continue
                path = os.path.join(root, name)
                try:
                    st = os.stat(path, follow_symlinks=False)
                except OSError:
                    continue
                kind = kind_of(name)
                too_small = st.st_size == 0 or (kind in ("PDF", "Word") and st.st_size < MIN_DOC_BYTES)
                if too_small or st.st_size > MAX_BYTES:
                    continue
                items.append({"path": path, "size": st.st_size, "kind": kind, "root": folder,
                              "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")})
    return items


def classify(path):
    """(category, score) for a path, or (None, 0) when nothing suggests a household document."""
    words = re.sub(r"[_\-./\\]+", " ", path.lower())
    best, best_score = None, 0
    for category, needles in CATEGORIES.items():
        hits = [n for n in needles if re.search(rf"(?<![a-z0-9]){re.escape(n)}s?(?![a-z])", words)]
        score = len(hits) + sum(1 for n in hits if n in STRONG)
        if score > best_score:
            best, best_score = category, score
    return best, best_score


def counts_text(items):
    """'3 emails, 1 PDF, 4 photos': most common first."""
    order = list(NAMES)
    counts = sorted(Counter(i["kind"] for i in items).items(), key=lambda kv: (-kv[1], order.index(kv[0])))
    return ", ".join(f"{n} {NAMES[k][n > 1]}" for k, n in counts)


def _display(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def _folders_with_files(items):
    """Every folder from each scanned root down that has files in it or beneath it."""
    folders = set()
    for item in items:
        d = os.path.dirname(item["path"])
        folders.add(d)
        while d != item["root"] and d.startswith(item["root"] + os.sep):
            d = os.path.dirname(d)
            folders.add(d)
    return folders


def render_shortlist(items, folders):
    """Every folder that holds readable files, each with its own readable files under it."""
    readable = sum(1 for i in items if i["kind"] in READABLE_KINDS)
    media = sum(1 for i in items if i["kind"] in MEDIA_KINDS)
    lines = ["# Household document shortlist", "",
             f"> Scanned {len(items)} files in {len(folders)} folder(s) on {datetime.now().strftime('%Y-%m-%d')}: "
             f"{readable} can be read; {media} photos, videos or recordings will be listed but not read; "
             f"{len(items) - readable - media} other files are listed by name only.",
             "> Tick a 📁 folder for one note about everything in it, including its subfolders (unless you tick "
             "those separately). Tick a file for a note about that file alone. Files inside a ticked folder "
             "need no tick of their own.",
             "> Nothing has been opened or sent anywhere. When the list is right, run "
             "`python3 backend/feeds/household_scan.py send` on the laptop.", ""]
    roots = {i["root"] for i in items}
    preticked = []
    for d in sorted(_folders_with_files(items)):
        under = [i for i in items if i["path"].startswith(d + os.sep)]
        here = [i for i in under if os.path.dirname(i["path"]) == d]
        if not any(i["kind"] in READABLE_KINDS for i in under):
            continue
        inside_ticked = any(d.startswith(p + os.sep) for p in preticked)
        folder_tick = (d not in roots and not inside_ticked and len(under) <= PRETICK_FOLDER_UP_TO
                       and classify(os.path.basename(d))[1] >= 1)
        if folder_tick:
            preticked.append(d)
        sub = " with subfolders" if len(under) > len(here) else ""
        lines += [f"## {_display(d)}", "",
                  f"- [{'x' if folder_tick else ' '}] 📁 `{d}` ({len(under)} files{sub}: {counts_text(under)})"]
        docs = sorted((i for i in here if i["kind"] in READABLE_KINDS),
                      key=lambda i: (i["modified"], i["path"]), reverse=True)
        shown = docs if len(docs) <= LIST_ALL_UP_TO else [i for i in docs if classify(i["path"])[0]]
        for item in shown:
            tick = "x" if not (folder_tick or inside_ticked) and classify(item["path"])[1] >= 2 else " "
            lines.append(f"  - [{tick}] `{item['path']}` ({item['kind']}, {item['modified']}, "
                         f"{max(1, item['size'] // 1024)} KB)")
        if len(shown) < len(docs):
            lines.append(f"  - {len(docs) - len(shown)} more readable files here; tick the folder to include them.")
        lines.append("")
    return "\n".join(lines)


def ticked(text):
    """(ticked folders, ticked files) from the shortlist."""
    folders, files = [], []
    for m in re.finditer(r"^\s*- \[[xX]\] (📁 )?`(.+?)`", text, flags=re.M):
        (folders if m.group(1) else files).append(m.group(2))
    return folders, files


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_entry(path, base):
    st = os.stat(path)
    return {"original_path": path, "name": os.path.relpath(path, base), "kind": kind_of(path), "size": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")}


def _walk(folder, stop_at):
    for root, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() not in SKIP_DIRS
                         and os.path.join(root, d) not in stop_at)
        for name in sorted(files):
            if not _skip_name(name):
                yield os.path.join(root, name)


def build_bundles(folders, files):
    """What will be sent: one bundle per ticked folder (its files, less any ticked
    subfolder's) and one per ticked file not already inside a ticked folder.
    Returns (bundles, missing paths)."""
    bundles, missing, covered = [], [], set()
    folders = [os.path.abspath(f) for f in folders]
    for folder in folders:
        if not os.path.isdir(folder):
            missing.append(folder)
            continue
        inner = {f for f in folders if f != folder and f.startswith(folder + os.sep)}
        read, listed = [], []
        for path in _walk(folder, inner):
            covered.add(path)
            try:
                entry = _file_entry(path, folder)
            except OSError:
                continue
            if entry["size"] == 0:
                continue
            (read if entry["kind"] in READABLE_KINDS and entry["size"] <= MAX_BYTES else listed).append(entry)
        bundles.append({"kind": "folder", "original_path": folder, "files": read, "listed": listed})
    for path in files:
        path = os.path.abspath(path)
        if path in covered:
            continue
        if not os.path.isfile(path):
            missing.append(path)
            continue
        bundles.append({"kind": "file", "original_path": path,
                        "files": [_file_entry(path, os.path.dirname(path))], "listed": []})
    bundles = [b for b in bundles if b["files"]]
    for n, b in enumerate(bundles, 1):
        b["id"] = f"b{n:03d}"
        for m, f in enumerate(b["files"], 1):
            f["staged_name"] = f"{b['id']}/{m:04d}{os.path.splitext(f['original_path'])[1].lower()}"
            f["sha256"] = sha256(f["original_path"])
    return bundles, missing


def cmd_scan(args):
    items = inventory(args.folders)
    out = os.path.join(args.vault, SHORTLIST_REL)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(render_shortlist(items, args.folders))
    readable = sum(1 for i in items if i["kind"] in READABLE_KINDS)
    print(f"{len(items)} files found ({counts_text(items) or 'none'}); {readable} can be read.\n"
          "Photos and videos will be listed in notes but not read.")
    print(f"Shortlist written to {out}\nOpen it in Obsidian, tick folders or files, then run: send")


def cmd_send(args):
    path = os.path.join(args.vault, SHORTLIST_REL)
    try:
        with open(path, encoding="utf-8") as f:
            folders, files = ticked(f.read())
    except OSError:
        sys.exit(f"No shortlist at {path}. Run scan first.")
    bundles, missing = build_bundles(folders, files)
    for m in missing:
        print(f"Skipped (no longer there): {m}")
    if not bundles:
        sys.exit("Nothing is ticked.")
    for b in bundles:
        extra = f"; listed only: {counts_text(b['listed'])}" if b["listed"] else ""
        label = "📁 " if b["kind"] == "folder" else ""
        print(f"  {label}{_display(b['original_path'])}: {len(b['files'])} to copy{extra}")
    print(f"{sum(len(b['files']) for b in bundles)} file(s) in {len(bundles)} item(s) to copy to "
          f"{args.server}:{args.dest}/")
    if args.dry_run:
        return
    with tempfile.TemporaryDirectory() as tmp:
        for b in bundles:
            os.makedirs(os.path.join(tmp, b["id"]))
            for f in b["files"]:                # symlinks, so rsync copies content without a second local copy
                os.symlink(f["original_path"], os.path.join(tmp, f["staged_name"]))
        with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 2, "created": datetime.now().isoformat(timespec="seconds"), "bundles": bundles},
                      f, indent=1)
        subprocess.run(["rsync", "-a", "--copy-links", "--chmod=D700,F600", tmp + "/",
                        f"{args.server}:{args.dest}/"], check=True)
    print("Copied. On the home server run:\n"
          "  python3 -m backend.feeds.household process --dry-run   (shows the cost)\n"
          "  python3 -m backend.feeds.household process --approve")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", default=DEFAULT_VAULT, help="path to the Obsidian vault on this machine")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("folders", nargs="+")
    scan.set_defaults(func=cmd_scan)
    send = sub.add_parser("send")
    send.add_argument("--server", default="homeserver")
    send.add_argument("--dest", default="exports/household")
    send.add_argument("--dry-run", action="store_true")
    send.set_defaults(func=cmd_send)
    args = parser.parse_args(argv)
    args.vault = os.path.expanduser(args.vault)
    args.func(args)


if __name__ == "__main__":
    main()
