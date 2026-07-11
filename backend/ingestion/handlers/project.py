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

async def process_project_item(content_text: str, source_info: str) -> dict:
    """Classifies a project-related note, matches it to an active project folder, and saves it there."""
    projects_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
    
    # 1. Resolve list of active projects from the filesystem
    active_projects = []
    if os.path.exists(projects_dir):
        active_projects = [
            d for d in os.listdir(projects_dir)
            if os.path.isdir(os.path.join(projects_dir, d)) and not d.startswith(".")
        ]
        
    print(f"Project Router: Detected active projects: {active_projects}")
    
    if not active_projects:
        # Fallback to Second Brain project or root of Projects if none exists
        default_proj = "Second Brain"
        os.makedirs(os.path.join(projects_dir, default_proj), exist_ok=True)
        active_projects = [default_proj]

    # 2. Use Gemini to find the matching project
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

    # 3. Save the note to the project directory
    target_project = match.matched_project if match.matched_project in active_projects else "Second Brain"
    note_dir = os.path.join(projects_dir, target_project)
    os.makedirs(note_dir, exist_ok=True)
    
    safe_title = sanitize_filename(match.note_title)
    file_path = os.path.join(note_dir, f"{safe_title}.md")
    
    # Check for name collisions
    counter = 1
    base_file_path = file_path
    while os.path.exists(file_path):
        file_path = base_file_path.replace(".md", f"_{counter}.md")
        counter += 1
        
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Save the note with simple, clean metadata
    file_content = f"""---
title: "{match.note_title}"
project: "{target_project}"
date_created: "{timestamp}"
source: "{source_info}"
tags: [project, {target_project.lower().replace(' ', '_')}]
---
# {match.note_title}

*Created on {timestamp} via {source_info}*

---

{content_text}
"""
    
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(file_content)
        
        # Log to _CHANGELOG.md
        # We can write the changelog entry directly or use log_action
        changelog_file = os.path.join(OBSIDIAN_VAULT_PATH, "00_System", "_CHANGELOG.md")
        if os.path.exists(changelog_file):
            rel_path = os.path.relpath(file_path, OBSIDIAN_VAULT_PATH)
            date_str = datetime.now().strftime("%Y-%m-%d")
            with open(changelog_file, "a", encoding="utf-8") as f:
                f.write(f"- {date_str}: Created project note [[{rel_path}]]\n")
                
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
