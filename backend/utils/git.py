import subprocess
import os
from backend.config import OBSIDIAN_VAULT_PATH

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
