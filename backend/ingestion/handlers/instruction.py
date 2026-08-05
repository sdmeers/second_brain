import os
import re
import shutil
import difflib
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from google.genai import types

from backend.config import GEMINI_MODEL, OBSIDIAN_VAULT_PATH
from backend.utils.gemini import call_gemini_with_retry
from backend.vault.writer import atomic_write
from backend.utils.sanitize import sanitize_filename
from backend.logger import log_action

TASKS_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "00_System", "Tasks.md")
COMPLETED_TASKS_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "00_System", "Completed_Tasks.md")
TRASH_DIR = os.path.join(OBSIDIAN_VAULT_PATH, "05_Archive", "Trash")

class TargetSelector(BaseModel):
    query: str = Field(description="Search text, title pattern, or phrase used to locate target file(s) or line(s).")
    scope: Optional[str] = Field(default="all", description="Scope of search: 'tasks', 'notes', 'vault', or specific directory path.")

class VaultAction(BaseModel):
    action_type: str = Field(
        description="One of: 'delete_task', 'complete_task', 'update_task', 'add_task', 'convert_task_to_note', "
                    "'create_project', 'move_note', 'rename_note', 'delete_note', 'reclassify_note', 'merge_notes', "
                    "'append_to_note', 'update_frontmatter', 'add_tag', 'remove_tag', 'reindex', 'run_cleaner', 'get_sitrep'."
    )
    target: TargetSelector
    destination: Optional[str] = Field(default="", description="Destination path or folder (e.g. '03_Areas/Reading', '02_Projects/Hermes', '05_Archive').")
    new_content: Optional[str] = Field(default="", description="Text to append, new title, or replacement content.")
    new_metadata_json: Optional[str] = Field(default="", description="Optional JSON string of metadata key-value pairs (e.g. '{\"status\": \"active\"}').")
    reasoning: str = Field(description="Brief explanation of what this specific action intends to perform.")

class InstructionPlan(BaseModel):
    summary: str = Field(description="Human-readable summary of the actions to be executed.")
    actions: List[VaultAction] = Field(description="List of individual vault actions to perform.")

SYSTEM_INSTRUCTION = """You are an expert agentic command interpreter for Steven's Second Brain system.
Analyze the user's natural language instruction and translate it into a structured InstructionPlan consisting of one or more discrete VaultAction steps.

AVAILABLE ACTION PRIMITIVES:
- 'get_sitrep': Generate and return a real-time summary / SITREP of tasks, recent activity, and ideas (triggered by queries like "Give me a SITREP", "What is the current summary?", "What happened this week?", "Status report").
- 'create_project': Create a new project folder structure under '02_Projects/Personal/' or '02_Projects/Professional/' containing master project note (triggered by instructions like "create a new project called Garage").
- 'add_task': Append a new pending task line to Tasks.md (triggered by instructions like "Add a task to paint doors", "Remind me to call John").
- 'delete_task': Remove matching pending task line(s) from Tasks.md.
- 'complete_task': Mark matching task line(s) as '- [x]' in Tasks.md and archive them to Completed_Tasks.md.
- 'update_task': Edit task text, deadline, or priority of matching task line(s) in Tasks.md.
- 'convert_task_to_note': Remove matching task line(s) from Tasks.md and create a dedicated note (e.g., Book, Project, or Area note) in the target destination folder (default: '03_Areas/Reading' for books).
- 'move_note': Relocate a note file from its current location to a new folder (e.g., '02_Projects/...', '03_Areas/...', '05_Archive').
- 'rename_note': Rename a note file.
- 'delete_note': Soft-delete a note file by moving it to '05_Archive/Trash/'.
- 'reclassify_note': Change a note's type/metadata and move it to the corresponding destination directory.
- 'merge_notes': Merge content from source note(s) into a target note.
- 'append_to_note': Append new text or log entries to an existing note.
- 'update_frontmatter': Modify YAML metadata fields in a note.
- 'add_tag' / 'remove_tag': Add or remove tags from notes/tasks.
- 'reindex': Trigger re-indexing of vector embeddings.
- 'run_cleaner': Trigger vault cleaner run.

RULES:
1. For instructions asking to "create a new project called X", use 'create_project' with target query "X".
2. For instructions asking to "add a task X" or "remind me to X", use 'add_task' with target query "X".
3. For queries asking for a summary or SITREP (e.g. "What is the current summary?", "Give me a SITREP"), use 'get_sitrep'.
4. For instructions asking to "reclassify task X as a book", use 'convert_task_to_note' with target query "X" and destination "03_Areas/Reading".
5. Break multi-part instructions into separate actions in the plan.
"""

async def parse_instruction_plan(instruction_text: str) -> InstructionPlan:
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.1,
        response_mime_type="application/json",
        response_schema=InstructionPlan,
    )
    
    response = await call_gemini_with_retry(
        model=GEMINI_MODEL,
        contents=f"Parse this user instruction into a structured plan:\n\n{instruction_text}",
        config=config
    )
    
    return InstructionPlan.model_validate_json(response.text.strip())

def find_matching_tasks(query: str, lines: list[str]) -> list[tuple[int, str]]:
    """Finds line indices and task strings matching a search query using fuzzy, token prefix, and substring matching."""
    matches = []
    query_clean = re.sub(r'[\'\"\[\]]', '', query.lower().strip())
    query_words = [w for w in re.findall(r'\w+', query_clean) if len(w) > 3]
    
    for i, line in enumerate(lines):
        if line.strip().startswith("- [") or line.strip().startswith("- [x]"):
            line_clean = re.sub(r'[\'\"\[\]]', '', line.lower().strip())
            
            # 1. Direct substring match
            if query_clean in line_clean or line_clean in query_clean:
                matches.append((i, line))
                continue
                
            # 2. Token overlap or prefix matching (e.g. Anatheman -> Anathem)
            line_words = [w for w in re.findall(r'\w+', line_clean) if len(w) > 3]
            word_match = False
            for qw in query_words:
                for lw in line_words:
                    if qw in lw or lw in qw or qw[:5] == lw[:5] or difflib.SequenceMatcher(None, qw, lw).ratio() > 0.65:
                        word_match = True
                        break
                if word_match:
                    break
                    
            if word_match:
                matches.append((i, line))
                continue
                
            # 3. Overall ratio match
            if difflib.SequenceMatcher(None, query_clean, line_clean).ratio() > 0.45:
                matches.append((i, line))
                
    return matches

def get_most_recent_user_note() -> str | None:
    """Finds the relative path of the most recently created or modified user note in the vault."""
    latest_file = None
    latest_mtime = 0
    
    for root, _, files in os.walk(OBSIDIAN_VAULT_PATH):
        if any(skip in root for skip in [".git", "00_System/Internal", "00_System/Logs", "01_Inbox", "05_Archive/Trash"]):
            continue
        for file in files:
            if file.endswith(".md") and not file.startswith(".") and file not in ["_INDEX.md", "Librarian_Activity.md", "Tasks.md", "Completed_Tasks.md", "Daily_Digest.md", "Completed.md", "Failed.md", "Inbox.md"]:
                full_path = os.path.join(root, file)
                try:
                    mtime = os.path.getmtime(full_path)
                    if mtime > latest_mtime:
                        latest_mtime = mtime
                        latest_file = os.path.relpath(full_path, OBSIDIAN_VAULT_PATH)
                except Exception:
                    pass
    return latest_file

def find_matching_notes(query: str) -> list[str]:
    """Finds relative note paths matching a title or query across the vault with strict target matching."""
    query_clean = re.sub(r'[\'\"\[\]]', '', query.lower().strip())
    
    # 1. Context Reference Resolution ("previous record", "last note", "latest capture", "latest_note")
    relative_triggers = ["previous record", "previous note", "last record", "last note", "latest note", "latest capture", "last file", "previous file", "latest_note", "that file", "that note", "that record"]
    if any(trigger in query_clean for trigger in relative_triggers):
        recent_note = get_most_recent_user_note()
        if recent_note:
            print(f"Backend [Instruction Handler]: Resolved relative reference '{query}' -> '{recent_note}'")
            return [recent_note]
            
    matches = []
    query_words = [w for w in re.findall(r'\w+', query_clean) if len(w) > 3]
    
    # 2. Strict matching against vault filenames
    for root, _, files in os.walk(OBSIDIAN_VAULT_PATH):
        if any(skip in root for skip in [".git", "00_System/Internal", "05_Archive/Trash"]):
            continue
        for file in files:
            if file.endswith(".md"):
                file_name_clean = re.sub(r'[\'\"\[\]]', '', file[:-3].lower())
                rel_path = os.path.relpath(os.path.join(root, file), OBSIDIAN_VAULT_PATH)
                
                # Direct match (require minimum word length for reverse substring match to avoid "rag" in "garage" false positives)
                if query_clean == file_name_clean or query_clean in file_name_clean:
                    matches.append(rel_path)
                elif len(file_name_clean) >= 4 and file_name_clean in query_clean:
                    matches.append(rel_path)
                elif query_words:
                    # Multi-word queries MUST match ALL query words in the target filename to prevent false bulk moves!
                    file_words = [w for w in re.findall(r'\w+', file_name_clean) if len(w) > 3]
                    if file_words and all(any(qw == fw or (len(qw) >= 4 and qw in fw) or (len(fw) >= 4 and fw in qw) or (len(qw) >= 5 and qw[:5] == fw[:5]) for fw in file_words) for qw in query_words):
                        matches.append(rel_path)
                        
    return matches

async def handle_task_action(action: VaultAction) -> str:
    """Executes task primitive operations (delete, complete, update, convert)."""
    if not os.path.exists(TASKS_PATH):
        return f"⚠️ Tasks file not found at {TASKS_PATH}"
        
    with open(TASKS_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    action_type = action.action_type
    
    if action_type == "add_task":
        task_desc = action.target.query.strip() or action.new_content.strip()
        if not task_desc:
            return "⚠️ Task description required for add_task."
            
        dest_tag = f" #{action.destination.strip()}" if action.destination else ""
        date_str = datetime.now().strftime("%Y-%m-%d")
        task_line = f"- [ ] {task_desc}{dest_tag} 📅 {date_str} (Captured via instruction on {date_str})\n"
        
        os.makedirs(os.path.dirname(TASKS_PATH), exist_ok=True)
        with open(TASKS_PATH, "a", encoding="utf-8") as tf:
            tf.write(task_line)
            
        log_action("Instruction Executed", f"Added task: {task_desc}")
        return f"✓ Added task to Tasks.md: '{task_desc}'."

    query = action.target.query.strip()
    matches = find_matching_tasks(query, lines)
    
    if not matches:
        # Cross-Fallback: Check if target exists as a note file in vault!
        matching_notes = find_matching_notes(query)
        if matching_notes:
            print(f"Backend [Instruction Handler]: Target task '{query}' not in Tasks.md, but matched note file in vault! Cross-routing to handle_note_file_action.")
            if action.action_type == "convert_task_to_note":
                action.action_type = "move_note"
            return await handle_note_file_action(action)
        return f"⚠️ Target Verification: No task or note matching '{query}' found in Tasks.md or vault."
        
    action_type = action.action_type
    
    if action_type == "delete_task":
        indices_to_remove = set(idx for idx, _ in matches)
        new_lines = [line for i, line in enumerate(lines) if i not in indices_to_remove]
        atomic_write(TASKS_PATH, "".join(new_lines))
        removed_summary = ", ".join([f"'{line.strip()}'" for _, line in matches])
        log_action("Instruction Executed", f"Deleted task(s): {removed_summary}")
        return f"✓ Deleted {len(matches)} task(s) from Tasks.md matching '{query}'."
        
    elif action_type == "complete_task":
        indices_to_remove = set(idx for idx, _ in matches)
        new_lines = [line for i, line in enumerate(lines) if i not in indices_to_remove]
        atomic_write(TASKS_PATH, "".join(new_lines))
        
        # Append to Completed_Tasks.md
        completed_date = datetime.now().strftime("%Y-%m-%d")
        os.makedirs(os.path.dirname(COMPLETED_TASKS_PATH), exist_ok=True)
        with open(COMPLETED_TASKS_PATH, "a", encoding="utf-8") as cf:
            for _, line in matches:
                clean_line = line.strip().replace("- [ ]", "- [x]").replace("- [x]", "- [x]")
                cf.write(f"{clean_line} [completed: {completed_date}]\n")
                
        return f"✓ Marked {len(matches)} task(s) as completed and archived."
        
    elif action_type == "convert_task_to_note":
        # Remove task from Tasks.md
        indices_to_remove = set(idx for idx, _ in matches)
        new_lines = [line for i, line in enumerate(lines) if i not in indices_to_remove]
        atomic_write(TASKS_PATH, "".join(new_lines))
        
        # Target destination folder
        dest_folder = action.destination.strip() or "03_Areas/Reading"
        dest_dir = os.path.join(OBSIDIAN_VAULT_PATH, dest_folder)
        os.makedirs(dest_dir, exist_ok=True)
        
        created_notes = []
        for _, line in matches:
            # Extract task description
            raw_text = re.sub(r'^- \[[ x]\]\s*', '', line.strip())
            raw_text = re.sub(r'📅.*', '', raw_text).strip()
            raw_text = re.sub(r'\(Captured from.*?\)', '', raw_text).strip()
            raw_text = re.sub(r'#\w+', '', raw_text).strip()
            raw_text = re.sub(r'^(Add|Read|Buy|Check|Get)\s+', '', raw_text, flags=re.IGNORECASE).strip()
            raw_text = re.sub(r'\s+(to|on)\s+(the\s+)?reading\s+list.*$', '', raw_text, flags=re.IGNORECASE).strip()
            
            note_title = sanitize_filename(action.new_content.strip() or raw_text)
            note_path = os.path.join(dest_dir, f"{note_title}.md")
            
            # Determine frontmatter type
            note_type = "book" if "reading" in dest_folder.lower() or "book" in dest_folder.lower() else "note"
            tags_str = "book" if note_type == "book" else "idea"
            
            note_body = f"""---
title: "{note_title}"
date_captured: "{datetime.now().strftime('%Y-%m-%d')}"
type: "{note_type}"
tags: [{tags_str}]
status: "to-review"
---
# [[{note_title}]]

## Context
Converted from task: *"{line.strip()}"*

## Summary & Notes
{action.reasoning or 'Created via instruction agent.'}
"""
            atomic_write(note_path, note_body)
            created_notes.append(f"[[{note_title}]]")
            
        log_action("Instruction Executed", f"Converted task(s) to note(s) in {dest_folder}: {', '.join(created_notes)}")
        return f"✓ Converted {len(matches)} task(s) into note(s) under '{dest_folder}': {', '.join(created_notes)}."

    elif action_type == "update_task":
        new_content = action.new_content.strip()
        for idx, line in matches:
            lines[idx] = f"- [ ] {new_content}\n"
        atomic_write(TASKS_PATH, "".join(lines))
        return f"✓ Updated {len(matches)} task(s) in Tasks.md."
        
    return f"⚠️ Unsupported task action: {action_type}"

async def handle_note_file_action(action: VaultAction) -> str:
    """Executes note file primitive operations (move, rename, delete, reclassify)."""
    query = action.target.query.strip()
    action_type = action.action_type
    
    if action_type == "create_project":
        project_name = sanitize_filename(query or action.new_content.strip())
        if not project_name:
            return "⚠️ Project name is required to create a new project."
            
        parent_cat = "Professional" if action.destination and "Professional" in action.destination else "Personal"
        project_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects", parent_cat, project_name)
        os.makedirs(project_dir, exist_ok=True)
        os.makedirs(os.path.join(project_dir, "Notes"), exist_ok=True)
        os.makedirs(os.path.join(project_dir, "Meetings"), exist_ok=True)
        os.makedirs(os.path.join(project_dir, "Outputs"), exist_ok=True)
        
        master_note = os.path.join(project_dir, f"{project_name}.md")
        now_str = datetime.now().strftime("%Y-%m-%d")
        body = f"""---
title: "{project_name}"
type: project
date_created: "{now_str}"
status: "active"
tags:
  - project
---
# 🚀 Project: {project_name}

## 🎯 Goal & Objective
{action.reasoning or 'Project created via Second Brain instruction.'}

## 📋 Action Items
- [ ] Initial project setup 📅 {now_str}

## 📁 Key Links & Outputs
- [[Notes]]
- [[Meetings]]
- [[Outputs]]
"""
        if not os.path.exists(master_note):
            atomic_write(master_note, body)
            
        log_action("Instruction Executed", f"Created new project: {project_name} in 02_Projects/{parent_cat}")
        return f"✓ Created new project folder and master note at '02_Projects/{parent_cat}/{project_name}'."

    matching_notes = find_matching_notes(query)
    
    if not matching_notes:
        # Cross-Fallback: Check if target exists as a task line in Tasks.md!
        if os.path.exists(TASKS_PATH):
            with open(TASKS_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
            matching_tasks = find_matching_tasks(query, lines)
            if matching_tasks:
                print(f"Backend [Instruction Handler]: Target note '{query}' not found as file, but matched {len(matching_tasks)} task(s) in Tasks.md! Routing to convert_task_to_note.")
                action.action_type = "convert_task_to_note"
                if not action.destination or action.destination in ["03_Areas", ""]:
                    action.destination = "03_Areas/Reading"
                return await handle_task_action(action)
                
        return f"⚠️ Target Verification: No note or task matching '{query}' found in vault."
        
    action_type = action.action_type
    
    if action_type == "delete_note":
        os.makedirs(TRASH_DIR, exist_ok=True)
        deleted = []
        for rel_path in matching_notes:
            full_src = os.path.join(OBSIDIAN_VAULT_PATH, rel_path)
            full_dest = os.path.join(TRASH_DIR, os.path.basename(rel_path))
            shutil.move(full_src, full_dest)
            deleted.append(os.path.basename(rel_path))
        return f"✓ Soft-deleted {len(deleted)} note(s) to Trash: {', '.join(deleted)}."
        
    elif action_type in ["move_note", "reclassify_note"]:
        dest_folder = action.destination.strip() or "03_Areas"
        full_dest_dir = os.path.join(OBSIDIAN_VAULT_PATH, dest_folder)
        os.makedirs(full_dest_dir, exist_ok=True)
        
        moved = []
        for rel_path in matching_notes:
            full_src = os.path.join(OBSIDIAN_VAULT_PATH, rel_path)
            full_dest = os.path.join(full_dest_dir, os.path.basename(rel_path))
            shutil.move(full_src, full_dest)
            moved.append(os.path.basename(rel_path))
            
            # Check if an associated source material (PDF/audio/text) exists in Sources/ or directory
            src_dir = os.path.dirname(full_src)
            stem = os.path.splitext(os.path.basename(rel_path))[0]
            for candidate_dir in [src_dir, os.path.join(src_dir, "Sources")]:
                if os.path.exists(candidate_dir):
                    for f in os.listdir(candidate_dir):
                        if f.startswith(stem) and not f.endswith(".md"):
                            src_file = os.path.join(candidate_dir, f)
                            dst_file = os.path.join(full_dest_dir, f)
                            try:
                                shutil.move(src_file, dst_file)
                                print(f"Backend [Instruction Handler]: Moved associated source file {f} -> {dest_folder}")
                            except Exception as move_e:
                                print(f"Backend [Instruction Handler]: Failed to move source file {f}: {move_e}")
                                
        return f"✓ Moved {len(moved)} note(s) to '{dest_folder}': {', '.join(moved)}."
        
    elif action_type == "rename_note":
        new_title = sanitize_filename(action.new_content.strip())
        if not new_title:
            return "⚠️ New title required for rename action."
        rel_path = matching_notes[0]
        full_src = os.path.join(OBSIDIAN_VAULT_PATH, rel_path)
        full_dest = os.path.join(os.path.dirname(full_src), f"{new_title}.md")
        os.rename(full_src, full_dest)
        return f"✓ Renamed '{os.path.basename(rel_path)}' to '{new_title}.md'."
        
    return f"⚠️ Unsupported note file action: {action_type}"

async def handle_note_content_action(action: VaultAction) -> str:
    """Executes note content operations (append, update_frontmatter, add_tag)."""
    query = action.target.query.strip()
    matching_notes = find_matching_notes(query)
    
    if not matching_notes:
        return f"⚠️ Target Verification: No note matching '{query}' found in vault."
        
    rel_path = matching_notes[0]
    full_path = os.path.join(OBSIDIAN_VAULT_PATH, rel_path)
    
    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    action_type = action.action_type
    
    if action_type == "append_to_note":
        text_to_append = action.new_content.strip()
        updated_content = content.rstrip() + f"\n\n{text_to_append}\n"
        atomic_write(full_path, updated_content)
        return f"✓ Appended text to note '{os.path.basename(rel_path)}'."
        
    return f"⚠️ Unsupported note content action: {action_type}"

async def handle_system_action(action: VaultAction) -> str:
    """Executes system operations (reindex, run_cleaner, get_sitrep)."""
    action_type = action.action_type
    
    if action_type in ["get_sitrep", "run_summary"]:
        from backend.brain_cleaner import generate_sitrep_summary
        return await generate_sitrep_summary()
        
    elif action_type == "reindex":
        from backend.rag.vectorstore import index_vault
        await index_vault()
        return "✓ Triggered vector database re-indexing."
        
    elif action_type == "run_cleaner":
        from backend.brain_cleaner import main as run_cleaner_main
        await run_cleaner_main()
        return "✓ Executed vault brain cleaner pass."
        
    return f"⚠️ Unsupported system action: {action_type}"

async def execute_vault_action(action: VaultAction) -> str:
    """Executes a single VaultAction with target verification and atomic write safety."""
    action_type = action.action_type
    
    if action_type in ["delete_task", "complete_task", "update_task", "convert_task_to_note"]:
        return await handle_task_action(action)
    elif action_type in ["move_note", "rename_note", "delete_note", "reclassify_note"]:
        return await handle_note_file_action(action)
    elif action_type in ["append_to_note", "update_frontmatter", "add_tag", "remove_tag"]:
        return await handle_note_content_action(action)
    elif action_type in ["reindex", "run_cleaner", "get_sitrep"]:
        return await handle_system_action(action)
    else:
        return f"⚠️ Unknown action type '{action_type}'"

async def process_vault_instruction(instruction_text: str, source_info: str = "") -> str:
    """Parses a natural language vault instruction into an InstructionPlan and executes each action."""
    print(f"Backend [Instruction Handler]: Processing instruction: '{instruction_text[:60]}...'")
    
    try:
        plan = await parse_instruction_plan(instruction_text)
    except Exception as e:
        print(f"Backend [Instruction Handler]: Plan parsing failed: {e}")
        return f"❌ Failed to parse vault instruction: {e}"
        
    if not plan.actions:
        return "⚠️ Instruction Handler: No valid vault actions identified."
        
    # If the single action is getting a SITREP/Summary, return the SITREP directly!
    if len(plan.actions) == 1 and plan.actions[0].action_type in ["get_sitrep", "run_summary"]:
        return await handle_system_action(plan.actions[0])
        
    results = []
    mutated_vault = False
    print(f"Backend [Instruction Handler]: Executing plan: '{plan.summary}' ({len(plan.actions)} actions)")
    
    for i, action in enumerate(plan.actions, start=1):
        print(f"  Action {i}/{len(plan.actions)} [{action.action_type}] Target: '{action.target.query}' -> Reasoning: {action.reasoning}")
        res = await execute_vault_action(action)
        results.append(res)
        if action.action_type not in ["get_sitrep", "reindex"]:
            mutated_vault = True
            
    # If vault content was modified, update vector index immediately
    if mutated_vault:
        try:
            from backend.rag.vectorstore import index_vault
            print("Backend [Instruction Handler]: Vault content mutated. Triggering incremental vector indexing...")
            await index_vault()
        except Exception as idx_e:
            print(f"Backend [Instruction Handler]: Incremental indexing warning: {idx_e}")
        
    # Build human-readable execution receipt
    receipt_lines = [
        "⚡ **Vault Instruction Executed**",
        f"**Plan:** {plan.summary}",
        ""
    ]
    receipt_lines.extend(results)
    
    final_receipt = "\n".join(receipt_lines)
    print(f"Backend [Instruction Handler]: Execution complete.\n{final_receipt}")
    return final_receipt
