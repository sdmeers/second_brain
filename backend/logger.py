import os
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=dotenv_path)

OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")

def log_action(action_type: str, details: str, concepts: list[str] = None):
    """
    Appends a log entry to Vault/00 System/log.md grouped under a daily collapsible header.
    Format:
    ## YYYY-MM-DD
    - **HH:MM** - **{emoji} {action_type}** | {details} | Concepts: [[Concept 1]], [[Concept 2]]
    """
    if not OBSIDIAN_VAULT_PATH:
        print("Logger: OBSIDIAN_VAULT_PATH not set, skipping log.")
        return
        
    log_file_path = os.path.join(OBSIDIAN_VAULT_PATH, "00 System", "log.md")
    
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    
    # Ensure parent directory exists
    try:
        os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
    except Exception as e:
        print(f"Logger Error: Could not create directory for log path {log_file_path}: {e}")
        return
        
    # Check if we need to write the day header
    header_str = f"## {date_str}\n"
    
    # Emoji mappings for standard action types
    emoji_map = {
        "Ingested": "📥 Ingested",
        "Re-Ingested": "📥 Re-Ingested",
        "Book Ingested": "📚 Book Ingested",
        "Concept Updated": "💡 Concept Updated",
        "Deduplicate": "🔄 Deduplicate",
        "Lint": "🧹 Lint"
    }
    action_display = emoji_map.get(action_type, action_type)
    
    concepts_str = ""
    if concepts:
        clean_concepts = []
        for c in concepts:
            c_clean = str(c).replace('\n', '').replace('[', '').replace(']', '').strip()
            if c_clean:
                clean_concepts.append(f"[[{c_clean}]]")
        if clean_concepts:
            concepts_str = f" | Concepts: {', '.join(clean_concepts)}"
            
    log_entry = f"- **{time_str}** - **{action_display}** | {details}{concepts_str}\n"
    
    try:
        # If file doesn't exist or is empty, write fresh
        if not os.path.exists(log_file_path) or os.path.getsize(log_file_path) == 0:
            with open(log_file_path, "w", encoding="utf-8") as f:
                f.write(f"{header_str}{log_entry}")
            return
            
        # Read existing content
        with open(log_file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        # If today's header is already in the file, insert directly under it
        if header_str in content:
            idx = content.find(header_str)
            insert_pos = idx + len(header_str)
            new_content = content[:insert_pos] + log_entry + content[insert_pos:]
        else:
            # New day: Prepend to the top of the file with separating spacing
            new_content = f"{header_str}{log_entry}\n{content}"
            
        # Write back the updated content
        with open(log_file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
            
    except Exception as e:
        print(f"Logger Error: Could not write to {log_file_path}: {e}")


