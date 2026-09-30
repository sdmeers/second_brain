import subprocess
import os
from backend.config import OBSIDIAN_VAULT_PATH

# Every automated vault commit starts with this; /undo refuses anything else.
ORCHESTRATOR_PREFIX = "[orchestrator]"
# Automatic 01_Tasks/ snapshots. /undo looks past these so they never block it.
TASK_SNAPSHOT_PREFIX = "[tasks]"


def _git(args: list[str], vault_path: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=vault_path, check=True, capture_output=True, text=True)


def get_last_commit_info(vault_path: str = None) -> dict | None:
    """Returns {sha, subject, files, is_orchestrator} for the latest non-snapshot commit, or None if there is none."""
    vault_path = vault_path or OBSIDIAN_VAULT_PATH
    if not vault_path or not os.path.exists(os.path.join(vault_path, ".git")):
        return None
    try:
        log = _git(["log", "-200", "--format=%H%x00%s"], vault_path).stdout.splitlines()
    except subprocess.CalledProcessError:
        return None
    # Task snapshots are made every time 01_Tasks/ settles, so the orchestrator commit
    # the user means by "undo" is the latest one that isn't a snapshot.
    entries = [line.split("\0", 1) for line in log if "\0" in line]
    entries = [(sha, subject.strip()) for sha, subject in entries
               if not subject.strip().startswith(TASK_SNAPSHOT_PREFIX)]
    if not entries:
        return None
    sha, subject = entries[0]
    try:
        files = _git(["show", "--name-only", "--format=", sha], vault_path).stdout.split()
    except subprocess.CalledProcessError:
        return None
    return {
        "sha": sha.strip(),
        "subject": subject,
        "files": files,
        "is_orchestrator": subject.startswith(ORCHESTRATOR_PREFIX),
    }


def revert_orchestrator_commit(expected_sha: str, vault_path: str = None) -> tuple[bool, str]:
    """Reverts HEAD, but only if it is still `expected_sha` and was made by the orchestrator.

    Returns (success, message). A revert that conflicts is aborted so the vault is left untouched.
    """
    vault_path = vault_path or OBSIDIAN_VAULT_PATH
    info = get_last_commit_info(vault_path)
    if not info:
        return False, "No git history found in the vault."
    if info["sha"] != expected_sha:
        return False, "The vault has had a new commit since you asked, so nothing was reverted. Run /undo again."
    if not info["is_orchestrator"]:
        return False, "The last commit was not made by the orchestrator, so it won't be reverted."
    try:
        _git(["revert", "--no-edit", expected_sha], vault_path)
    except subprocess.CalledProcessError as e:
        subprocess.run(["git", "revert", "--abort"], cwd=vault_path, capture_output=True, text=True)
        detail = (e.stderr or e.stdout or str(e)).strip()
        return False, f"git revert failed and was aborted: {detail[:300]}"
    return True, f"Reverted: {info['subject']}"

def commit_paths(paths: list[str], message: str, vault_path: str = None) -> bool:
    """Commits only ``paths`` (relative to the vault), leaving any other changes alone.

    Returns True if a commit was made, False if there was nothing to commit, no repo,
    or git failed. Never pushes: snapshots ride along with the next orchestrator push.
    """
    vault_path = vault_path or OBSIDIAN_VAULT_PATH
    if not vault_path or not os.path.exists(os.path.join(vault_path, ".git")):
        return False
    try:
        _git(["add", "-A", "--", *paths], vault_path)
        if not _git(["status", "--porcelain", "--", *paths], vault_path).stdout.strip():
            return False
        _git(["commit", "-q", "-m", message, "--only", "--", *paths], vault_path)
        print(f"Backend [Git]: Successfully committed: {message}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Backend [Git]: Commit of {paths} failed: {(e.stderr or str(e)).strip()}")
        return False


def commit_vault_changes(commit_message: str):
    """Stages all changes in the vault, commits them, and optionally pushes to a remote repository."""
    if not OBSIDIAN_VAULT_PATH or not os.path.exists(os.path.join(OBSIDIAN_VAULT_PATH, ".git")):
        print("Backend [Git]: Git repository not found in vault directory. Skipping commit.")
        return False
        
    try:
        # 1. git add -A
        subprocess.run(
            ["git", "add", "-A"],
            cwd=OBSIDIAN_VAULT_PATH,
            check=True,
            capture_output=True,
            text=True
        )
        
        # 2. Check if there are changes to commit
        status_res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=OBSIDIAN_VAULT_PATH,
            check=True,
            capture_output=True,
            text=True
        )
        if not status_res.stdout.strip():
            print("Backend [Git]: No changes to commit in vault.")
            return True
            
        # 3. git commit -m
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=OBSIDIAN_VAULT_PATH,
            check=True,
            capture_output=True,
            text=True
        )
        print(f"Backend [Git]: Successfully committed: {commit_message}")
        
        # 4. Optional: git push (non-blocking / with timeout)
        try:
            # Check if a remote is configured
            remote_check = subprocess.run(
                ["git", "remote"],
                cwd=OBSIDIAN_VAULT_PATH,
                capture_output=True,
                text=True
            )
            if remote_check.stdout.strip():
                print("Backend [Git]: Pushing to remote repository...")
                subprocess.run(
                    ["git", "push"],
                    cwd=OBSIDIAN_VAULT_PATH,
                    timeout=30,
                    capture_output=True,
                    text=True
                )
                print("Backend [Git]: Push successful.")
        except subprocess.TimeoutExpired:
            print("Backend [Git]: Git push timed out (remote might be offline).")
        except Exception as push_e:
            print(f"Backend [Git]: Git push failed: {push_e}")
            
        return True
    except subprocess.CalledProcessError as e:
        print(f"Backend [Git]: Git operation failed: {e.stderr.strip() if e.stderr else str(e)}")
        return False
    except Exception as e:
        print(f"Backend [Git]: Unexpected error during git operation: {e}")
        return False
