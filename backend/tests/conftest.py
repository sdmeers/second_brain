"""Shared fixtures.

The backend requires OBSIDIAN_VAULT_PATH and an API key at import time, so a throwaway
vault is created and the environment populated *before* any backend module is imported.
Tests therefore never touch the real vault.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

_TEST_VAULT = Path(tempfile.mkdtemp(prefix="second_brain_test_vault_"))

for sub in ("00_System/Inbox", "00_System/Internal", "01_Tasks",
            "02_Projects/Personal", "02_Projects/Professional", "03_Areas",
            "04_Resources/Research_Brain/Summaries", "04_Resources/Research_Brain/Sources",
            "04_Resources/Research_Brain/Atlas", "04_Resources/Research_Brain/Concepts",
            "04_Resources/Articles_and_News/Sources", "05_Archive"):
    (_TEST_VAULT / sub).mkdir(parents=True, exist_ok=True)

os.environ["OBSIDIAN_VAULT_PATH"] = str(_TEST_VAULT)
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used")
os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("ENABLE_TELEGRAM_BOT", "false")
os.environ.setdefault("ENABLE_GMAIL_POLLING", "false")
os.environ.setdefault("ENABLE_OBSIDIAN_INBOX_POLLING", "false")
os.environ["TASK_BACKUP_PATH"] = tempfile.mkdtemp(prefix="second_brain_test_backups_")
# Tests write task files and run the dashboard straight away; don't treat that as a
# fresh edit from another device.
os.environ["TASK_SYNC_QUIET_SECONDS"] = "0"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def vault() -> Path:
    """Path to the throwaway test vault."""
    return _TEST_VAULT


@pytest.fixture
def tasks_dir(vault) -> Path:
    return vault / "01_Tasks"


@pytest.fixture(autouse=True)
def clean_task_files(tasks_dir):
    """Each test starts with empty GTD files."""
    for name in ("Next_Actions.md", "Waiting_For.md", "Someday_Maybe.md",
                 "Captured.md", "z_Completed.md"):
        (tasks_dir / name).write_text("", encoding="utf-8")
    yield
