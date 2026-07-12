import os
import re
from datetime import datetime
from backend.config import OBSIDIAN_VAULT_PATH
from backend.utils.gemini import call_gemini_with_retry
from backend.config import GEMINI_MODEL
from google.genai import types
from pydantic import BaseModel, Field
from backend.utils.sanitize import sanitize_filename

class ProjectMatchResult(BaseModel):
    matched_project: str = Field(description="The exact name of the project folder matched, or 'None'.")
    note_title: str = Field(description="A clean, descriptive title for this note (3 to 6 words).")
    reasoning: str = Field(description="Brief explanation of the match.")

async def process_project_item(content_text: str, source_info: str, category: str = None, raw_file_path: str = None, matched_project: str = None, matched_title: str = None) -> dict:
    """Classifies a project-related note, matches it to an active project folder, and saves it there under the correct subfolder."""
    projects_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
    
    # 1. Resolve list of active projects from the filesystem
    active_projects = []
    if os.path.exists(projects_dir):
        active_projects = [
            d for d in os.listdir(projects_dir)
            if os.path.isdir(os.path.join(projects_dir, d)) and not d.startswith(".")
        ]
        
    print(f"Project Router: Detected active projects: {active_projects}")
    
    if matched_project and matched_title:
        match = ProjectMatchResult(
            matched_project=matched_project,
            note_title=matched_title,
            reasoning="Pre-matched by watcher router"
        )
    elif not active_projects:
        # Fallback to Second Brain project or root of Projects if none exists
        default_proj = "Second Brain"
        os.makedirs(os.path.join(projects_dir, default_proj), exist_ok=True)
        active_projects = [default_proj]

    # 2. Use Gemini to find the matching project if not pre-matched
    if not (matched_project and matched_title):
        system_instruction = f"""You are a personal information librarian.
Analyze the incoming note and map it to exactly ONE of the active projects list: {active_projects}.

If the note is clearly about one of these projects (e.g. mentions coding tasks for 'VibeCook', RAID/Dstl work for 'Task Force RAID', or second brain/sync updates for 'Second Brain'), return that project name.
If it does not fit any active project, return 'None'.

Also, generate a clean, brief note title (3 to 6 words) summarizing the content. Do not use quotes or special characters in the title.
"""
        
        try:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=ProjectMatchResult,
            )
            
            response = await call_gemini_with_retry(
                model=GEMINI_MODEL,
                contents=f"Map this note to a project:\n\n{content_text}",
                config=config
            )
            
            match = ProjectMatchResult.model_validate_json(response.text.strip())
        except Exception as e:
            print(f"Project Router: Error matching project via Gemini: {e}")
            match = ProjectMatchResult(
                matched_project="Second Brain",
                note_title="Inbox Project Update",
                reasoning=f"Fallback due to error: {e}"
            )

    # 3. Determine the correct subfolder under the project directory based on category
    target_project = match.matched_project if match.matched_project in active_projects else "Second Brain"
    subfolder_name = "Notes"
    if category == "meeting_transcript":
        subfolder_name = "Meetings"
    elif category == "idea":
        subfolder_name = "Notes"
    elif category in ["deliverable", "output"]:
        subfolder_name = "Outputs"
        
    note_dir = os.path.join(projects_dir, target_project, subfolder_name)
    os.makedirs(note_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_prefix = datetime.now().strftime("%Y-%m-%d")
    
    safe_title = sanitize_filename(match.note_title)
    if category == "meeting_transcript":
        file_name = f"{date_prefix}_{safe_title}"
    else:
        file_name = safe_title
        
    file_path = os.path.join(note_dir, f"{file_name}.md")
    
    # Check for name collisions
    counter = 1
    base_file_path = file_path
    while os.path.exists(file_path):
        file_path = base_file_path.replace(".md", f"_{counter}.md")
        counter += 1
        
    # Copy raw source file if provided (e.g. audio, pdf, docx, txt)
    if raw_file_path and os.path.exists(raw_file_path):
        try:
            import shutil
            _, ext = os.path.splitext(raw_file_path)
            raw_dest_name = f"{os.path.basename(file_path).rsplit('.', 1)[0]}_raw{ext}"
            raw_dest_path = os.path.join(note_dir, raw_dest_name)
            shutil.copy2(raw_file_path, raw_dest_path)
            print(f"Project Router: Copied original source file to {raw_dest_path}")
            source_link = f"[[{raw_dest_name}]]"
        except Exception as copy_e:
            print(f"Project Router: Failed to copy raw source file: {copy_e}")
            source_link = source_info
    else:
        source_link = source_info
        
    # Save the note with simple, clean metadata
    file_content = f"""---
title: "{match.note_title}"
project: "{target_project}"
date_created: "{timestamp}"
source: "{source_link}"
tags: [project, {target_project.lower().replace(' ', '_')}]
---
# {match.note_title}

*Created on {timestamp} via {source_link}*

---

{content_text}
"""
    
    try:
        from backend.vault.writer import atomic_write
        atomic_write(file_path, file_content)
        
        # Log to _CHANGELOG.md
        changelog_file = os.path.join(OBSIDIAN_VAULT_PATH, "00_System", "_CHANGELOG.md")
        if os.path.exists(changelog_file):
            rel_path = os.path.relpath(file_path, OBSIDIAN_VAULT_PATH)
            date_str = datetime.now().strftime("%Y-%m-%d")
            try:
                with open(changelog_file, "r", encoding="utf-8") as f:
                    old_changelog = f.read()
            except Exception:
                old_changelog = ""
            new_changelog = old_changelog + f"- {date_str}: Created project note [[{rel_path}]]\n"
            atomic_write(changelog_file, new_changelog)
                
        print(f"Project Router: Saved note to {file_path}")
        return {
            "status": "success",
            "project": target_project,
            "title": match.note_title,
            "file_path": file_path
        }
    except Exception as e:
        print(f"Project Router: Error saving note to project: {e}")
        raise e
