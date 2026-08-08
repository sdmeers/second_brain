import os
import re
from datetime import datetime
import shutil
import httpx
from backend.config import OBSIDIAN_VAULT_PATH, GEMINI_MODEL, TASKS_PATH, INBOX_PATH
from backend.utils.gemini import call_gemini_with_retry
from google.genai import types
from pydantic import BaseModel, Field

# In-memory session state for clarify flow
CLARIFY_SESSIONS = {}

async def handle_clarify_command(chat_id: int, token: str, http_client: httpx.AsyncClient):
    captured_md = os.path.join(TASKS_PATH, "Captured.md")
    
    if not os.path.exists(captured_md):
        await http_client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "🎉 Inbox zero! No captured items to clarify."}
        )
        return

    with open(captured_md, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    items = [line for line in lines if line.strip().startswith("- ")]
    
    if not items:
        await http_client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "🎉 Inbox zero! No captured items to clarify."}
        )
        return

    # Process first batch of 5
    CLARIFY_SESSIONS[chat_id] = {
        "items": items,
        "current_index": 0,
        "batch_end": min(5, len(items)),
        "lines": lines,
        "item_data": {}  # Temporary state for active item
    }
    
    await process_next_clarify_item(chat_id, token, http_client)

async def process_next_clarify_item(chat_id: int, token: str, http_client: httpx.AsyncClient):
    session = CLARIFY_SESSIONS.get(chat_id)
    if not session:
        return
        
    if session["current_index"] >= session["batch_end"]:
        remaining = len(session["items"]) - session["current_index"]
        if remaining > 0:
            reply_markup = {
                "inline_keyboard": [
                    [{"text": "▶️ Continue next 5", "callback_data": "clarify:continue"},
                     {"text": "⏸️ Pause", "callback_data": "clarify:pause"}]
                ]
            }
            await http_client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id, 
                    "text": f"✅ Batch complete. {remaining} items remaining.",
                    "reply_markup": reply_markup
                }
            )
        else:
            await http_client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": "🎉 All items clarified! Inbox zero!"}
            )
            del CLARIFY_SESSIONS[chat_id]
        return

    item_line = session["items"][session["current_index"]]
    session["item_data"] = {"raw_line": item_line}
    
    # AI Smart Default Analysis
    class SmartDefaultResult(BaseModel):
        context: str = Field(description="Context tag e.g. @home, @computer, @errands, @phone, @garage, @office, @WFH")
        project: str = Field(description="Name of project if applicable e.g. Garage, Second Brain, TF RAID")
        is_actionable: bool = Field(description="True if this is an executable task")

    try:
        config = types.GenerateContentConfig(
            system_instruction="Analyze captured item for GTD routing. Recommend context (@home, @computer, @errands, @phone, @garage, @office, @WFH) and project if mentioned.",
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=SmartDefaultResult,
        )
        response = await call_gemini_with_retry(
            model=GEMINI_MODEL,
            contents=f"Item: {item_line}",
            config=config
        )
        res = SmartDefaultResult.model_validate_json(response.text.strip())
        ctx = res.context if res.context.startswith("@") else f"@{res.context}"
        proj = res.project.strip().title() if res.project else None
        
        session["item_data"]["ai_context"] = ctx
        session["item_data"]["ai_project"] = proj
        
        if proj:
            suggested_button = f"⚡ Add as {ctx} #project/{proj}"
        else:
            suggested_button = f"⚡ Add as {ctx}"
    except Exception:
        session["item_data"]["ai_context"] = "@computer"
        session["item_data"]["ai_project"] = None
        suggested_button = "⚡ Add as @computer"

    reply_markup = {
        "inline_keyboard": [
            [{"text": suggested_button, "callback_data": "clarify:smart_add"}],
            [{"text": "🔧 Customise Flow", "callback_data": "clarify:customise"}]
        ]
    }

    clean_display = re.sub(r'\(Source:.*?\)', '', item_line)
    clean_display = re.sub(r'\(Captured on.*?\)', '', clean_display).strip("- \n")
    await http_client.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id, 
            "text": f"📥 <b>Item {session['current_index']+1} of {len(session['items'])}:</b>\n{clean_display}",
            "parse_mode": "HTML",
            "reply_markup": reply_markup
        }
    )

def ensure_project_note_exists(project_name: str) -> str:
    """Ensures project note exists, creating it with v5 template if missing."""
    proj_clean = project_name.strip().title()
    proj_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
    
    # 1. First pass: exact filename match (e.g. Garage.md)
    for root, dirs, files in os.walk(proj_dir):
        for f in files:
            if f.lower() == f"{proj_clean.lower()}.md":
                return os.path.join(root, f)
                
    # 2. Second pass: exact directory name match (e.g. folder named Garage)
    for root, dirs, files in os.walk(proj_dir):
        if os.path.basename(root).lower() == proj_clean.lower():
            target_md = os.path.join(root, f"{proj_clean}.md")
            if os.path.exists(target_md):
                return target_md
                
    # If not found, create under Personal
    personal_dir = os.path.join(proj_dir, "Personal")
    target_folder = os.path.join(personal_dir, proj_clean)
    if os.path.exists(target_folder) and os.path.isdir(target_folder):
        found_path = os.path.join(target_folder, f"{proj_clean}.md")
    else:
        found_path = os.path.join(personal_dir, f"{proj_clean}.md")
        
    if not os.path.exists(found_path):
        os.makedirs(os.path.dirname(found_path), exist_ok=True)
        today_str = datetime.now().strftime("%Y-%m-%d")
        template = f"""---
title: "{proj_clean}"
type: project
category: Personal
outcome: "{proj_clean} project goals and desired outcome"
date_created: "{today_str}"
status: active
---
# {proj_clean}

## Desired Outcome
{proj_clean} project goals and desired outcome.

## Project Steps

## Active Actions
> All actions tagged `#project/{proj_clean}` across task lists.
> View via Telegram: `/project {proj_clean}` or search `#project/{proj_clean}` in Obsidian.

## Reference Material
> Notes and resources tagged with `#project/{proj_clean}` across the vault.
"""
        with open(found_path, "w", encoding="utf-8") as f:
            f.write(template)
            
    return found_path

def append_to_project_roadmap(project_name: str, step_text: str):
    """Appends a step to project note under ## Project Steps."""
    note_path = ensure_project_note_exists(project_name)
    with open(note_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    clean_step = re.sub(r'\(Captured on.*?\)', '', step_text)
    clean_step = re.sub(r'\(Source:.*?\)', '', clean_step).strip("- \n")
    if "## Project Steps" in content:
        content = content.replace("## Project Steps", f"## Project Steps\n- [ ] {clean_step}")
    else:
        content += f"\n\n## Project Steps\n- [ ] {clean_step}\n"
        
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(content)

def get_category_project_buttons(category: str) -> list:
    """Gets top-level project buttons for a given category (Personal or Professional)."""
    proj_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects", category)
    projects = []
    if os.path.exists(proj_dir):
        for entry in sorted(os.listdir(proj_dir)):
            if entry.startswith(".") or entry.startswith("_") or entry in ["archive", "docs", "Notes", "Meetings", "Outputs"]:
                continue
            entry_path = os.path.join(proj_dir, entry)
            if os.path.isdir(entry_path) or entry.endswith(".md"):
                p_name = entry.replace(".md", "")
                projects.append(p_name)
                
    keyboard = []
    row = []
    for p in projects:
        p_short = p[:25]
        row.append({"text": f"📁 {p_short}", "callback_data": f"clarify:roadmap_proj:{p_short}"})
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    return keyboard

async def archive_and_remove_captured(item_line: str):
    """Archives source file from Inbox and removes item from Captured.md."""
    source_match = re.search(r'\(Source:\s*\[\[(.*?)\]\]\)', item_line)
    filename = source_match.group(1) if source_match else None
    
    if filename:
        inbox_path = os.path.join(OBSIDIAN_VAULT_PATH, "00_System", "Inbox")
        archive_path = os.path.join(inbox_path, "Archive")
        os.makedirs(archive_path, exist_ok=True)
        
        src_file = os.path.join(inbox_path, filename)
        dest_file = os.path.join(archive_path, filename)
        if os.path.exists(src_file):
            shutil.move(src_file, dest_file)

    captured_md = os.path.join(TASKS_PATH, "Captured.md")
    if os.path.exists(captured_md):
        with open(captured_md, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace(item_line, "")
        with open(captured_md, "w", encoding="utf-8") as f:
            f.write(content)

async def handle_clarify_callback(callback_query: dict, token: str, http_client: httpx.AsyncClient):
    chat_id = callback_query["message"]["chat"]["id"]
    data = callback_query.get("data", "")
    message_id = callback_query["message"]["message_id"]
    
    action = data.split(":")[1]
    session = CLARIFY_SESSIONS.get(chat_id)
    
    if action == "pause":
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": "⏸️ Clarify session paused."}
        )
        if chat_id in CLARIFY_SESSIONS:
            del CLARIFY_SESSIONS[chat_id]
        return
        
    if action == "continue":
        if session:
            session["batch_end"] = min(session["batch_end"] + 5, len(session["items"]))
            await http_client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={"chat_id": chat_id, "message_id": message_id, "text": "▶️ Resuming..."}
            )
            await process_next_clarify_item(chat_id, token, http_client)
        return

    if not session:
        return

    item_data = session.get("item_data", {})
    item_line = item_data.get("raw_line", session["items"][session["current_index"]])

    if action == "smart_add":
        ctx = item_data.get("ai_context", "@computer")
        proj = item_data.get("ai_project")
        
        clean_text = re.sub(r'\(Captured on.*?\)', '', item_line)
        clean_text = re.sub(r'\(Source:.*?\)', '', clean_text).strip("- \n")
        task_str = f"- [ ] {clean_text} {ctx}"
        if proj:
            task_str += f" #project/{proj}"
            ensure_project_note_exists(proj)
            
        tasks_md = os.path.join(TASKS_PATH, "Next_Actions.md")
        with open(tasks_md, "a", encoding="utf-8") as f:
            f.write(f"{task_str}\n")
            
        await archive_and_remove_captured(item_line)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"✅ Added to Next Actions ({ctx}):\n{clean_text}"}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

    if action == "customise":
        # Full decision tree
        clean_prompt = re.sub(r'\(.*?\)', '', item_line).strip("- \n")
        reply_markup = {
            "inline_keyboard": [
                [{"text": "⚡ Next Action", "callback_data": "clarify:action_next"},
                 {"text": "📌 Project Roadmap Step", "callback_data": "clarify:action_roadmap_cat"}],
                [{"text": "⏳ Waiting For", "callback_data": "clarify:action_waiting"},
                 {"text": "💡 Someday/Maybe", "callback_data": "clarify:action_someday"}],
                [{"text": "📚 Reference Note", "callback_data": "clarify:action_ref"},
                 {"text": "🗑️ Trash", "callback_data": "clarify:action_trash"}]
            ]
        }
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"🤔 <b>How would you like to classify this item?</b>\n\n{clean_prompt}",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return

    if action == "action_roadmap_cat":
        # Select category first (Personal vs Professional)
        reply_markup = {
            "inline_keyboard": [
                [{"text": "🏠 Personal Projects", "callback_data": "clarify:cat_proj:Personal"},
                 {"text": "💼 Professional Projects", "callback_data": "clarify:cat_proj:Professional"}]
            ]
        }
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "📂 <b>Select Project Category:</b>",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return

    if action.startswith("cat_proj"):
        category = data.split(":")[2]
        keyboard = get_category_project_buttons(category)
        reply_markup = {"inline_keyboard": keyboard}
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"📁 <b>Select {category} Project:</b>",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return

    if action.startswith("roadmap_proj"):
        proj_name = data.split(":")[2]
        append_to_project_roadmap(proj_name, item_line)
        await archive_and_remove_captured(item_line)
        
        clean_text = re.sub(r'\(Captured on.*?\)', '', item_line)
        clean_text = re.sub(r'\(Source:.*?\)', '', clean_text).strip("- \n")
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"📌 Appended roadmap step to <b>{proj_name}</b>:\n{clean_text}", "parse_mode": "HTML"}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

    if action == "action_waiting":
        clean_text = re.sub(r'\(Captured on.*?\)', '', item_line)
        clean_text = re.sub(r'\(Source:.*?\)', '', clean_text).strip("- \n")
        waiting_md = os.path.join(TASKS_PATH, "Waiting_For.md")
        with open(waiting_md, "a", encoding="utf-8") as f:
            f.write(f"- [ ] {clean_text} #waiting\n")
        await archive_and_remove_captured(item_line)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"⏳ Added to Waiting For:\n{clean_text}"}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

    if action == "action_next":
        # Select context
        reply_markup = {
            "inline_keyboard": [
                [{"text": "@home", "callback_data": "clarify:ctx:@home"},
                 {"text": "@computer", "callback_data": "clarify:ctx:@computer"},
                 {"text": "@errands", "callback_data": "clarify:ctx:@errands"}],
                [{"text": "@phone", "callback_data": "clarify:ctx:@phone"},
                 {"text": "@garage", "callback_data": "clarify:ctx:@garage"},
                 {"text": "@office", "callback_data": "clarify:ctx:@office"}]
            ]
        }
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "🏷️ <b>Select Context:</b>",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return

    if action.startswith("ctx"):
        ctx = data.split(":")[2]
        clean_text = re.sub(r'\(Captured on.*?\)', '', item_line)
        clean_text = re.sub(r'\(Source:.*?\)', '', clean_text).strip("- \n")
        task_str = f"- [ ] {clean_text} {ctx}"
        
        tasks_md = os.path.join(TASKS_PATH, "Next_Actions.md")
        with open(tasks_md, "a", encoding="utf-8") as f:
            f.write(f"{task_str}\n")
            
        await archive_and_remove_captured(item_line)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"⚡ Added to Next Actions ({ctx}):\n{clean_text}"}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

    if action == "action_someday":
        clean_text = re.sub(r'\(Captured on.*?\)', '', item_line)
        clean_text = re.sub(r'\(Source:.*?\)', '', clean_text).strip("- \n")
        someday_md = os.path.join(TASKS_PATH, "Someday_Maybe.md")
        with open(someday_md, "a", encoding="utf-8") as f:
            f.write(f"- [ ] {clean_text}\n")
        await archive_and_remove_captured(item_line)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"💡 Saved to Someday/Maybe:\n{clean_text}"}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

    if action == "action_ref":
        clean_text = re.sub(r'\(Captured on.*?\)', '', item_line)
        clean_text = re.sub(r'\(Source:.*?\)', '', clean_text).strip("- \n")
        await archive_and_remove_captured(item_line)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"📚 Routed item to Reference / Research Brain:\n{clean_text}"}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

    if action == "action_trash":
        await archive_and_remove_captured(item_line)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"🗑️ Trashed captured item."}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return
