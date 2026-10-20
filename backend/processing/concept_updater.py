import os
from backend.config import ATLAS_PATH, CONCEPTS_PATH, GEMINI_MODEL
from backend.utils.sanitize import sanitize_filename
from backend.utils.gemini import call_gemini_with_retry
from backend.utils.logger import log_action
from backend.prompts import load_prompt

def resolve_concept_path(directory: str, clean_concept: str) -> str:
    """Resolves target concept filepath, matching existing files case-insensitively."""
    target_filename = f"{clean_concept}.md"
    if os.path.exists(directory):
        for existing in os.listdir(directory):
            if existing.lower() == target_filename.lower():
                return os.path.join(directory, existing)
    return os.path.join(directory, target_filename)

async def update_concept_page(concept_name: str, new_summary: str, source_title: str):
    """Updates an existing concept page or creates a new one, prioritizing Atlas themes."""
    clean_concept = sanitize_filename(concept_name.replace('[[', '').replace(']]', ''))
    
    atlas_path = resolve_concept_path(ATLAS_PATH, clean_concept)
    concept_path = resolve_concept_path(CONCEPTS_PATH, clean_concept)
    
    # Prioritize Atlas over Concepts
    target_path = atlas_path if os.path.exists(atlas_path) else concept_path
    is_atlas = target_path.startswith(ATLAS_PATH) and os.path.exists(target_path)
    
    if os.path.exists(target_path):
        print(f"Backend [Concept]: Updating existing {'atlas' if is_atlas else 'concept'} [[{clean_concept}]]")
        with open(target_path, encoding="utf-8") as f:
            existing_content = f.read()
            
        preserve_msg = "\n\n" + load_prompt("concept_atlas_guard") if is_atlas else ""
            
        prompt = load_prompt(
            "concept_update",
            page_kind="atlas theme" if is_atlas else "concept", concept=clean_concept,
            existing_content=existing_content, new_summary=new_summary,
            source_title=source_title, atlas_guard=preserve_msg,
        )
        try:
            response = await call_gemini_with_retry(
                model=GEMINI_MODEL,
                contents=prompt,
            )
            updated_content = response.text.strip()
            # Safety check: if is_atlas, ensure we didn't lose the dataview
            if is_atlas and "```dataview" not in updated_content and "```dataview" in existing_content:
                print(f"Backend [Concept]: WARNING - Gemini stripped dataview from [[{clean_concept}]]. Attempting to restore.")
                # Simple restoration: append the old dataview section if it's missing
                if "# Map of Content" in existing_content:
                    moc_part = existing_content.split("# Map of Content")[-1]
                    if "# Map of Content" not in updated_content:
                        updated_content += "\n\n# Map of Content" + moc_part
            
            from backend.vault.writer import atomic_write
            atomic_write(target_path, updated_content)
            log_action("Concept Updated", f'Updated [[{clean_concept}]] with context from "{source_title}"')
        except Exception as e:
            print(f"Backend [Concept]: Failed to update [[{clean_concept}]]: {e}")
    else:
        print(f"Backend [Concept]: Creating new concept [[{clean_concept}]]")
        prompt = load_prompt("concept_create", concept=clean_concept, new_summary=new_summary, source_title=source_title)
        try:
            response = await call_gemini_with_retry(
                model=GEMINI_MODEL,
                contents=prompt,
            )
            new_content = response.text.strip()
            from backend.vault.writer import atomic_write
            atomic_write(target_path, new_content)
        except Exception as e:
            print(f"Backend [Concept]: Failed to create concept [[{clean_concept}]]: {e}")
