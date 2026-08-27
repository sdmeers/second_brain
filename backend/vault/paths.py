import os
import re
from backend.config import SUMMARIES_PATH, ATLAS_PATH, CONCEPTS_PATH

def get_atlas_themes() -> list[str]:
    """Reads the Atlas folder and returns a list of themes as wikilinks."""
    try:
        if not os.path.exists(ATLAS_PATH):
            return []
        files = os.listdir(ATLAS_PATH)
        return [f"[[{f[:-3]}]]" for f in files if f.endswith(".md")]
    except Exception as e:
        print(f"Error reading Atlas Themes: {e}")
        return []

def get_existing_concepts() -> list[str]:
    """Reads the Concepts folder and returns a list of concepts as wikilinks."""
    try:
        if not os.path.exists(CONCEPTS_PATH):
            return []
        files = os.listdir(CONCEPTS_PATH)
        return [f"[[{f[:-3]}]]" for f in files if f.endswith(".md")]
    except Exception as e:
        print(f"Error reading Existing Concepts: {e}")
        return []

def is_url_already_ingested(url: str) -> str | None:
    """
    Checks if the URL is already present in any of the existing summaries.
    Returns the title of the existing summary if found, else None.
    """
    if not os.path.exists(SUMMARIES_PATH):
        return None
        
    def clean_u(u: str) -> str:
        u = u.strip().lower()
        if u.startswith("http://") or u.startswith("https://"):
            u = u.split("?")[0].split("#")[0].rstrip("/")
        return u
        
    target_url = clean_u(url)
    
    try:
        for filename in os.listdir(SUMMARIES_PATH):
            if not filename.endswith(".md"):
                continue
            file_path = os.path.join(SUMMARIES_PATH, filename)
            try:
                with open(file_path, encoding="utf-8") as f:
                    frontmatter_lines = []
                    for _ in range(30):
                        line = f.readline()
                        if not line:
                            break
                        frontmatter_lines.append(line)
                    
                    frontmatter_text = "".join(frontmatter_lines)
                    match = re.search(r'^url:\s*["\']?(.*?)["\']?\s*$', frontmatter_text, re.MULTILINE)
                    if match:
                        existing_url = clean_u(match.group(1))
                        if existing_url == target_url:
                            return filename[:-3]  # Return title without .md
            except Exception as e:
                print(f"Error reading {file_path} for duplicate check: {e}")
    except Exception as e:
        print(f"Error listing summaries directory: {e}")
        
    return None
