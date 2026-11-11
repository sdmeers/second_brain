import os
import re
from datetime import datetime, timedelta
import httpx
from backend.config import OBSIDIAN_VAULT_PATH, TASKS_PATH
from backend.utils.sessions import SessionStore
from backend.vault import clarify_ops
from backend.vault.tasks import (
    append_project_step,
    ensure_project_note,
    strip_task_metadata,
)

# In-memory session state for the clarify flow; expires so an abandoned run is
# reported as "session expired" rather than lingering forever.
CLARIFY_SESSIONS = SessionStore("clarify")

async def handle_clarify_command(chat_id: int, token: str, http_client: httpx.AsyncClient):
    captured_md = os.path.join(TASKS_PATH, "Captured.md")
    
    if not os.path.exists(captured_md):
        await http_client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "🎉 Inbox zero! No captured items to clarify."}
        )
        return

    with open(captured_md, encoding="utf-8") as f:
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
        "item_data": {},
        "awaiting_text_input": None  # 'new_project_name' | 'new_project_outcome'
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
    
    clean_display = re.sub(r'\(Source:.*?\)', '', item_line)
    clean_display = re.sub(r'\(Captured on.*?\)', '', clean_display).strip("- \n")
    
    # Complete GTD Clarify Menu
    reply_markup = {
        "inline_keyboard": [
            [{"text": "⚡ Next Action", "callback_data": "clarify:action_next"},
             {"text": "📌 Existing Project Roadmap", "callback_data": "clarify:action_roadmap_cat"}],
            [{"text": "📁 New Project", "callback_data": "clarify:action_new_proj_cat"},
             {"text": "⏳ Waiting For", "callback_data": "clarify:action_waiting"}],
            [{"text": "🔔 Defer / Tickle", "callback_data": "clarify:action_defer"},
             {"text": "💡 Someday / Maybe", "callback_data": "clarify:action_someday"}],
            [{"text": "📚 Pass to AI Librarian", "callback_data": "clarify:action_librarian"},
             {"text": "⚡ 2-Min Rule (Done)", "callback_data": "clarify:action_2min"}],
            [{"text": "🗑️ Trash", "callback_data": "clarify:action_trash"}]
        ]
    }

    await http_client.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id, 
            "text": f"📥 <b>Item {session['current_index']+1} of {len(session['items'])}:</b>\n{clean_display}\n\n🤔 <i>How would you like to classify this?</i>",
            "parse_mode": "HTML",
            "reply_markup": reply_markup
        }
    )

def ensure_project_note_exists(project_name: str, category: str = "Personal", outcome: str = None) -> str:
    """Ensures project note exists, creating it from the shared template if missing."""
    return ensure_project_note(project_name, category=category, outcome=outcome)

def sanitize_task_item_text(item_line: str) -> str:
    """Strips metadata tags, source links, and leading markdown checkboxes/bullets.

    Kept as a named alias so existing call sites read clearly; the implementation
    lives in backend.vault.tasks so every module strips identically.
    """
    return strip_task_metadata(item_line)

def append_to_project_roadmap(project_name: str, step_text: str):
    """Appends a step to project note under ## Project Steps."""
    append_project_step(project_name, step_text)

def get_category_project_buttons(category: str, action_prefix: str = "roadmap_proj") -> list:
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
        row.append({"text": f"📁 {p_short}", "callback_data": f"clarify:{action_prefix}:{p_short}"})
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    return keyboard

async def archive_and_remove_captured(item_line: str) -> str:
    """Archives source file from Inbox and removes item from Captured.md. Returns filename if any."""
    filename = clarify_ops.remove_captured(item_line)
    return filename

def parse_natural_date_str(date_str: str):
    """Parses natural dates like '23rd Aug', '23 Aug', '23/08', 'tomorrow', '7d', '2026-08-23'."""
    today = datetime.now().date()
    ds = date_str.lower().strip()
    
    if ds in ["tomorrow", "+1d", "1d"]:
        return today + timedelta(days=1)
    if ds in ["next week", "+7d", "7d", "week"]:
        return today + timedelta(days=7)
    if ds in ["next month", "+30d", "30d", "month"]:
        return today + timedelta(days=30)
    if ds.startswith("+") and ds[1:-1].isdigit():
        return today + timedelta(days=int(ds[1:-1]))
    if ds.endswith("d") and ds[:-1].isdigit():
        return today + timedelta(days=int(ds[:-1]))

    cleaned_ds = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', ds)

    try:
        return datetime.strptime(cleaned_ds, "%Y-%m-%d").date()
    except ValueError:
        pass

    m_slash = re.match(r'^(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?$', cleaned_ds)
    if m_slash:
        day = int(m_slash.group(1))
        month = int(m_slash.group(2))
        year = int(m_slash.group(3)) if m_slash.group(3) else today.year
        if year < 100: year += 2000
        try:
            target = datetime(year, month, day).date()
            if target < today and not m_slash.group(3):
                target = datetime(year + 1, month, day).date()
            return target
        except ValueError:
            pass

    months = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
        "nov": 11, "november": 11, "dec": 12, "december": 12
    }
    
    m_day_month = re.match(r'^(\d{1,2})\s*([a-z]+)(?:\s+(\d{4}))?$', cleaned_ds)
    if m_day_month:
        day = int(m_day_month.group(1))
        month_name = m_day_month.group(2)
        year = int(m_day_month.group(3)) if m_day_month.group(3) else today.year
        if month_name in months:
            month = months[month_name]
            try:
                target = datetime(year, month, day).date()
                if target < today and not m_day_month.group(3):
                    target = datetime(year + 1, month, day).date()
                return target
            except ValueError:
                pass

    m_month_day = re.match(r'^([a-z]+)\s*(\d{1,2})(?:\s+(\d{4}))?$', cleaned_ds)
    if m_month_day:
        month_name = m_month_day.group(1)
        day = int(m_month_day.group(2))
        year = int(m_month_day.group(3)) if m_month_day.group(3) else today.year
        if month_name in months:
            month = months[month_name]
            try:
                target = datetime(year, month, day).date()
                if target < today and not m_month_day.group(3):
                    target = datetime(year + 1, month, day).date()
                return target
            except ValueError:
                pass

    return today + timedelta(days=7)

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

    # 1. Trash
    if action == "action_trash":
        await archive_and_remove_captured(item_line)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": "🗑️ Trashed captured item."}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

    # 2. Pass to AI Librarian
    if action == "action_librarian":
        clean_text = sanitize_task_item_text(item_line)
        clarify_ops.to_reference(item_line)
        
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"📚 Sent to AI Librarian for PARA reference filing:\n{clean_text}"}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

    # 3. 2-Minute Rule
    if action == "action_2min":
        clean_text = sanitize_task_item_text(item_line)
        clarify_ops.to_done(item_line)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"⚡ 2-Minute Rule Done! Logged to z_Completed.md:\n{clean_text}"}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

    # 4. New Project Creation Flow
    if action == "action_new_proj_cat":
        reply_markup = {
            "inline_keyboard": [
                [{"text": "🏠 Personal", "callback_data": "clarify:new_cat:Personal"},
                 {"text": "💼 Professional", "callback_data": "clarify:new_cat:Professional"}]
            ]
        }
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "🆕 <b>Select Category for New Project:</b>",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return

    if action.startswith("new_cat"):
        category = data.split(":")[2]
        session["item_data"]["new_project_category"] = category
        session["awaiting_text_input"] = "new_project_name"
        
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"🆕 <b>Step 1 of 2 — New {category} Project:</b>\nPlease reply with the <b>Project Name</b> (e.g. <code>Garage Workbench</code> or <code>TF RAID Integration</code>):",
                "parse_mode": "HTML"
            }
        )
        return

    # 5. Existing Project Roadmap Step Flow
    if action == "action_roadmap_cat":
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
        keyboard = get_category_project_buttons(category, action_prefix="roadmap_proj")
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
        clarify_ops.to_project_step(item_line, project=proj_name)

        clean_text = sanitize_task_item_text(item_line)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"📌 Appended roadmap step to <b>{proj_name}</b>:\n{clean_text}", "parse_mode": "HTML"}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

    # 6. Waiting For Flow
    if action == "action_waiting":
        session["awaiting_text_input"] = "waiting_person"
        session["item_data"]["raw_line"] = item_line
        
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "⏳ <b>Who or what are you waiting for?</b>\n\nPlease reply with the person, company, or entity (e.g. <code>Rosie</code>, <code>Amazon</code>, <code>Accountant</code>):",
                "parse_mode": "HTML"
            }
        )
        return

    if action == "waiting_standalone_cat":
        reply_markup = {
            "inline_keyboard": [
                [{"text": "🏠 Personal", "callback_data": "clarify:save_waiting:Personal:none"},
                 {"text": "💼 Professional", "callback_data": "clarify:save_waiting:Professional:none"}]
            ]
        }
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "🏷️ <b>Select Category for Waiting Item:</b>",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return

    if action.startswith("waiting_tag_cat"):
        category = data.split(":")[2]
        session["item_data"]["selected_category"] = category
        keyboard = get_category_project_buttons(category, action_prefix="waiting_proj")
        reply_markup = {"inline_keyboard": keyboard}
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"🏷️ <b>Select {category} Project for Waiting Item:</b>",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return

    if action.startswith("waiting_proj") or action.startswith("save_waiting"):
        parts = data.split(":")
        if action.startswith("save_waiting"):
            category = parts[2]
            proj_tag = parts[3] if len(parts) > 3 else "none"
        else:
            proj_tag = parts[2]
            category = session["item_data"].get("selected_category")
            if not category:
                from backend.utils.tasks_dashboard import get_project_category
                category = get_project_category(proj_tag)

        waiting_person = session["item_data"].get("waiting_person", "Someone")
        clean_text = sanitize_task_item_text(item_line)
        cat_tag = "#professional" if category == "Professional" else "#personal"
        
        wait_tag = f"#waiting({waiting_person})"
        tag_disp = f" {wait_tag} {cat_tag}"
        if proj_tag != "none":
            tag_disp += f" #project/{proj_tag.strip().replace(' ', '_')}"

        clarify_ops.to_waiting(item_line, waiting_on=waiting_person, category=category,
                               project=None if proj_tag == "none" else proj_tag)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"⏳ Added to Waiting For ({tag_disp}):\n{clean_text}"}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

    # 7. Next Action Flow with Optional Project Tagging
    if action == "action_next":
        reply_markup = {
            "inline_keyboard": [
                [{"text": "@home", "callback_data": "clarify:ctx:@home"},
                 {"text": "@computer", "callback_data": "clarify:ctx:@computer"},
                 {"text": "@errands", "callback_data": "clarify:ctx:@errands"}],
                [{"text": "@phone", "callback_data": "clarify:ctx:@phone"},
                 {"text": "@anywhere", "callback_data": "clarify:ctx:@anywhere"},
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
        session["item_data"]["selected_context"] = ctx
        
        reply_markup = {
            "inline_keyboard": [
                [{"text": "🚫 No Project Tag (Standalone)", "callback_data": "clarify:standalone_cat"}],
                [{"text": "🏠 Tag Personal Project", "callback_data": "clarify:tag_cat:Personal"},
                 {"text": "💼 Tag Professional Project", "callback_data": "clarify:tag_cat:Professional"}]
            ]
        }
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"🏷️ Context: <b>{ctx}</b>\nOptionally tag with a project?",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return

    if action == "standalone_cat":
        reply_markup = {
            "inline_keyboard": [
                [{"text": "🏠 Personal", "callback_data": "clarify:save_action:Personal:none"},
                 {"text": "💼 Professional", "callback_data": "clarify:save_action:Professional:none"}]
            ]
        }
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "🏷️ <b>Select Category for Action:</b>",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return

    if action.startswith("tag_cat"):
        category = data.split(":")[2]
        session["item_data"]["selected_category"] = category
        keyboard = get_category_project_buttons(category, action_prefix="tag_proj")
        reply_markup = {"inline_keyboard": keyboard}
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"🏷️ <b>Select {category} Project Tag:</b>",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return

    if action.startswith("tag_proj") or action.startswith("save_action"):
        parts = data.split(":")
        if action.startswith("save_action"):
            category = parts[2]
            proj_tag = parts[3]
        else:
            proj_tag = parts[2]
            category = session["item_data"].get("selected_category")
            if not category:
                from backend.utils.tasks_dashboard import get_project_category
                category = get_project_category(proj_tag)

        ctx = session["item_data"].get("selected_context", "@computer")
        clean_text = sanitize_task_item_text(item_line)
        cat_tag = "#professional" if category == "Professional" else "#personal"
        
        tag_disp = f" {cat_tag}"
        if proj_tag != "none":
            tag_disp += f" #project/{proj_tag.strip().replace(' ', '_')}"

        clarify_ops.to_next(item_line, category=category, contexts=[ctx],
                            project=None if proj_tag == "none" else proj_tag)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"⚡ Added to Next Actions ({ctx}{tag_disp}):\n{clean_text}"}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

    # 8. Defer / Tickle Flow — Directly open visual Calendar Picker
    if action == "action_defer":
        today = datetime.now().date()
        from backend.utils.telegram_calendar import generate_telegram_calendar
        cal_kb = generate_telegram_calendar(today.year, today.month, "clarify_cal")
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "📅 <b>Select Deferral Date:</b>",
                "parse_mode": "HTML",
                "reply_markup": cal_kb
            }
        )
        return

    if action == "defer_open_cal":
        today = datetime.now().date()
        from backend.utils.telegram_calendar import generate_telegram_calendar
        cal_kb = generate_telegram_calendar(today.year, today.month, "clarify_cal")
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "📅 <b>Select Deferral Date:</b>",
                "parse_mode": "HTML",
                "reply_markup": cal_kb
            }
        )
        return

    if action.startswith("clarify_cal") or data.startswith("clarify_cal:"):
        parts = data.split(":")
        cal_action = parts[1]
        if cal_action == "nav":
            year = int(parts[2])
            month = int(parts[3])
            from backend.utils.telegram_calendar import generate_telegram_calendar
            cal_kb = generate_telegram_calendar(year, month, "clarify_cal")
            await http_client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": "📅 <b>Select Deferral Date:</b>",
                    "parse_mode": "HTML",
                    "reply_markup": cal_kb
                }
            )
            return

        if cal_action == "pick":
            iso_str = parts[2]
            try:
                date_display = datetime.strptime(iso_str, "%Y-%m-%d").strftime("%d-%m-%Y")
            except ValueError:
                date_display = iso_str

            session["item_data"]["custom_defer_date"] = iso_str
            
            reply_markup = {
                "inline_keyboard": [
                    [{"text": "🏠 Personal", "callback_data": "clarify:save_custom_defer:Personal"},
                     {"text": "💼 Professional", "callback_data": "clarify:save_custom_defer:Professional"}]
                ]
            }
            await http_client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"🏷️ <b>Select Category for Deferred Item ({date_display}):</b>",
                    "parse_mode": "HTML",
                    "reply_markup": reply_markup
                }
            )
            return

    if action == "defer_custom":
        session["awaiting_text_input"] = "defer_custom_date"
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "📅 <b>Reply with the Deferral Date:</b>\ne.g. <code>23-08-2026</code>, <code>23rd Aug</code>, or <code>23/08</code>",
                "parse_mode": "HTML"
            }
        )
        return

    if action.startswith("defer_days"):
        days = int(data.split(":")[2])
        session["item_data"]["defer_days"] = days
        reply_markup = {
            "inline_keyboard": [
                [{"text": "🏠 Personal", "callback_data": f"clarify:save_defer:{days}:Personal"},
                 {"text": "💼 Professional", "callback_data": f"clarify:save_defer:{days}:Professional"}]
            ]
        }
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"🏷️ <b>Select Category for Deferred Item (+{days}d):</b>",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return

    if action.startswith("save_defer") or action.startswith("save_custom_defer"):
        parts = data.split(":")
        if action.startswith("save_custom_defer"):
            category = parts[2]
            defer_date = session["item_data"].get("custom_defer_date", datetime.now().strftime("%Y-%m-%d"))
        else:
            days = int(parts[2])
            category = parts[3]
            defer_date = (datetime.now().date() + timedelta(days=days)).strftime("%Y-%m-%d")

        try:
            date_display = datetime.strptime(defer_date, "%Y-%m-%d").strftime("%d-%m-%Y")
        except ValueError:
            date_display = defer_date

        clean_text = sanitize_task_item_text(item_line)
        cat_tag = "#professional" if category == "Professional" else "#personal"
        
        clarify_ops.to_next(item_line, category=category, tickler=defer_date)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"🔔 Deferred until {date_display} ({cat_tag}):\n{clean_text}"}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

    # 9. Someday / Maybe Flow
    if action == "action_someday":
        reply_markup = {
            "inline_keyboard": [
                [{"text": "🏠 Personal", "callback_data": "clarify:save_someday:Personal"},
                 {"text": "💼 Professional", "callback_data": "clarify:save_someday:Professional"}]
            ]
        }
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "🏷️ <b>Select Category for Someday/Maybe Item:</b>",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return

    if action.startswith("save_someday"):
        category = data.split(":")[2]
        cat_tag = "#professional" if category == "Professional" else "#personal"
        clean_text = sanitize_task_item_text(item_line)
        clarify_ops.to_someday(item_line, category=category)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"💡 Saved to Someday/Maybe ({cat_tag}):\n{clean_text}"}
        )
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return

async def handle_clarify_text_input(chat_id: int, text: str, token: str, http_client: httpx.AsyncClient) -> bool:
    """Handles text replies during clarify flow (e.g. project name & outcome)."""
    session = CLARIFY_SESSIONS.get(chat_id)
    if not session or not session.get("awaiting_text_input"):
        return False
        
    input_type = session["awaiting_text_input"]
    item_data = session.get("item_data", {})
    item_line = item_data.get("raw_line", session["items"][session["current_index"]])
    
    if input_type == "waiting_person":
        person_clean = text.strip()
        session["item_data"]["waiting_person"] = person_clean
        session["awaiting_text_input"] = None
        
        reply_markup = {
            "inline_keyboard": [
                [{"text": "🚫 No Project Tag (Standalone)", "callback_data": "clarify:waiting_standalone_cat"}],
                [{"text": "🏠 Tag Personal Project", "callback_data": "clarify:waiting_tag_cat:Personal"},
                 {"text": "💼 Tag Professional Project", "callback_data": "clarify:waiting_tag_cat:Professional"}]
            ]
        }
        await http_client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": f"⏳ Waiting on: <b>{person_clean}</b>\n\nOptionally link to an active project?",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return True

    if input_type == "new_project_name":
        session["item_data"]["new_project_name"] = text.strip().title()
        session["awaiting_text_input"] = "new_project_outcome"
        
        await http_client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": f"🆕 <b>Step 2 of 2 — {session['item_data']['new_project_name']}:</b>\nPlease reply with the <b>Desired Outcome</b> (e.g. <code>Fully equipped home gym with squat rack and rubber flooring</code>):",
                "parse_mode": "HTML"
            }
        )
        return True

    if input_type == "new_project_outcome":
        category = item_data.get("new_project_category", "Personal")
        proj_name = item_data.get("new_project_name", "New Project")
        outcome = text.strip()
        
        # Create the project note with its outcome; the captured item becomes its first
        # roadmap step.
        clarify_ops.to_new_project(item_line, name=proj_name, category=category, outcome=outcome)
        
        clean_text = re.sub(r'\(Captured on.*?\)', '', item_line)
        clean_text = re.sub(r'\(Source:.*?\)', '', clean_text).strip("- \n")
        
        await http_client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": f"🆕 Created <b>{category} Project: {proj_name}</b>\n🎯 Outcome: <i>{outcome}</i>\n📌 Appended roadmap step:\n{clean_text}",
                "parse_mode": "HTML"
            }
        )
        
        session["awaiting_text_input"] = None
        session["current_index"] += 1
        await process_next_clarify_item(chat_id, token, http_client)
        return True

    if input_type == "defer_custom_date":
        parsed_date = parse_natural_date_str(text)
        date_str = parsed_date.strftime("%Y-%m-%d")
        session["item_data"]["custom_defer_date"] = date_str
        session["awaiting_text_input"] = None
        
        reply_markup = {
            "inline_keyboard": [
                [{"text": "🏠 Personal", "callback_data": "clarify:save_custom_defer:Personal"},
                 {"text": "💼 Professional", "callback_data": "clarify:save_custom_defer:Professional"}]
            ]
        }
        await http_client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": f"🏷️ <b>Select Category for Deferred Item ({date_str}):</b>",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return True
        
    return False
