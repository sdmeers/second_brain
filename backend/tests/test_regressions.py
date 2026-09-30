"""One test per confirmed bug from the code review.

Each of these fails against the pre-fix code, so they are the guard against regression.
"""

import inspect
import os
import tempfile

import pytest

from backend.ingestion import watcher
from backend.ingestion.handlers import clarify, engage, instruction
from backend.utils import librarian
from backend.vault import tasks as vault_tasks


class TestUndefinedNames:
    """Bugs 1-3: names used but never defined/imported (NameError at runtime)."""

    def test_tickle_sessions_exists(self):
        """/tickle raised NameError on its first line."""
        assert hasattr(engage, "TICKLE_SESSIONS")
        engage.TICKLE_SESSIONS[999] = "buy card"
        assert engage.TICKLE_SESSIONS.get(999) == "buy card"
        del engage.TICKLE_SESSIONS[999]

    def test_clarify_has_timedelta(self):
        """parse_natural_date_str raised NameError for every non-ISO date."""
        assert "timedelta" in vars(clarify) or hasattr(clarify, "timedelta")

    def test_watcher_has_httpx(self):
        """The PDF content-type probe raised NameError inside a bare except."""
        assert hasattr(watcher, "httpx")


class TestQueueStatusKwarg:
    """Bug 4: update_item_status was called with error_msg= instead of error_message=."""

    def test_signature_uses_error_message(self):
        from backend.ingestion.queue import update_item_status
        assert "error_message" in inspect.signature(update_item_status).parameters

    def test_no_call_site_uses_the_wrong_kwarg(self):
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(watcher.__file__)))
        offenders = []
        for root, dirs, files in os.walk(backend_dir):
            dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__", "tests")]
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(root, f)
                    if "error_msg=" in open(path, encoding="utf-8").read():
                        offenders.append(path)
        assert offenders == [], f"error_msg= should be error_message=: {offenders}"


class TestPathsPointAtTheRealVault:
    """Bugs 5-6: modules referenced files that do not exist."""

    def test_instruction_handler_uses_01_tasks(self):
        """Was 00_System/Tasks.md, a pre-01_Tasks path, so every task primitive no-oped."""
        assert "01_Tasks" in instruction.TASKS_FILE
        assert instruction.TASKS_FILE.endswith("Next_Actions.md")

    def test_instruction_handler_archives_to_z_completed(self):
        assert instruction.COMPLETED_TASKS_FILE.endswith("z_Completed.md")

    def test_reflect_counts_completions_from_the_real_archive(self, tasks_dir):
        """/reflect read Completed.md and so always reported 0 completed this week."""
        vault_tasks.archive_completed_lines(["- [ ] Something finished"])
        data = engage.get_reflect_summary_data()
        assert data["completed_7d_count"] >= 1


class TestSyncConflictHealer:
    """Bug 7: the healer's task branch tested for 'tasks.md' and so never ran."""

    def test_next_actions_is_recognised(self):
        from backend.vault.task_merge import canonical_task_file_name
        assert canonical_task_file_name("next_actions.md") == "Next_Actions.md"
        assert canonical_task_file_name("tasks.md") is None

    def test_conflict_detection_requires_an_existing_original(self):
        """The old regex also matched ordinary notes like Notes-ABC1234.md."""
        d = tempfile.mkdtemp()
        open(os.path.join(d, "Next_Actions.md"), "w").close()
        assert watcher.is_sync_conflict_file("Next_Actions-OROPVGA.md", d) is True
        assert watcher.is_sync_conflict_file("Notes-ABC1234.md", d) is False

    def test_explicit_syncthing_marker_always_matches(self):
        assert watcher.is_sync_conflict_file(
            "Next_Actions.sync-conflict-20260101-120000-ABCDEFG.md", "/nonexistent") is True

    def test_ordinary_notes_are_never_conflicts(self):
        for name in ("Meeting Notes.md", "README.md", "Project Plan.md"):
            assert watcher.is_sync_conflict_file(name, "/tmp") is False


class TestPromptNewlines:
    """Bug 8: \\n in f-strings emitted a literal backslash-n into model prompts."""

    @pytest.mark.parametrize("module", [
        "backend/rag/chat.py",
        "backend/utils/librarian.py",
    ])
    def test_no_literal_backslash_n_in_prompt_strings(self, module):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(librarian.__file__))))
        source = open(os.path.join(repo_root, module), encoding="utf-8").read()
        assert "\\\\n" not in source, "literal backslash-n leaks into the prompt"


class TestArchiveAndRemoveReturnsFilename:
    """Bug 9: documented a return value but had no return statement."""

    def test_returns_the_source_filename(self):
        source = inspect.getsource(clarify.archive_and_remove_captured)
        assert "return filename" in source


class TestPackagingDeclaresRuntimeDeps:
    """Bug 11: docx/pptx were imported but not declared, so a clean sync crashed."""

    def test_docx_and_pptx_declared(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(librarian.__file__))))
        pyproject = open(os.path.join(repo_root, "backend", "pyproject.toml"),
                         encoding="utf-8").read()
        assert "python-docx" in pyproject
        assert "python-pptx" in pyproject

    def test_both_import(self):
        import docx  # noqa: F401
        import pptx  # noqa: F401


class TestParaDestinationIsConfined:
    """Hardening: the LLM-chosen subfolder is used to makedirs."""

    def test_allowlist_rejects_unknown_categories(self):
        assert "05_Archive" not in librarian.ALLOWED_PARA_CATEGORIES
        assert librarian.ALLOWED_PARA_CATEGORIES == {"02_Projects", "03_Areas", "04_Resources"}


class TestLazyGeminiClient:
    """Hardening: the client was built at import time regardless of provider."""

    def test_get_client_is_lazy(self):
        from backend.utils import gemini
        assert hasattr(gemini, "get_client")
        assert callable(gemini.get_client)


class TestQueueBackoff:
    """Hardening: failed items retried on the very next tick, three times in a row."""

    def test_backoff_tiers_are_configured(self):
        from backend.ingestion.queue import MAX_RETRIES, RETRY_BACKOFF_MINUTES
        assert RETRY_BACKOFF_MINUTES == (1, 5, 30)
        assert MAX_RETRIES == 3


class TestCommandUnwrapping:
    """Commands are rendered in backticks throughout the docs. Copying one out brought
    the backticks along, so text.startswith("/") was False and the message went to the
    RAG engine instead of the command dispatcher -- appearing to do nothing."""

    @pytest.mark.parametrize("wrapped,expected", [
        ("`/project second_brain add A step`", "/project second_brain add A step"),
        ("`/tickle Buy card`", "/tickle Buy card"),
        ("```/status```", "/status"),
        ("  `/next @home`  ", "/next @home"),
        ("``/daily``", "/daily"),
    ])
    def test_backticked_commands_are_unwrapped(self, wrapped, expected):
        from backend.ingestion.telegram import unwrap_command
        assert unwrap_command(wrapped) == expected

    @pytest.mark.parametrize("text", [
        "/project already clean",
        "just some plain text",
        "`a code snippet worth capturing`",
        "`print('hello')`",
        "What does `/tickle` do?",
        "",
    ])
    def test_non_commands_are_left_alone(self, text):
        from backend.ingestion.telegram import unwrap_command
        assert unwrap_command(text) == text

    def test_multiline_code_block_that_is_not_a_command_is_preserved(self):
        from backend.ingestion.telegram import unwrap_command
        snippet = "`def f():\n    return 1`"
        assert unwrap_command(snippet) == snippet
