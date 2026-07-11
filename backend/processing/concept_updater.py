import os
from backend.config import ATLAS_PATH, CONCEPTS_PATH, GEMINI_MODEL
from backend.utils.sanitize import sanitize_filename
from backend.utils.gemini import call_gemini_with_retry
from backend.utils.logger import log_action

async def update_concept_page(concept_name: str, new_summary: str, source_title: str):
    """Updates an existing concept page or creates a new one, prioritizing Atlas themes."""
    clean_concept = sanitize_filename(concept_name.replace('[[', '').replace(']]', ''))
    
    atlas_path = os.path.join(ATLAS_PATH, f"{clean_concept}.md")
    concept_path = os.path.join(CONCEPTS_PATH, f"{clean_concept}.md")
    
    # Prioritize Atlas over Concepts
    target_path = atlas_path if os.path.exists(atlas_path) else concept_path
    is_atlas = target_path == atlas_path
    
    if os.path.exists(target_path):
        print(f"Backend [Concept]: Updating existing {'atlas' if is_atlas else 'concept'} [[{clean_concept}]]")
        with open(target_path, "r", encoding="utf-8") as f:
            existing_content = f.read()
            
        preserve_msg = "\n\nCRITICAL: This is an Atlas Map of Content. DO NOT modify the '# Map of Content' section or any Dataview queries. Only update the definition and discussion sections above it." if is_atlas else ""
            
        prompt = f"""Here is the existing {'atlas theme' if is_atlas else 'concept'} page for '{clean_concept}':
{existing_content}

Here is a new source summary that references it:
{new_summary}

Please update the page to integrate any new information, note contradictions, and add a backlink to the new source ([[{source_title}]]). Return the complete updated Markdown.{preserve_msg}
"""
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
        prompt = f"""Generate a baseline definition and concept page for '{clean_concept}' based on its usage in this new source summary:
{new_summary}
 
Please include a backlink to the source: [[{source_title}]]. Return the Markdown.
"""
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
