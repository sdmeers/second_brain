"""No credentials in tracked files.

A Jira API token was once hard-coded in ``create_jira_epics.py`` and committed. The
``.gitignore`` rules for ``.env`` files could not have stopped that, because the secret
was in source code, not in an env file. This scans everything git tracks for strings
shaped like real credentials, so the test suite (run before every commit) catches it.

Credentials belong in ``backend/.env``, which is gitignored; ``backend/.env.example``
holds placeholders only.
"""

import os
import re
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SECRET_SHAPES = {
    "Atlassian API token": re.compile(r"ATATT[0-9A-Za-z_=\-]{20,}"),
    "Telegram bot token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "OpenAI API key": re.compile(r"\bsk-[A-Za-z0-9_\-]{30,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def tracked_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [f for f in out.stdout.decode().split("\0") if f]


def test_env_files_are_ignored_not_tracked():
    tracked = tracked_files()
    leaked = [f for f in tracked if os.path.basename(f).startswith(".env") and not f.endswith(".env.example")]
    leaked += [f for f in tracked if f.endswith((".jira.env", ".pem", ".key", ".token"))]
    assert leaked == [], f"credential files are tracked by git: {leaked}"

    ignored = subprocess.run(["git", "check-ignore", "-q", "backend/.env"], cwd=REPO_ROOT)
    assert ignored.returncode == 0, "backend/.env is no longer covered by .gitignore"


def test_no_credential_shaped_strings_in_tracked_files():
    found = []
    for rel in tracked_files():
        path = os.path.join(REPO_ROOT, rel)
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (UnicodeDecodeError, OSError):
            continue  # binary or missing from the working tree
        for name, shape in SECRET_SHAPES.items():
            if shape.search(text):
                found.append(f"{rel}: looks like a {name}")
    assert found == [], (
        "Possible credentials in tracked files (move them to backend/.env):\n  " + "\n  ".join(found))
