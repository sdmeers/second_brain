import os
import re
from backend.config import OBSIDIAN_VAULT_PATH, SUMMARIES_PATH, ATLAS_PATH, CONCEPTS_PATH
from backend.utils.urls import normalize_url

def list_active_projects() -> dict[str, str]:
    """Active project name -> path relative to 02_Projects (e.g. 'Garage' -> 'Personal/Garage').

    Projects are nested one level under Personal/ and Professional/; listing 02_Projects
    directly only ever returned those two category folders, so no note could match a real
    project.
    """
    from backend.vault.tasks import iter_projects, projects_root
    root = projects_root()
    return {p.name: os.path.relpath(os.path.dirname(p.path), root) for p in iter_projects()}


def list_areas() -> dict[str, str]:
    """Area folder name -> path relative to 03_Areas, top level plus one nested level
    (e.g. 'Car' -> 'Car', 'Dstl' -> 'Work/Dstl'). Read live so prompts never drift from
    the vault the way the old hard-coded Area lists did."""
    areas_dir = os.path.join(OBSIDIAN_VAULT_PATH, "03_Areas")
    areas = {}
    if not os.path.isdir(areas_dir):
        return areas
    for top in sorted(os.listdir(areas_dir)):
        top_path = os.path.join(areas_dir, top)
        if top.startswith(".") or not os.path.isdir(top_path):
            continue
        areas[top] = top
        for sub in sorted(os.listdir(top_path)):
            if not sub.startswith(".") and sub != "Sources" and os.path.isdir(os.path.join(top_path, sub)):
                areas.setdefault(sub, f"{top}/{sub}")
    return areas


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

    target_url = normalize_url(url)
    if not target_url:
        return None

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
                        existing_url = normalize_url(match.group(1))
                        if existing_url == target_url:
                            return filename[:-3]  # Return title without .md
            except Exception as e:
                print(f"Error reading {file_path} for duplicate check: {e}")
    except Exception as e:
        print(f"Error listing summaries directory: {e}")
        
    return None
