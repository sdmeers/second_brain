#!/usr/bin/env python3
"""Household document scan. Runs on the laptop, where the files are.

Uses only the Python standard library, so it runs with plain ``python3`` and needs no
virtual environment or .env. It never opens, moves, renames or changes an original:
``scan`` looks only at names, sizes and dates, and ``send`` reads a ticked file once to
copy it to the home server for reading.

  python3 backend/feeds/household_scan.py scan ~/Documents/Pension ~/Documents/Car ...
      Builds the inventory and writes a shortlist into the vault for you to tick.

  python3 backend/feeds/household_scan.py send
      Copies the ticked documents to the home server. Nothing is read by a model until
      you then approve the cost there.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime

DEFAULT_VAULT = os.environ.get("OBSIDIAN_VAULT_PATH") or os.path.expanduser("~/Documents/second_brain")
SHORTLIST_REL = os.path.join("00_System", "Internal", "Household_Shortlist.md")
DOC_EXTENSIONS = {".pdf", ".docx"}
SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "site-packages", "dist", "build", "target",
             "music", "pictures", "photos", "videos", "movies", "games", "steam", "snap", "cache", "tmp",
             "second_brain", "vault", "onedrivesafebackup"}
MIN_BYTES, MAX_BYTES = 4_000, 40_000_000

# category -> words looked for in the file name and its folders (lower case)
CATEGORIES = {
    "Insurance": ["insurance", "policy", "schedule of cover", "certificate of motor", "renewal", "cover note"],
    "Vehicle": ["v5", "logbook", "mot", "vehicle", "car tax", "ved", "service history", "dvla"],
    "Pension": ["pension", "annuity", "retirement", "sipp", "annual benefit"],
    "Mortgage and property": ["mortgage", "deed", "title register", "land registry", "epc", "survey", "completion",
                              "council tax", "buildings"],
    "Warranties and manuals": ["warranty", "guarantee", "manual", "instructions", "user guide"],
    "Certificates": ["certificate", "gas safety", "electrical", "eicr", "boiler", "birth", "marriage", "passport",
                     "licence", "license"],
    "Tax and legal": ["p60", "p45", "tax return", "self assessment", "hmrc", "will", "power of attorney", "probate"],
}
STRONG = {"insurance", "policy", "pension", "mortgage", "warranty", "guarantee", "certificate", "v5", "mot",
          "p60", "will", "deed", "passport"}


def inventory(folders):
    """Every candidate document under the folders, from metadata alone (no file is opened)."""
    items = []
    for folder in folders:
        folder = os.path.abspath(os.path.expanduser(folder))
        for root, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in SKIP_DIRS]
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if name.startswith((".", "~$")) or ext not in DOC_EXTENSIONS:
                    continue
                path = os.path.join(root, name)
                try:
                    st = os.stat(path, follow_symlinks=False)
                except OSError:
                    continue
                if not (MIN_BYTES <= st.st_size <= MAX_BYTES):
                    continue
                items.append({"path": path, "size": st.st_size,
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


def shortlist(items):
    """Likely documents grouped by category, duplicates removed, newest first."""
    seen, groups = set(), {}
    for item in sorted(items, key=lambda i: i["modified"], reverse=True):
        category, score = classify(item["path"])
        key = (os.path.basename(item["path"]).lower(), item["size"])
        if not category or key in seen:
            continue
        seen.add(key)
        groups.setdefault(category, []).append({**item, "score": score})
    return groups


def render_shortlist(groups, folders, total):
    count = sum(len(v) for v in groups.values())
    lines = ["# Household document shortlist", "",
             f"> Scanned {total} documents in {len(folders)} folder(s) on {datetime.now().strftime('%Y-%m-%d')}; "
             f"{count} look like household reference documents.",
             "> Tick the ones to read and untick the rest. Nothing has been opened or sent anywhere.",
             "> When the list is right, run `python3 backend/feeds/household_scan.py send` on the laptop.", ""]
    for category in CATEGORIES:
        if category not in groups:
            continue
        lines += [f"## {category}", ""]
        for item in groups[category]:
            tick = "x" if item["score"] >= 2 else " "
            lines.append(f"- [{tick}] `{item['path']}` ({item['modified']}, {max(1, item['size'] // 1024)} KB)")
        lines.append("")
    return "\n".join(lines)


def ticked_paths(text):
    return re.findall(r"^- \[[xX]\] `(.+?)`", text, flags=re.M)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(paths):
    docs, missing = [], []
    for n, path in enumerate(paths, 1):
        if not os.path.isfile(path):
            missing.append(path)
            continue
        st = os.stat(path)
        ext = os.path.splitext(path)[1].lower()
        docs.append({"staged_name": f"{n:04d}{ext}", "original_path": path, "size": st.st_size,
                     "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
                     "sha256": sha256(path)})
    return docs, missing


def cmd_scan(args):
    items = inventory(args.folders)
    groups = shortlist(items)
    out = os.path.join(args.vault, SHORTLIST_REL)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(render_shortlist(groups, args.folders, len(items)))
    print(f"{len(items)} documents found, {sum(len(v) for v in groups.values())} shortlisted.")
    print(f"Shortlist written to {out}\nOpen it in Obsidian, tick what you want read, then run: send")


def cmd_send(args):
    path = os.path.join(args.vault, SHORTLIST_REL)
    try:
        with open(path, encoding="utf-8") as f:
            paths = ticked_paths(f.read())
    except OSError:
        sys.exit(f"No shortlist at {path}. Run scan first.")
    docs, missing = build_manifest(paths)
    for m in missing:
        print(f"Skipped (no longer there): {m}")
    if not docs:
        sys.exit("Nothing is ticked.")
    print(f"{len(docs)} document(s) to copy to {args.server}:{args.dest}/")
    if args.dry_run:
        return
    with tempfile.TemporaryDirectory() as tmp:
        for d in docs:                      # symlinks, so rsync copies content without a second local copy
            os.symlink(d["original_path"], os.path.join(tmp, d["staged_name"]))
        with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"created": datetime.now().isoformat(timespec="seconds"), "documents": docs}, f, indent=1)
        subprocess.run(["rsync", "-a", "--copy-links", "--chmod=F600", tmp + "/",
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
