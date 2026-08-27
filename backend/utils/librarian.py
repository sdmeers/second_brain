import os
import json
import yaml
from pydantic import BaseModel, Field

from backend.rag.embeddings import get_embeddings_batch
from backend.rag.vectorstore import get_vector_db_connection
from backend.utils.gemini import call_gemini_with_retry
from backend.config import GEMINI_MODEL, WIKILINK_SIMILARITY_THRESHOLD
from backend.utils.sanitize import sanitize_filename
from backend.vault.writer import atomic_write

class WikilinkResult(BaseModel):
    related: list[str] = Field(description='List of genuinely related note titles to link as [[Wikilinks]].')

async def find_related_candidates(note_text: str) -> dict:
    embeddings = await get_embeddings_batch([note_text])
    if not embeddings:
        return {'project': None, 'area': None, 'concepts': []}
    embedding = embeddings[0]

    conn = get_vector_db_connection()
    cursor = conn.cursor()
    
    query = """
    SELECT c.file_path, c.chunk_index, c.text_content, v.distance
    FROM vss_brain_chunks v
    JOIN brain_chunks c ON c.id = v.rowid
    WHERE vss_search(v.vector, vss_search_params(?, ?))
    ORDER BY v.distance ASC
    """
    
    cursor.execute(query, (json.dumps(embedding), 200))
    results = cursor.fetchall()
    
    projects = {}
    areas = {}
    concepts = []
    
    for row in results:
        file_path, chunk_index, text_content, distance = row
        
        if distance > WIKILINK_SIMILARITY_THRESHOLD:
            continue
            
        if file_path.startswith('02_Projects/'):
            parts = file_path.split('/')
            if len(parts) >= 2:
                folder_name = parts[1]
                if folder_name not in projects:
                    projects[folder_name] = []
                projects[folder_name].append(distance)
        elif file_path.startswith('03_Areas/'):
            parts = file_path.split('/')
            if len(parts) >= 2:
                folder_name = parts[1]
                if folder_name not in areas:
                    areas[folder_name] = []
                areas[folder_name].append(distance)
        elif file_path.startswith('04_Resources/'):
            filename = file_path.split('/')[-1]
            title = filename.replace('.md', '')
            if title not in [c[0] for c in concepts]:
                concepts.append((title, distance))
                
    best_project = None
    best_project_dist = float('inf')
    for proj, dists in projects.items():
        avg_dist = sum(dists) / len(dists)
        if avg_dist < best_project_dist:
            best_project_dist = avg_dist
            best_project = proj
            
    best_area = None
    best_area_dist = float('inf')
    for area, dists in areas.items():
        avg_dist = sum(dists) / len(dists)
        if avg_dist < best_area_dist:
            best_area_dist = avg_dist
            best_area = area
            
    top_concepts = [c[0] for c in concepts[:5]]
    
    return {
        'project': best_project,
        'area': best_area,
        'concepts': top_concepts
    }

async def enrich_note_metadata(saved_file_path: str, note_text: str, classification_category: str, is_research: bool) -> None:
    if is_research:
        return
        
    candidates = await find_related_candidates(note_text)
    
    candidate_titles = []
    if candidates['project']:
        candidate_titles.append(candidates['project'])
    if candidates['area']:
        candidate_titles.append(candidates['area'])
    candidate_titles.extend(candidates['concepts'])
    
    candidate_titles = candidate_titles[:7]
    
    wikilinks = []
    if candidate_titles:
        prompt = f"Given this note content, which of these existing notes are genuinely conceptually related? Only include strong, meaningful connections. Return an empty list if none are truly relevant. Candidates: {candidate_titles}\n\nNote content: {note_text}"
        
        try:
            from google.genai import types
            response = await call_gemini_with_retry(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type='application/json',
                    response_schema=WikilinkResult
                )
            )
            
            response_data = json.loads(response.text)
            wikilinks = response_data.get('related', [])
        except Exception as e:
            print(f"Librarian: Failed to call Gemini for wikilinks: {e}")
            
    type_tag = "#type/summary"
    if classification_category == "idea":
        type_tag = "#type/idea"
    elif classification_category == "task":
        type_tag = "#type/task"
    elif classification_category == "meeting_transcript":
        type_tag = "#type/meeting_transcript"
    elif classification_category in ["reference", "url_capture"]:
        type_tag = "#type/reference"
    elif classification_category == "voice_dump":
        type_tag = "#type/voice_note"
    elif classification_category == "deep_research":
        type_tag = "#type/concept"
        
    try:
        with open(saved_file_path, encoding="utf-8") as f:
            content = f.read()
            
        parts = content.split("---")
        if len(parts) >= 3 and content.startswith("---"):
            frontmatter_str = parts[1]
            body = "---".join(parts[2:])
            
            try:
                frontmatter = yaml.safe_load(frontmatter_str) or {}
            except yaml.YAMLError:
                frontmatter = {}
                
            tags = frontmatter.get("tags", [])
            if not isinstance(tags, list):
                tags = [tags] if tags else []
                
            if type_tag not in tags:
                tags.append(type_tag)
            if "status/to-review" not in tags:
                tags.append("status/to-review")
                
            frontmatter["tags"] = tags
            
            related = frontmatter.get("related", [])
            if not isinstance(related, list):
                related = [related] if related else []
                
            for link in wikilinks:
                formatted_link = f"[[{link}]]"
                if formatted_link not in related:
                    related.append(formatted_link)
                    
            if related:
                frontmatter["related"] = related
                
            new_frontmatter_str = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
            new_content = f"---\n{new_frontmatter_str}---\n{body.lstrip()}"
            
            atomic_write(saved_file_path, new_content)
            print(f"Librarian: Successfully enriched note metadata for {saved_file_path}")
            
    except Exception as e:
        print(f"Librarian: Failed to process frontmatter for {saved_file_path}: {e}")

ALLOWED_PARA_CATEGORIES = {"02_Projects", "03_Areas", "04_Resources"}


class PARADestination(BaseModel):
    category: str = Field(description="Must be one of: '02_Projects', '03_Areas', '04_Resources'")
    subfolder: str = Field(
        description="The exact target subfolder. "
                    "For 02_Projects: 'Personal/<ProjectName>' or 'Professional/<ProjectName>'. "
                    "For 03_Areas: 'Family', 'Health', 'House', 'Home_Server', 'Work', 'Finance_and_legal', 'Coding', 'Cookery', 'Learning', 'Running', 'Music', 'Reading'. "
                    "For 04_Resources: 'Articles_and_News', 'Books_and_Media', 'Cheatsheets_and_Guides', 'Manuals_and_Equipment', 'Research_Brain'."
    )
    reasoning: str = Field(description="Brief explanation of why this PARA location was chosen.")

PARA_SYSTEM_PROMPT = """You are the AI Librarian for Steven's Second Brain system, adhering strictly to Tiago Forte's P.A.R.A Method.

Determine the exact target P.A.R.A folder for the given note or document based on these strict guidelines:

1. '02_Projects': Active short-term efforts with a specific goal and deadline (e.g. Personal/Garage, Personal/Doors, Personal/Second Brain, Professional/TF RAID).

2. '03_Areas': Personal operational roles, responsibilities, standards, and personal records that Steven personally maintains on an ongoing basis.
   - Allowed subfolders: 'Family', 'Health', 'House', 'Home_Server', 'Work', 'Finance_and_legal', 'Coding', 'Cookery', 'Learning', 'Running', 'Music', 'Reading'.
   - Examples: Personal driving license, family packing lists, NHS records, home server systemd configs, house invoices, personal reading lists (Books to read.md).
   - CRITICAL: Do NOT file external book summaries, research papers, software cheat sheets, newsletters, or equipment manuals under 03_Areas!

3. '04_Resources': External reference material, learning resources, manuals, articles, and research.
   - 'Manuals_and_Equipment': Equipment manuals, appliance guides, hardware specs, datasheets (e.g. Kärcher pressure washer, appliance guides).
   - 'Articles_and_News': Web articles, newsletters, blog posts, tech news clippings.
   - 'Cheatsheets_and_Guides': Software cheat sheets, tool reference guides, syntax sheets (e.g. Obsidian cheatsheet, git guides).
   - 'Books_and_Media': Book summaries, novel reviews, video transcripts, podcast notes (e.g. The Expanse, Good Strategy Bad Strategy summary).
   - 'Research_Brain': Academic papers, scientific studies, AI governance whitepapers, deep technical research.
"""

async def determine_para_destination(title: str, text: str, is_pdf: bool = False) -> tuple[str, str]:
    """Determines the exact vault directory for a given title and content using P.A.R.A rules."""
    from backend.config import (
        OBSIDIAN_VAULT_PATH, ARTICLES_AND_NEWS_PATH, ARTICLES_SOURCES_PATH,
        MANUALS_AND_EQUIPMENT_PATH, MANUALS_SOURCES_PATH, BOOKS_AND_MEDIA_PATH,
        CHEATSHEETS_PATH
    )
    
    # Fast path for known types
    lower_title = title.lower()
    if is_pdf or "manual" in lower_title or "user guide" in lower_title or "specifications" in lower_title:
        return MANUALS_AND_EQUIPMENT_PATH, MANUALS_SOURCES_PATH
    if "cheatsheet" in lower_title or "cheat sheet" in lower_title or "quick reference" in lower_title:
        return CHEATSHEETS_PATH, os.path.join(CHEATSHEETS_PATH, "Sources")
    if "summary" in lower_title and ("book" in lower_title or "novel" in lower_title or "edition" in lower_title):
        return BOOKS_AND_MEDIA_PATH, os.path.join(BOOKS_AND_MEDIA_PATH, "Sources")

    try:
        from google.genai import types
        config = types.GenerateContentConfig(
            system_instruction=PARA_SYSTEM_PROMPT,
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=PARADestination,
        )
        prompt = f"Title: {title}\n\nContent snippet:\n{text[:3000]}"
        response = await call_gemini_with_retry(
            model=GEMINI_MODEL,
            contents=prompt,
            config=config
        )
        dest = PARADestination.model_validate_json(response.text.strip())

        # The category and subfolder come from the model, and are used to create
        # directories: validate them against the allowlist and confine the result to
        # the vault rather than trusting the response.
        if dest.category not in ALLOWED_PARA_CATEGORIES:
            raise ValueError(f"Model returned disallowed PARA category: {dest.category!r}")

        subfolder = dest.subfolder.replace("\\", "/").strip("/")
        segments = [sanitize_filename(part, default="") for part in subfolder.split("/")]
        segments = [seg for seg in segments if seg and seg not in (".", "..")]
        if not segments or len(segments) > 2:
            raise ValueError(f"Model returned unusable PARA subfolder: {dest.subfolder!r}")

        target_dir = os.path.join(OBSIDIAN_VAULT_PATH, dest.category, *segments)

        vault_root = os.path.realpath(OBSIDIAN_VAULT_PATH)
        if os.path.commonpath([vault_root, os.path.realpath(target_dir)]) != vault_root:
            raise ValueError(f"Resolved PARA destination escapes the vault: {target_dir!r}")

        sources_dir = os.path.join(target_dir, "Sources")
        os.makedirs(sources_dir, exist_ok=True)
        return target_dir, sources_dir
    except Exception as e:
        print(f"Librarian: Error classifying PARA destination: {e}")
        return ARTICLES_AND_NEWS_PATH, ARTICLES_SOURCES_PATH

