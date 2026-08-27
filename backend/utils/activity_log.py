import os
from datetime import datetime
from backend.config import OBSIDIAN_VAULT_PATH
from backend.vault.writer import atomic_write

def log_librarian_activity(title: str, location: str, source_type: str, extra: str = ''):
    """Appends a human-readable log entry to the Librarian Activity Log."""
    log_path = os.path.join(OBSIDIAN_VAULT_PATH, '00_System', 'Librarian_Activity.md')
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    
    # Only wrap in double brackets if it's a real file in a known location
    if location != 'Unknown':
        link_str = f"[[{title}]]"
    else:
        link_str = title
        
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    entry = f'- **{timestamp}:** Ingested {link_str} to `{location}` ({source_type}){" — " + extra if extra else ""}\n'
    
    existing = ''
    if os.path.exists(log_path):
        try:
            with open(log_path, encoding='utf-8') as f:
                existing = f.read()
        except Exception:
            existing = ''
    
    if not existing.strip():
        existing = '# 📋 Librarian Activity Log\n\nAutomatically maintained by the AI Librarian.\n\n---\n\n'
    
    atomic_write(log_path, existing + entry)
