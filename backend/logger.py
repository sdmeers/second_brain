import os
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=dotenv_path)

OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")

def log_action(action_type: str, details: str, concepts: list[str] = None):
    """
    Appends a log entry to Vault/log.md.
    Format: ## [{YYYY-MM-DD HH:MM}] {action_type} | {details} | Concepts: {concepts}
    """
    if not OBSIDIAN_VAULT_PATH:
        print("Logger: OBSIDIAN_VAULT_PATH not set, skipping log.")
        return
        
    log_file_path = os.path.join(OBSIDIAN_VAULT_PATH, "log.md")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    concepts_str = ""
    if concepts:
        concepts_str = f" | Concepts: {', '.join(concepts)}"
        
    log_entry = f"## [{timestamp}] {action_type} | {details}{concepts_str}\n"
    
    try:
        with open(log_file_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception as e:
        print(f"Logger Error: Could not write to {log_file_path}: {e}")
