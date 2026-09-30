"""Sync-conflict healing of the GTD task files must never lose a task.

On 2026-09-29 Syncthing kept an empty Next_Actions.md as the original and put the real
list in the conflict copy; the healer kept the original's open tasks only, deleted the
conflict copy, and every open task was gone with no backup.
"""

import os
import subprocess
import time

import pytest

from backend.ingestion import watcher
from backend.utils.git import commit_paths, get_last_commit_info
from backend.utils.tasks_dashboard import append_gtd_item, maintain_tasks_dashboard
from backend.vault import task_history
from backend.vault.task_merge import MergeResult, merge_task_file, task_key
from backend.vault.writer import atomic_write

EMPTY_NEXT_ACTIONS = (
    "# ⚡ Next Actions\n\n> Flexible, context-tagged action list. Filter in Telegram using "
    "`/next @context`.\n\n---\n\n## ⚡ Next Actions\n\n🎉 *All clear! No active next actions remaining.*\n"
)
FULL_NEXT_ACTIONS = (
    "# ⚡ Next Actions\n\n---\n\n## 🏠 Personal\n"
    "- [ ] Book earwax removal. @errands #personal\n"
    "- [ ] finish painting doors @home #personal #project/Doors\n"
    "- [ ] Send Kev a birthday card @errands #personal\n\n"
    "## 💼 Professional\n*None*\n"
)
CONFLICT_NAME = "Next_Actions.sync-conflict-20260929-232135-CD2N4J2.md"


@pytest.fixture
def notifications(monkeypatch):
    sent = []

    async def fake_notify(message):
        sent.append(message)

    monkeypatch.setattr(watcher, "notify_via_telegram", fake_notify)
    watcher._refused_task_heals.clear()
    return sent


async def _heal(tasks_dir, original: str, conflict: str, name=CONFLICT_NAME):
    (tasks_dir / "Next_Actions.md").write_text(original, encoding="utf-8")
    conflict_path = tasks_dir / name
    conflict_path.write_text(conflict, encoding="utf-8")
    ok = await watcher.auto_heal_syncthing_conflict(f"01_Tasks/{name}", str(conflict_path))
    return ok, conflict_path


class TestIncident:
    async def test_tasks_only_in_conflict_copy_survive(self, tasks_dir, notifications):
        ok, conflict_path = await _heal(tasks_dir, EMPTY_NEXT_ACTIONS, FULL_NEXT_ACTIONS)

        merged = (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8")
        assert ok and not conflict_path.exists()
        for task in ("Book earwax removal", "finish painting doors", "Send Kev a birthday card"):
            assert task in merged
        assert "All clear" not in merged
        assert "3 only in the other device's copy" in notifications[-1]

    async def test_both_copies_are_backed_up_before_merging(self, tasks_dir, notifications):
        await _heal(tasks_dir, EMPTY_NEXT_ACTIONS, FULL_NEXT_ACTIONS)

        backups = sorted(os.listdir(task_history.TASK_BACKUP_PATH))
        latest = os.path.join(task_history.TASK_BACKUP_PATH, backups[-1])
        assert open(os.path.join(latest, CONFLICT_NAME), encoding="utf-8").read() == FULL_NEXT_ACTIONS
        assert open(os.path.join(latest, "Next_Actions.md"), encoding="utf-8").read() == EMPTY_NEXT_ACTIONS

    async def test_original_tasks_are_kept_too(self, tasks_dir, notifications):
        await _heal(tasks_dir, FULL_NEXT_ACTIONS, EMPTY_NEXT_ACTIONS)
        merged = (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8")
        assert "Book earwax removal" in merged and "Send Kev a birthday card" in merged


class TestCompletions:
    async def test_tick_on_either_side_wins_and_is_archived_once(self, tasks_dir, notifications):
        original = "- [ ] Book earwax removal. @errands #personal\n- [ ] Keep me @home #personal\n"
        conflict = "- [x] Book earwax removal. @errands #personal\n- [ ] Keep me @home #personal\n"
        await _heal(tasks_dir, original, conflict)
        await _heal(tasks_dir, original, conflict,
                    name="Next_Actions.sync-conflict-20260929-232136-CD2N4J2.md")

        merged = (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8")
        archive = (tasks_dir / "z_Completed.md").read_text(encoding="utf-8")
        assert "Book earwax removal" not in merged and "Keep me" in merged
        assert archive.count("Book earwax removal") == 1

    async def test_ticks_in_the_original_are_archived_not_dropped(self, tasks_dir, notifications):
        """The old healer stripped the original's [x] lines without archiving them."""
        original = "- [x] Ticked on this device @home #personal\n"
        await _heal(tasks_dir, original, "- [ ] Other @home #personal\n")
        assert "Ticked on this device" in (tasks_dir / "z_Completed.md").read_text(encoding="utf-8")


class TestMerge:
    def test_retagged_task_is_not_duplicated(self):
        result = merge_task_file(
            "Next_Actions.md",
            "- [ ] Call the plumber @phone #personal 📅 2026-10-01\n",
            "- [ ] Call the plumber @home #personal\n")
        assert len(result.open_items) == 1
        assert "@phone" in result.open_items[0]

    def test_key_ignores_context_tags_and_dates(self):
        assert task_key("- [ ] Pay bill @home #personal 📅 2026-10-01") == task_key("- [x] Pay bill")

    def test_captured_items_are_unioned(self):
        result = merge_task_file("Captured.md", "- Idea one\n", "- Idea two\n")
        assert "Idea one" in result.content and "Idea two" in result.content
        assert not result.lost


class TestRefusal:
    async def test_a_lossy_merge_is_refused_and_leaves_files_alone(self, tasks_dir, notifications, monkeypatch):
        import backend.vault.task_merge as task_merge
        monkeypatch.setattr(task_merge, "merge_task_file", lambda *a: MergeResult(
            content=EMPTY_NEXT_ACTIONS, open_items=[], completed=[], lost=["- [ ] Book earwax removal."]))

        ok, conflict_path = await _heal(tasks_dir, FULL_NEXT_ACTIONS, FULL_NEXT_ACTIONS + "\n")

        assert not ok and conflict_path.exists()
        assert (tasks_dir / "Next_Actions.md").read_text(encoding="utf-8") == FULL_NEXT_ACTIONS
        assert "Needs You" in notifications[-1]

        # ...and it isn't retried (and re-notified) every 15 seconds
        notifications.clear()
        assert not await watcher.auto_heal_syncthing_conflict(f"01_Tasks/{CONFLICT_NAME}", str(conflict_path))
        assert notifications == []


class TestQuietPeriod:
    async def test_dashboard_leaves_a_freshly_synced_file_alone(self, tasks_dir, monkeypatch):
        monkeypatch.setattr(task_history, "TASK_SYNC_QUIET_SECONDS", 30)
        path = tasks_dir / "Next_Actions.md"
        path.write_text("- [x] Just ticked on the phone\n", encoding="utf-8")

        await maintain_tasks_dashboard()

        assert path.read_text(encoding="utf-8") == "- [x] Just ticked on the phone\n"

    def test_the_servers_own_writes_do_not_count(self, tasks_dir, monkeypatch):
        monkeypatch.setattr(task_history, "TASK_SYNC_QUIET_SECONDS", 30)
        path = str(tasks_dir / "Next_Actions.md")
        atomic_write(path, "- [ ] Written by the server\n")
        assert not task_history.edited_elsewhere_recently(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write("- [ ] Synced in\n")
        os.utime(path, ns=(time.time_ns(), time.time_ns() + 1000))
        assert task_history.edited_elsewhere_recently(path)

    async def test_appended_items_need_no_second_dashboard_write(self, tasks_dir):
        path = tasks_dir / "Next_Actions.md"
        append_gtd_item(str(path), "- [ ] First @home")
        append_gtd_item(str(path), "- [ ] Second @office #professional")
        before = path.read_text(encoding="utf-8")
        await maintain_tasks_dashboard()
        assert path.read_text(encoding="utf-8") == before


class TestDebouncedCommit:
    async def test_commits_once_after_writes_settle(self, tasks_dir, monkeypatch):
        commits = []
        monkeypatch.setattr(task_history, "commit_task_snapshot", lambda msg: commits.append(msg))
        atomic_write(str(tasks_dir / "Next_Actions.md"), "- [ ] a\n")
        atomic_write(str(tasks_dir / "Next_Actions.md"), "- [ ] b\n")

        await task_history.maybe_commit_task_changes(delay_seconds=60)
        assert commits == []
        await task_history.maybe_commit_task_changes(delay_seconds=0)
        await task_history.maybe_commit_task_changes(delay_seconds=0)
        assert len(commits) == 1

    async def test_nothing_written_means_no_commit(self, monkeypatch):
        commits = []
        monkeypatch.setattr(task_history, "commit_task_snapshot", lambda msg: commits.append(msg))
        monkeypatch.setattr(task_history, "_pending_since", None)
        await task_history.maybe_commit_task_changes(delay_seconds=0)
        assert commits == []


def _git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@e.x", *args],
                          cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "T")
    _git(tmp_path, "config", "user.email", "t@e.x")
    (tmp_path / "01_Tasks").mkdir()
    (tmp_path / "01_Tasks" / "Next_Actions.md").write_text("- [ ] a\n", encoding="utf-8")
    (tmp_path / "note.md").write_text("n\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "baseline")
    return tmp_path


class TestCommitPaths:
    def test_commits_only_the_given_paths(self, repo):
        (repo / "01_Tasks" / "Next_Actions.md").write_text("- [ ] b\n", encoding="utf-8")
        (repo / "01_Tasks" / "New.sync-conflict-1-2-X.md").write_text("c\n", encoding="utf-8")
        (repo / "note.md").write_text("edited elsewhere\n", encoding="utf-8")

        assert commit_paths(["01_Tasks"], "[tasks] snap", str(repo))

        assert set(_git(repo, "show", "--name-only", "--format=", "HEAD").split()) == {
            "01_Tasks/Next_Actions.md", "01_Tasks/New.sync-conflict-1-2-X.md"}
        assert "note.md" in _git(repo, "status", "--porcelain")

    def test_no_changes_no_commit(self, repo):
        assert not commit_paths(["01_Tasks"], "[tasks] snap", str(repo))
        assert _git(repo, "rev-list", "--count", "HEAD").strip() == "1"

    def test_undo_looks_past_task_snapshots(self, repo):
        (repo / "ingested.md").write_text("x\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "[orchestrator] Ingested: [[X]]")
        (repo / "01_Tasks" / "Next_Actions.md").write_text("- [ ] c\n", encoding="utf-8")
        commit_paths(["01_Tasks"], "[tasks] snap", str(repo))

        info = get_last_commit_info(str(repo))
        assert info["is_orchestrator"] and info["files"] == ["ingested.md"]

    def test_undo_reverts_beneath_task_snapshots(self, repo):
        from backend.utils.git import revert_orchestrator_commit
        (repo / "ingested.md").write_text("x\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "[orchestrator] Ingested: [[X]]")
        (repo / "01_Tasks" / "Next_Actions.md").write_text("- [ ] c\n", encoding="utf-8")
        commit_paths(["01_Tasks"], "[tasks] snap", str(repo))

        ok, message = revert_orchestrator_commit(get_last_commit_info(str(repo))["sha"], str(repo))

        assert ok, message
        assert not (repo / "ingested.md").exists()
        assert (repo / "01_Tasks" / "Next_Actions.md").read_text(encoding="utf-8") == "- [ ] c\n"
