import subprocess
import os
from backend.config import OBSIDIAN_VAULT_PATH

# Every automated vault commit starts with this; /undo refuses anything else.
ORCHESTRATOR_PREFIX = "[orchestrator]"


def _git(args: list[str], vault_path: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=vault_path, check=True, capture_output=True, text=True)


def get_last_commit_info(vault_path: str = None) -> dict | None:
    """Returns {sha, subject, files, is_orchestrator} for the vault's HEAD, or None if there is no repo/commit."""
    vault_path = vault_path or OBSIDIAN_VAULT_PATH
    if not vault_path or not os.path.exists(os.path.join(vault_path, ".git")):
        return None
    try:
        sha, subject = _git(["log", "-1", "--format=%H%n%s"], vault_path).stdout.split("\n", 1)
        files = _git(["show", "--name-only", "--format=", "HEAD"], vault_path).stdout.split()
    except (subprocess.CalledProcessError, ValueError):
        return None
    subject = subject.strip()
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
