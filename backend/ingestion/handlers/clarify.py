import os
import re
from datetime import datetime
from backend.config import OBSIDIAN_VAULT_PATH, GEMINI_MODEL
from backend.utils.gemini import call_gemini_with_retry
from google.genai import types
from pydantic import BaseModel, Field

# In-memory session state for clarify flow
CLARIFY_SESSIONS = {}

async def handle_clarify_command(chat_id: int, token: str, http_client):
    captured_md = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Captured.md")
    
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
    batch = items[:5]
    CLARIFY_SESSIONS[chat_id] = {
        "items": items,
        "current_index": 0,
        "batch_end": min(5, len(items)),
        "lines": lines
    }
    
    await process_next_clarify_item(chat_id, token, http_client)

async def process_next_clarify_item(chat_id: int, token: str, http_client):
    session = CLARIFY_SESSIONS.get(chat_id)
    if not session:
        return
        
    if session["current_index"] >= session["batch_end"]:
        # Ask to continue
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
    # Use Gemini for smart default
    # This is a placeholder for the actual AI call
    
    class SmartDefaultResult(BaseModel):
        context: str = Field(description="The context tag like @errands, @computer")
        project: str = Field(description="The project name if applicable")
        is_actionable: bool = Field(description="Is this actionable")
    
    try:
        config = types.GenerateContentConfig(
            system_instruction="Analyze the captured item and recommend context and project.",
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
        suggested_button = f"⚡ Add as {res.context} #project/{res.project}"
    except Exception:
        suggested_button = "⚡ Add as @computer"

    reply_markup = {
        "inline_keyboard": [
            [{"text": suggested_button, "callback_data": "clarify:smart_add"}],
            [{"text": "🔧 Customise", "callback_data": "clarify:customise"}]
        ]
    }

    await http_client.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id, 
            "text": f"📥 Item {session['current_index']+1} of {len(session['items'])}:\n{item_line}",
            "reply_markup": reply_markup
        }
    )

async def handle_clarify_callback(callback_query: dict, token: str, http_client):
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

    if session:
        item_line = session["items"][session["current_index"]]
        
        # 1. Parse filename from `(Source: [[filename]])`
        source_match = re.search(r'\(Source:\s*\[\[(.*?)\]\]\)', item_line)
        filename = source_match.group(1) if source_match else None
        
        # 2. Archive the file if it exists in Inbox
        if filename:
            inbox_path = os.path.join(OBSIDIAN_VAULT_PATH, "00_System", "Inbox")
            archive_path = os.path.join(inbox_path, "Archive")
            os.makedirs(archive_path, exist_ok=True)
            
            src_file = os.path.join(inbox_path, filename)
            dest_file = os.path.join(archive_path, filename)
            
            if os.path.exists(src_file):
                import shutil
                shutil.move(src_file, dest_file)
        
        # 3. Create task in Next_Actions with the Archive backlink
        task_text = re.sub(r'\(Captured on.*?\)', '', item_line).strip("- \n")
        if filename:
            task_text = re.sub(r'\(Source:.*?\]\]\)', f'(Source: [[00_System/Inbox/Archive/{filename}]])', task_text)
            
        tasks_md = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Next_Actions.md")
        os.makedirs(os.path.dirname(tasks_md), exist_ok=True)
        with open(tasks_md, "a", encoding="utf-8") as f:
            f.write(f"- [ ] {task_text.strip()}\n")
            
        # 4. Remove from Captured.md
        captured_md = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Captured.md")
        if os.path.exists(captured_md):
            with open(captured_md, "r", encoding="utf-8") as f:
                content = f.read()
            content = content.replace(item_line, "")
            with open(captured_md, "w", encoding="utf-8") as f:
                f.write(content)
        
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"✅ Processed:\n{item_line}"}
        )
        
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
