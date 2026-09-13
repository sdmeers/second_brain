"""/undo must only ever revert the orchestrator's own latest commit."""

import subprocess

import pytest

from backend.ingestion.handlers.undo import build_undo_prompt
from backend.utils.git import get_last_commit_info, revert_orchestrator_commit


def _git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "note.md").write_text("original\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "Hand-made baseline")
    return tmp_path


def _commit(repo, name, content, message):
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


class TestRevertOrchestratorCommit:
    def test_reverts_orchestrator_commit(self, repo):
        _commit(repo, "ingested.md", "summary\n", "[orchestrator] Ingested: [[X]] (Category: deep_research)")
        info = get_last_commit_info(str(repo))
        assert info["is_orchestrator"] and info["files"] == ["ingested.md"]

        ok, message = revert_orchestrator_commit(info["sha"], str(repo))

        assert ok, message
        assert not (repo / "ingested.md").exists()
        assert _git(repo, "log", "-1", "--format=%s").startswith('Revert "[orchestrator]')

    def test_refuses_hand_made_commit(self, repo):
        _commit(repo, "note.md", "edited by hand\n", "Tidy notes")
        info = get_last_commit_info(str(repo))

        ok, _ = revert_orchestrator_commit(info["sha"], str(repo))

        assert not ok
        assert (repo / "note.md").read_text(encoding="utf-8") == "edited by hand\n"

    def test_refuses_when_head_moved_since_confirmation(self, repo):
        _commit(repo, "a.md", "a\n", "[orchestrator] Ingested: [[A]] (Category: url_capture)")
        stale_sha = get_last_commit_info(str(repo))["sha"]
        _commit(repo, "b.md", "b\n", "[orchestrator] Ingested: [[B]] (Category: url_capture)")

        ok, _ = revert_orchestrator_commit(stale_sha, str(repo))

        assert not ok
        assert (repo / "a.md").exists() and (repo / "b.md").exists()

    def test_conflicting_revert_is_aborted(self, repo):
        _commit(repo, "note.md", "orchestrator version\n", "[orchestrator] Ingested: [[N]] (Category: url_capture)")
        sha = get_last_commit_info(str(repo))["sha"]
        (repo / "note.md").write_text("uncommitted synced edit\n", encoding="utf-8")

        ok, _ = revert_orchestrator_commit(sha, str(repo))

        assert not ok
        assert (repo / "note.md").read_text(encoding="utf-8") == "uncommitted synced edit\n"
        assert get_last_commit_info(str(repo))["sha"] == sha

    def test_no_repo(self, tmp_path):
        assert get_last_commit_info(str(tmp_path)) is None


class TestUndoPrompt:
    def test_orchestrator_commit_gets_confirm_buttons(self):
        info = {"sha": "a" * 40, "subject": "[orchestrator] Ingested: [[<X>]]", "files": ["f.md"], "is_orchestrator": True}
        text, markup = build_undo_prompt(info)
        assert "&lt;X&gt;" in text
        assert markup["inline_keyboard"][0][0]["callback_data"] == "undo:confirm:" + "a" * 12

    def test_hand_commit_has_no_buttons(self):
        info = {"sha": "b" * 40, "subject": "Tidy", "files": [], "is_orchestrator": False}
        _, markup = build_undo_prompt(info)
        assert markup is None
