import os
import re
import shutil
from datetime import datetime, timedelta
import httpx
from backend.config import OBSIDIAN_VAULT_PATH
from backend.utils.sessions import SessionStore
from backend.vault.tasks import (
    append_project_step,
    archive_completed_lines,
    ensure_project_note,
    find_project_note,
    iter_projects,
    iter_task_lines,
    parse_flexible_date,
    read_project,
    remove_step_from_project,
    strip_task_metadata,
)

# Placeholder lines the dashboard writes into empty GTD lists; never real items.
PLACEHOLDER_MARKERS = ("🎉", "Inbox Zero", "All clear", "List clear", "*None*")


def _is_placeholder(line: str) -> bool:
    return any(marker in line for marker in PLACEHOLDER_MARKERS)

def get_today_str():
    return datetime.now().strftime("%Y-%m-%d")

async def send_msg(chat_id: int, token: str, client: httpx.AsyncClient, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup: payload["reply_markup"] = reply_markup
    await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)

NEXT_CATEGORY_LABELS = {"personal": "Personal", "professional": "Professional", "both": "Both"}

async def handle_next_command(chat_id: int, text: str, token: str, http_client: httpx.AsyncClient, show_category_picker: bool = True):
    cmd_parts = text.split(maxsplit=1)
    context_filter = cmd_parts[1].strip() if len(cmd_parts) > 1 else None

    # Bare `/next` (no @context arg) -> prompt for Personal / Professional / Both first
    if not context_filter and show_category_picker:
        buttons = [
            [{"text": "🏠 Personal", "callback_data": "next:personal"},
             {"text": "💼 Professional", "callback_data": "next:professional"}],
            [{"text": "🔀 Both", "callback_data": "next:both"}]
        ]
        await send_msg(chat_id, token, http_client, "⚡ <b>Next Actions</b>\n\nFilter by category:", reply_markup={"inline_keyboard": buttons})
        return

    await send_next_actions(chat_id, token, http_client, context_filter=context_filter)

async def send_next_actions(chat_id: int, token: str, http_client: httpx.AsyncClient, category_filter: str = None, context_filter: str = None, edit_message_id: int = None):
    tasks_file = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Next_Actions.md")
    if not os.path.exists(tasks_file):
        await _send_or_edit(chat_id, token, http_client, "No Next_Actions.md found.", edit_message_id)
        return

    with open(tasks_file, encoding="utf-8") as f:
        lines = f.readlines()

    today = get_today_str()
    filtered_tasks = []

    for line in lines:
        if not line.strip().startswith("- [ ]"): continue

        # Check tickler date
        m = re.search(r'🔔 (\d{4}-\d{2}-\d{2})', line)
        if m and m.group(1) > today:
            continue

        if context_filter:
            if context_filter not in line:
                continue

        if category_filter and category_filter != "both":
            if f"#{category_filter}" not in line:
                continue

        filtered_tasks.append(line.strip())

    label_bits = []
    if category_filter:
        label_bits.append(NEXT_CATEGORY_LABELS.get(category_filter, category_filter.title()))
    if context_filter:
        label_bits.append(context_filter)
    label = f" ({', '.join(label_bits)})" if label_bits else ""

    if not filtered_tasks:
        await _send_or_edit(chat_id, token, http_client, f"⚡ <b>Next Actions</b>{label}\n\nNo active next actions found.", edit_message_id)
        return

    msg = f"⚡ <b>Next Actions</b>{label}\n\n"
    for t in filtered_tasks:
        clean_t = re.sub(r'\[\[.*?\]\]', '', t).replace('(Source: )', '').replace('- [ ]', '•').strip()
        msg += f"{clean_t}\n"

    await _send_or_edit(chat_id, token, http_client, msg, edit_message_id)

async def _send_or_edit(chat_id: int, token: str, http_client: httpx.AsyncClient, text: str, edit_message_id: int = None):
    if edit_message_id:
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": edit_message_id, "text": text, "parse_mode": "HTML", "reply_markup": {"inline_keyboard": []}}
        )
    else:
        await send_msg(chat_id, token, http_client, text)

async def handle_next_callback(callback_query: dict, token: str, http_client: httpx.AsyncClient):
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    data = callback_query.get("data", "")
    category = data.split(":", 1)[1] if ":" in data else "both"
    if category not in NEXT_CATEGORY_LABELS:
        category = "both"
    await send_next_actions(chat_id, token, http_client, category_filter=category, edit_message_id=message_id)

async def handle_waiting_command(chat_id: int, token: str, http_client: httpx.AsyncClient):
    file_path = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Waiting_For.md")
    if not os.path.exists(file_path):
        await send_msg(chat_id, token, http_client, "No Waiting_For.md found.")
        return
        
    with open(file_path, encoding="utf-8") as f:
        lines = f.readlines()
        
    today_str = get_today_str()
    results = []
    
    for line in lines:
        if not line.strip().startswith("- [ ]"): continue
        
        m_tick = re.search(r'🔔 (\d{4}-\d{2}-\d{2})', line)
        if m_tick and m_tick.group(1) > today_str:
            continue
            
        # Try to find who we are waiting on
        who = "Someone"
        m_who = re.search(r'#waiting\((.*?)\)', line)
        if m_who: who = m_who.group(1)
        
        # Follow up date
        follow_up = ""
        m_date = re.search(r'📅 (\d{4}-\d{2}-\d{2})', line)
        if m_date: follow_up = f" (Follow-up: {m_date.group(1)})"
        
        results.append(f"• <b>{who}</b>: {line.replace('- [ ]', '').strip()}{follow_up}")
        
    if not results:
        await send_msg(chat_id, token, http_client, "No delegated items found.")
        return
        
    msg = "⏳ <b>Waiting For</b>\n\n" + "\n".join(results)
    await send_msg(chat_id, token, http_client, msg)

async def handle_project_command(chat_id: int, text: str, token: str, http_client: httpx.AsyncClient):
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await send_msg(chat_id, token, http_client, "Usage: /project <name> [add <step>]")
        return
        
    proj_query = parts[1].strip()
    
    # Check if this is an "add step" command
    m_add = re.search(r'^(.*?) add (.*)$', proj_query, re.IGNORECASE)
    is_add = False
    step_to_add = ""
    if m_add:
        proj_query = m_add.group(1).strip()
        step_to_add = m_add.group(2).strip()
        is_add = True
        
    print(f"Backend [Project]: query={proj_query!r} is_add={is_add} step={step_to_add[:80]!r}")

    # Find (or create) the project master note via the shared resolver
    found_path = ensure_project_note(proj_query, category="Personal")
    print(f"Backend [Project]: resolved note -> {found_path}")

    if is_add:
        append_project_step(proj_query, step_to_add)
        print(f"Backend [Project]: appended step to {found_path}")
        await send_msg(chat_id, token, http_client, f"✅ Added step to roadmap:\n`{step_to_add}`")
        return

    # Show project view (frontmatter parsed as YAML, not by regex, so it survives
    # titles containing quotes or colons)
    project = read_project(found_path)
    if not project:
        await send_msg(chat_id, token, http_client, f"❌ Could not read project note at `{found_path}`.")
        return

    title = project.name
    outcome = project.outcome
    steps = project.steps

    msg = f"📁 <b>{title}</b>\n🎯 Outcome: <i>{outcome}</i>\n\n"
    
    # 1. Active Actions search across task files
    active_actions = []
    proj_tag_space = f"#project/{title}".lower()
    proj_tag_underscore = f"#project/{title.replace(' ', '_')}".lower()
    
    for task_file in ["Next_Actions.md", "Waiting_For.md"]:
        tf_path = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", task_file)
        if os.path.exists(tf_path):
            with open(tf_path, encoding="utf-8") as f:
                for line in f:
                    line_lower = line.lower()
                    if line_lower.strip().startswith("- [ ]") and (proj_tag_space in line_lower or proj_tag_underscore in line_lower):
                        clean_action = re.sub(r'\(Captured on.*?\)', '', line)
                        clean_action = re.sub(r'\(Source:.*?\)', '', clean_action).strip("- \n")
                        active_actions.append(clean_action)

    if active_actions:
        msg += "⚡ <b>Active Actions:</b>\n" + "\n".join(f"• {a}" for a in active_actions) + "\n\n"
    else:
        msg += "⚡ <b>Active Actions:</b> <i>None active currently</i>\n\n"

    # 2. Roadmap Steps from Project Note
    if steps:
        msg += "📌 <b>Roadmap Steps:</b>\n" + "\n".join(f"• {s}" for s in steps[:10])
        if len(steps) > 10: msg += f"\n...and {len(steps)-10} more."
    else:
        msg += "📌 <b>Roadmap Steps:</b> <i>No roadmap steps defined.</i>"
        
    await send_msg(chat_id, token, http_client, msg)

# Multi-step flow state, expiring so abandoned flows do not leak (see utils/sessions.py)
PROMOTION_SESSIONS = SessionStore("promotion")

# Pending /tickle deferrals, keyed by chat_id -> task search string
TICKLE_SESSIONS = SessionStore("tickle")

async def handle_done_command(chat_id: int, text: str, token: str, http_client: httpx.AsyncClient):
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await send_msg(chat_id, token, http_client, "Usage: `/done <task text>`")
        return
        
    task_query = parts[1].strip().lower()
    
    files_to_check = [
        os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Next_Actions.md"),
        os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Waiting_For.md")
    ]
    
    found = False
    task_line = ""
    project_tag = None
    
    for fp in files_to_check:
        if not os.path.exists(fp): continue
        with open(fp, encoding="utf-8") as f:
            lines = f.readlines()
            
        new_lines = []
        for line in lines:
            if task_query in line.lower() and "- [ ]" in line and not found:
                found = True
                task_line = line.strip()
                m_proj = re.search(r'#project/([a-zA-Z0-9_\-]+)', line)
                if m_proj: project_tag = m_proj.group(1).replace('_', ' ')
            else:
                new_lines.append(line)
                
        if found:
            has_active = any("- [ ]" in line for line in new_lines)
            if not has_active:
                fname = os.path.basename(fp)
                if fname == "Next_Actions.md":
                    new_lines = ["# ⚡ Next Actions\n\n", "> Flexible, context-tagged action list. Filter in Telegram using `/next @context`.\n\n", "---\n\n", "## ⚡ Next Actions\n\n", "🎉 *All clear! No active next actions remaining.*\n"]
                elif fname == "Waiting_For.md":
                    new_lines = ["# ⏳ Waiting For\n\n", "> Delegated actions and items owed to you by others. View in Telegram using `/waiting`.\n\n", "---\n\n", "## ⏳ Waiting For\n\n", "🎉 *All clear! No pending items waiting on others.*\n"]
                elif fname == "Someday_Maybe.md":
                    new_lines = ["# 💡 Someday / Maybe\n\n", "> Future ideas, uncommitted desires, and projects to review during `/reflect`.\n\n", "---\n\n", "## 💡 Someday / Maybe\n\n", "🎉 *List clear! No someday/maybe items recorded.*\n"]
            with open(fp, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            break
            
    if not found:
        await send_msg(chat_id, token, http_client, f"❌ Task matching '`{task_query}`' not found in active task lists.")
        return
        
    clean_task_name = strip_task_metadata(task_line)
    archive_completed_lines([task_line])

    await send_msg(chat_id, token, http_client, f"✅ <b>Task Completed:</b>\n<s>{clean_task_name}</s>")
    
    # Project auto-promotion flow with interactive Telegram buttons
    if project_tag:
        await prompt_project_step_promotion(chat_id, project_tag, token, http_client)

async def prompt_project_step_promotion(chat_id: int, project_tag: str, token: str = None, http_client: httpx.AsyncClient = None):
    from backend.config import TELEGRAM_BOT_TOKEN
    tok = token or TELEGRAM_BOT_TOKEN
    if not tok or not chat_id:
        return
        
    proj_found_path = find_project_note(project_tag)
    project = read_project(proj_found_path)
    if not project:
        return

    proj_clean = project.name
    category = project.category
    candidate_steps = project.steps

    # Check currently active tasks in Next_Actions.md to avoid duplicate promotion
    from backend.config import TASKS_MD_FILE
    active_next_texts = [
        t.raw.lower() for t in iter_task_lines(TASKS_MD_FILE, include_checked=False)
    ]

    unpromoted_steps = [s for s in candidate_steps if not any(s.lower() in ant for ant in active_next_texts)]

    if unpromoted_steps:
        PROMOTION_SESSIONS[chat_id] = {
            "project_tag": project_tag.replace(" ", "_"),
            "project_title": proj_clean,
            "project_path": proj_found_path,
            "category": category,
            "steps": unpromoted_steps[:8]
        }
        
        keyboard = []
        for i, step_str in enumerate(unpromoted_steps[:5]):
            btn_text = f"{i+1}. " + (step_str[:36] + "..." if len(step_str) > 36 else step_str)
            keyboard.append([{"text": btn_text, "callback_data": f"promote:step:{i}"}])
        
        keyboard.append([{"text": "⏭️ Skip / None for now", "callback_data": "promote:skip"}])
        
        reply_markup = {"inline_keyboard": keyboard}
        
        payload = {
            "chat_id": chat_id,
            "text": f"🎯 <b>Project Roadmap: #{project_tag.replace(' ', '_')}</b>\n\nWhich step would you like to promote to <b>Next Actions</b>?",
            "parse_mode": "HTML",
            "reply_markup": reply_markup
        }
        
        if http_client:
            await http_client.post(f"https://api.telegram.org/bot{tok}/sendMessage", json=payload)
        else:
            async with httpx.AsyncClient() as client:
                await client.post(f"https://api.telegram.org/bot{tok}/sendMessage", json=payload)


async def handle_promote_callback(callback_query: dict, token: str, http_client: httpx.AsyncClient):
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    data = callback_query.get("data", "")
    
    parts = data.split(":")
    action = parts[1]
    
    session = PROMOTION_SESSIONS.get(chat_id)
    
    if action == "skip":
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "🎯 No next steps promoted. Active project roadmap kept intact.",
                "parse_mode": "HTML"
            }
        )
        if chat_id in PROMOTION_SESSIONS:
            del PROMOTION_SESSIONS[chat_id]
        return

    if not session:
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "❌ Promotion session expired. Use `/project <name>` to view roadmap.",
                "parse_mode": "HTML"
            }
        )
        return

    if action == "step":
        step_idx = int(parts[2])
        if step_idx < len(session["steps"]):
            selected_step = session["steps"][step_idx]
            session["selected_step"] = selected_step
            
            reply_markup = {
                "inline_keyboard": [
                    [{"text": "@home", "callback_data": "promote:ctx:@home"},
                     {"text": "@computer", "callback_data": "promote:ctx:@computer"},
                     {"text": "@errands", "callback_data": "promote:ctx:@errands"}],
                    [{"text": "@phone", "callback_data": "promote:ctx:@phone"},
                     {"text": "@anywhere", "callback_data": "promote:ctx:@anywhere"},
                     {"text": "@office", "callback_data": "promote:ctx:@office"}]
                ]
            }
            await http_client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"🏷️ <b>Select Context for:</b>\n<i>\"{selected_step}\"</i>",
                    "parse_mode": "HTML",
                    "reply_markup": reply_markup
                }
            )
        return

    if action == "ctx":
        chosen_ctx = parts[2]
        selected_step = session.get("selected_step", "")
        project_tag = session.get("project_tag", "")
        category = session.get("category", "Personal")
        proj_path = session.get("project_path")
        
        clean_step = strip_task_metadata(selected_step)
        
        # 1. Add to Next_Actions.md
        task_str = f"- [ ] {clean_step} {chosen_ctx} #project/{project_tag} #{category.lower()}"
        tasks_md = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Next_Actions.md")
        
        from backend.utils.tasks_dashboard import append_gtd_item
        append_gtd_item(tasks_md, task_str)

        # 2. Delete the promoted step from ## Project Steps in the project master note
        if not proj_path or not os.path.exists(proj_path):
            proj_path = find_project_note(project_tag)

        remove_step_from_project(proj_path, clean_step)


        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"⚡ <b>Promoted to Next Actions:</b>\n• {clean_step} {chosen_ctx} #project/{project_tag}\n\n<i>Removed from project roadmap backlog.</i>",
                "parse_mode": "HTML"
            }
        )
        if chat_id in PROMOTION_SESSIONS:
            del PROMOTION_SESSIONS[chat_id]
        return

async def handle_tickle_command(chat_id: int, text: str, token: str, http_client: httpx.AsyncClient):
    cmd_parts = text.split(maxsplit=1)
    if len(cmd_parts) < 2:
        await send_msg(
            chat_id, token, http_client,
            "⚠️ Usage: `/tickle <task_search>`\n\n"
            "Example:\n"
            "• `/tickle Buy birthday card`"
        )
        return

    task_query = cmd_parts[1].strip()
    TICKLE_SESSIONS[chat_id] = task_query
    
    today = datetime.now().date()
    from backend.utils.telegram_calendar import generate_telegram_calendar
    cal_kb = generate_telegram_calendar(today.year, today.month, "tickle_cal")
    
    await send_msg(
        chat_id, token, http_client,
        f"📅 <b>Select Deferral Date for:</b>\n<i>\"{task_query}\"</i>",
        reply_markup=cal_kb
    )

async def handle_tickle_callback(callback_query: dict, token: str, http_client: httpx.AsyncClient):
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    data = callback_query.get("data", "")

    parts = data.split(":")
    cal_action = parts[1]
    
    if cal_action == "nav":
        year = int(parts[2])
        month = int(parts[3])
        task_query = TICKLE_SESSIONS.get(chat_id, "Task")
        from backend.utils.telegram_calendar import generate_telegram_calendar
        cal_kb = generate_telegram_calendar(year, month, "tickle_cal")
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"📅 <b>Select Deferral Date for:</b>\n<i>\"{task_query}\"</i>",
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

        task_query = TICKLE_SESSIONS.get(chat_id, "").strip().lower()
        if not task_query:
            await http_client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={"chat_id": chat_id, "message_id": message_id, "text": "❌ Session expired. Run `/tickle <task_search>` again."}
            )
            return

        tasks_dir = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks")
        found_file = None
        matched_line = None

        for fname in ["Next_Actions.md", "Waiting_For.md", "Someday_Maybe.md"]:
            fp = os.path.join(tasks_dir, fname)
            if not os.path.exists(fp):
                continue
            with open(fp, encoding="utf-8") as f:
                lines = f.readlines()

            new_lines = []
            for line in lines:
                if task_query in line.lower() and line.strip().startswith("- ["):
                    found_file = fp
                    matched_line = line.strip()
                    if "🔔" in line:
                        line = re.sub(r'🔔\s*\d{4}-\d{2}-\d{2}', f'🔔 {iso_str}', line)
                    else:
                        line = line.strip() + f" 🔔 {iso_str}\n"
                new_lines.append(line)

            if found_file:
                from backend.vault.writer import atomic_write
                atomic_write(fp, "".join(new_lines))
                break

        if matched_line:
            clean_task = re.sub(r'🔔\s*\d{4}-\d{2}-\d{2}', '', strip_task_metadata(matched_line)).strip()
            await http_client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"🔔 <b>Deferred until {date_display}:</b>\n{clean_task}",
                    "parse_mode": "HTML"
                }
            )
        else:
            await http_client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"❌ Task matching '<code>{task_query}</code>' not found in active task lists.",
                    "parse_mode": "HTML"
                }
            )

async def handle_daily_command(chat_id: int, token: str, http_client: httpx.AsyncClient):
    from backend.brain_cleaner import generate_daily_digest
    await generate_daily_digest(target_chat_id=chat_id)

async def handle_status_command(chat_id: int, token: str, http_client: httpx.AsyncClient):
    tasks_dir = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks")
    proj_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
    
    def get_list_items(filename: str) -> list:
        fp = os.path.join(tasks_dir, filename)
        items = []
        if os.path.exists(fp):
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    line_s = line.strip()
                    if line_s.startswith("- [ ]") or line_s.startswith("- "):
                        clean = re.sub(r'\(Source:.*?\)', '', line_s)
                        clean = re.sub(r'\(Captured on.*?\)', '', clean)
                        clean = clean.replace('- [ ]', '').replace('- ', '').strip()
                        if clean:
                            items.append(clean)
        return items

    captured_items = get_list_items("Captured.md")
    next_items = get_list_items("Next_Actions.md")
    waiting_items = get_list_items("Waiting_For.md")
    someday_items = get_list_items("Someday_Maybe.md")
    
    completed_7d_count = 0
    today = datetime.now().date()
    cutoff_7d = today - timedelta(days=7)
    
    comp_fp = os.path.join(tasks_dir, "z_Completed.md")
    if os.path.exists(comp_fp):
        with open(comp_fp, encoding="utf-8") as f:
            for line in f:
                line_s = line.strip()
                if line_s.startswith("- [x]") or line_s.startswith("- [X]"):
                    date_match = re.search(r'\(Completed on (\d{4}-\d{2}-\d{2})\)', line_s)
                    if date_match:
                        try:
                            comp_date = datetime.strptime(date_match.group(1), "%Y-%m-%d").date()
                            if comp_date >= cutoff_7d:
                                completed_7d_count += 1
                        except ValueError:
                            completed_7d_count += 1
                    else:
                        completed_7d_count += 1

    msg = "📊 <b>GTD System Status</b>\n\n"
    
    msg += f"📥 <b>Captured ({len(captured_items)}):</b>\n"
    if captured_items:
        msg += "\n".join(f"• {item}" for item in captured_items[:10]) + "\n\n"
    else:
        msg += "• <i>None</i>\n\n"
        
    msg += f"⚡ <b>Next Actions ({len(next_items)}):</b>\n"
    if next_items:
        msg += "\n".join(f"• {item}" for item in next_items[:10]) + "\n\n"
    else:
        msg += "• <i>None</i>\n\n"

    msg += f"⏳ <b>Waiting For ({len(waiting_items)}):</b>\n"
    if waiting_items:
        msg += "\n".join(f"• {item}" for item in waiting_items[:10]) + "\n\n"
    else:
        msg += "• <i>None</i>\n\n"

    msg += f"💡 <b>Someday/Maybe ({len(someday_items)}):</b>\n"
    if someday_items:
        msg += "\n".join(f"• {item}" for item in someday_items[:10]) + "\n\n"
    else:
        msg += "• <i>None</i>\n\n"

    msg += f"✅ <b>Completed Tasks (Last 7 Days):</b> {completed_7d_count} item(s)\n\n"

    # Top-level projects only (no subfolder scanning)
    msg += "📁 <b>Active Projects:</b>\n"
    projects = []
    
    for category in ["Personal", "Professional"]:
        cat_dir = os.path.join(proj_dir, category)
        if os.path.exists(cat_dir):
            for entry in os.listdir(cat_dir):
                if entry.startswith(".") or entry.startswith("_"):
                    continue
                entry_path = os.path.join(cat_dir, entry)
                if os.path.isdir(entry_path) or entry.endswith(".md"):
                    p_name = entry.replace(".md", "")
                    projects.append(f"• <b>{p_name}</b> ({category})")
                    
    # Also check top-level 02_Projects for standalone notes/folders
    for entry in os.listdir(proj_dir):
        if entry not in ["Personal", "Professional"] and not entry.startswith(".") and not entry.startswith("_"):
            p_name = entry.replace(".md", "")
            projects.append(f"• <b>{p_name}</b>")
            
    if projects:
        msg += "\n".join(projects)
    else:
        msg += "<i>No active projects found.</i>"
        
    await send_msg(chat_id, token, http_client, msg)

async def handle_archive_project_command(chat_id: int, text: str, token: str, http_client: httpx.AsyncClient):
    cmd_parts = text.split(maxsplit=1)
    if len(cmd_parts) < 2:
        await send_msg(chat_id, token, http_client, "⚠️ Please specify a project name to archive:\nExample: `/archive Garage` or `/archive Police AI Director`")
        return

    proj_input = cmd_parts[1].strip()
    proj_clean = proj_input.replace(" ", "_").lower()
    
    proj_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
    found_path = None
    category = "Personal"
    
    # 1. Search Personal & Professional subfolders
    for cat in ["Personal", "Professional"]:
        cat_path = os.path.join(proj_dir, cat)
        if os.path.exists(cat_path):
            for entry in os.listdir(cat_path):
                e_clean = entry.replace(".md", "").strip().lower().replace(" ", "_")
                if e_clean == proj_clean or entry.strip().lower() == proj_input.lower():
                    found_path = os.path.join(cat_path, entry)
                    category = cat
                    break
        if found_path:
            break

    # 2. Search top-level 02_Projects
    if not found_path and os.path.exists(proj_dir):
        for entry in os.listdir(proj_dir):
            if entry not in ["Personal", "Professional"] and not entry.startswith(".") and not entry.startswith("_"):
                e_clean = entry.replace(".md", "").strip().lower().replace(" ", "_")
                if e_clean == proj_clean or entry.strip().lower() == proj_input.lower():
                    found_path = os.path.join(proj_dir, entry)
                    category = "Personal"
                    break

    if not found_path or not os.path.exists(found_path):
        await send_msg(chat_id, token, http_client, f"❌ Project note or folder not found for `{proj_input}` in `02_Projects/`.")
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    proj_name = os.path.basename(found_path).replace(".md", "")

    # Update status in markdown note if single file or if folder has <ProjectName>.md
    note_to_update = found_path if os.path.isfile(found_path) else os.path.join(found_path, f"{proj_name}.md")
    if os.path.exists(note_to_update):
        with open(note_to_update, encoding="utf-8") as f:
            content = f.read()
        
        if "status:" in content:
            content = re.sub(r'status:\s*"?.*?"?', 'status: "completed"', content)
        else:
            content = content.replace("---", f"---\nstatus: \"completed\"\ndate_completed: \"{today_str}\"", 1)

        if "date_completed:" not in content:
            content = re.sub(r'(status:\s*"completed")', r'\1\ndate_completed: "' + today_str + '"', content)

        with open(note_to_update, "w", encoding="utf-8") as f:
            f.write(content)

    # Move filesystem entry to 05_Archive/02_Projects/<Category>/
    archive_dir = os.path.join(OBSIDIAN_VAULT_PATH, "05_Archive", "02_Projects", category)
    os.makedirs(archive_dir, exist_ok=True)
    
    dest_path = os.path.join(archive_dir, os.path.basename(found_path))
    if os.path.exists(dest_path):
        if os.path.isdir(dest_path):
            shutil.rmtree(dest_path)
        else:
            os.remove(dest_path)
    shutil.move(found_path, dest_path)

    rel_dest = os.path.relpath(dest_path, OBSIDIAN_VAULT_PATH)
    
    # Re-run index autoupdater & tasks dashboard to ensure clean state
    from backend.brain_cleaner import auto_update_index
    from backend.utils.tasks_dashboard import maintain_tasks_dashboard
    await auto_update_index()
    await maintain_tasks_dashboard()

    msg = (
        f"📦 <b>Project Archived: {proj_name}</b>\n\n"
        f"• <b>Category:</b> {category}\n"
        f"• <b>Vault Location:</b> <code>{rel_dest}</code>\n"
        f"• <b>Status:</b> <code>completed</code>\n"
        f"• <b>Date Completed:</b> {today_str}\n\n"
        f"<i>Notes and vector embeddings remain 100% searchable via RAG Q&A.</i>"
    )
    await send_msg(chat_id, token, http_client, msg)

async def handle_holiday_command(chat_id: int, text: str, token: str, http_client: httpx.AsyncClient):
    from backend.utils.holiday_manager import set_holiday_pause, clear_holiday_pause, is_scope_paused
    from backend.ingestion.handlers.clarify import parse_natural_date_str

    cmd_parts = text.split()
    
    if len(cmd_parts) > 1 and cmd_parts[1].lower() in ["end", "resume", "clear", "cancel"]:
        scope = cmd_parts[2].lower() if len(cmd_parts) > 2 else "all"
        clear_holiday_pause(scope)
        await send_msg(chat_id, token, http_client, f"🌴 <b>Holiday Mode Resumed!</b>\nAll daily and weekly reviews have been reactivated for <b>{scope.replace('#', '')}</b>.")
        return

    if len(cmd_parts) > 1 and cmd_parts[1].lower() == "status":
        pers_paused, pers_date = is_scope_paused("personal")
        prof_paused, prof_date = is_scope_paused("professional")
        all_paused, all_date = is_scope_paused("all")
        
        status_msg = "🌴 <b>Holiday Mode Status</b>\n\n"
        if all_paused:
            status_msg += f"• <b>All Reviews:</b> ⏸️ Paused until <code>{all_date}</code>\n"
        else:
            status_msg += "• <b>All Reviews:</b> ▶️ Active\n"
            
        if pers_paused:
            status_msg += f"• <b>Personal Reviews:</b> ⏸️ Paused until <code>{pers_date}</code>\n"
        else:
            status_msg += "• <b>Personal Reviews:</b> ▶️ Active\n"
            
        if prof_paused:
            status_msg += f"• <b>Professional Reviews:</b> ⏸️ Paused until <code>{prof_date}</code>\n"
        else:
            status_msg += "• <b>Professional Reviews:</b> ▶️ Active\n"
            
        status_msg += "\n<i>To resume reviews early, run `/holiday resume`.</i>"
        await send_msg(chat_id, token, http_client, status_msg)
        return

    if len(cmd_parts) >= 3:
        scope = cmd_parts[1].lower().replace("#", "").strip()
        date_str = cmd_parts[2].strip()
        
        until_date = parse_natural_date_str(date_str)
        date_display = until_date.strftime("%d-%m-%Y")
        
        set_holiday_pause(scope, until_date)
        
        await send_msg(
            chat_id, token, http_client,
            f"🌴 <b>Holiday Mode Activated!</b>\n\n"
            f"• <b>Scope:</b> <code>#{scope}</code>\n"
            f"• <b>Paused Until:</b> <code>{date_display}</code> ({until_date.strftime('%A')})\n\n"
            f"<i>Daily/weekly reviews will pause matching items until {date_display}. Run `/holiday resume` anytime to reactivate early.</i>"
        )
        return

    reply_markup = {
        "inline_keyboard": [
            [{"text": "🏠 Personal", "callback_data": "holiday:scope:personal"},
             {"text": "💼 Professional", "callback_data": "holiday:scope:professional"}],
            [{"text": "🌴 All Reviews", "callback_data": "holiday:scope:all"}],
            [{"text": "📊 Check Status", "callback_data": "holiday:status"},
             {"text": "▶️ Resume All Reviews Now", "callback_data": "holiday:resume:all"}]
        ]
    }
    await send_msg(
        chat_id, token, http_client,
        "🌴 <b>Holiday Mode Setup</b>\nSelect which review scope to pause:",
        reply_markup=reply_markup
    )

async def handle_holiday_callback(callback_query: dict, token: str, http_client: httpx.AsyncClient):
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    data = callback_query.get("data", "")
    
    from backend.utils.holiday_manager import set_holiday_pause, clear_holiday_pause, is_scope_paused
    from backend.utils.telegram_calendar import generate_telegram_calendar

    today = datetime.now().date()

    if data.startswith("holiday:scope:"):
        scope = data.split(":")[2]
        reply_markup = {
            "inline_keyboard": [
                [{"text": "🌴 1 Week (+7d)", "callback_data": f"holiday:duration:{scope}:7"},
                 {"text": "🌴 2 Weeks (+14d)", "callback_data": f"holiday:duration:{scope}:14"}],
                [{"text": "📅 Open Calendar Picker", "callback_data": f"holiday:open_cal:{scope}"},
                 {"text": "✍️ Custom Text Date", "callback_data": f"holiday:type_date:{scope}"}]
            ]
        }
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"🌴 <b>Select Duration to Pause #{scope}:</b>",
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
        )
        return

    if data.startswith("holiday:duration:"):
        parts = data.split(":")
        scope = parts[2]
        days = int(parts[3])
        
        until_date = today + timedelta(days=days)
        set_holiday_pause(scope, until_date)
        date_display = until_date.strftime("%d-%m-%Y")
        day_name = until_date.strftime("%A")
        
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"🌴 <b>Holiday Mode Activated!</b>\n\n• <b>Scope:</b> <code>#{scope}</code>\n• <b>Paused Until:</b> <code>{date_display}</code> ({day_name})\n\n<i>Daily/weekly reviews will exclude matching items until {date_display}. Run `/holiday resume` anytime to reactivate early.</i>",
                "parse_mode": "HTML"
            }
        )
        return

    if data.startswith("holiday:type_date:"):
        scope = data.split(":")[2]
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"📅 <b>Reply with Holiday Return Date for #{scope}:</b>\ne.g. <code>/holiday #{scope} 23-08-2026</code> or <code>/holiday #{scope} 23rd Aug</code>",
                "parse_mode": "HTML"
            }
        )
        return

    if data.startswith("holiday:open_cal:"):
        scope = data.split(":")[2]
        cal_kb = generate_telegram_calendar(today.year, today.month, f"holiday_cal:{scope}")
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"📅 <b>Select Holiday Return Date for #{scope}:</b>",
                "parse_mode": "HTML",
                "reply_markup": cal_kb
            }
        )
        return

    if data.startswith("holiday_cal:"):
        parts = data.split(":")
        scope = parts[1]
        cal_action = parts[2]
        
        if cal_action == "nav":
            year = int(parts[3])
            month = int(parts[4])
            cal_kb = generate_telegram_calendar(year, month, f"holiday_cal:{scope}")
            await http_client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"📅 <b>Select Holiday Return Date for #{scope}:</b>",
                    "parse_mode": "HTML",
                    "reply_markup": cal_kb
                }
            )
            return

        if cal_action == "pick":
            iso_str = parts[3]
            until_date = datetime.strptime(iso_str, "%Y-%m-%d").date()
            set_holiday_pause(scope, until_date)
            
            date_display = until_date.strftime("%d-%m-%Y")
            day_name = until_date.strftime("%A")
            
            await http_client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"🌴 <b>Holiday Mode Activated!</b>\n\n• <b>Scope:</b> <code>#{scope}</code>\n• <b>Paused Until:</b> <code>{date_display}</code> ({day_name})\n\n<i>Daily/weekly reviews will exclude matching items until {date_display}. Run `/holiday resume` anytime to reactivate early.</i>",
                    "parse_mode": "HTML"
                }
            )
            return

    if data == "holiday:status":
        pers_paused, pers_date = is_scope_paused("personal")
        prof_paused, prof_date = is_scope_paused("professional")
        all_paused, all_date = is_scope_paused("all")
        
        status_msg = "🌴 <b>Holiday Mode Status</b>\n\n"
        if all_paused:
            status_msg += f"• <b>All Reviews:</b> ⏸️ Paused until <code>{all_date}</code>\n"
        else:
            status_msg += "• <b>All Reviews:</b> ▶️ Active\n"
            
        if pers_paused:
            status_msg += f"• <b>Personal Reviews:</b> ⏸️ Paused until <code>{pers_date}</code>\n"
        else:
            status_msg += "• <b>Personal Reviews:</b> ▶️ Active\n"
            
        if prof_paused:
            status_msg += f"• <b>Professional Reviews:</b> ⏸️ Paused until <code>{prof_date}</code>\n"
        else:
            status_msg += "• <b>Professional Reviews:</b> ▶️ Active\n"
            
        status_msg += "\n<i>To resume reviews early, run `/holiday resume`.</i>"
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": status_msg, "parse_mode": "HTML"}
        )
        return

    if data.startswith("holiday:resume:"):
        scope = data.split(":")[2]
        clear_holiday_pause(scope)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": f"🌴 <b>Holiday Mode Resumed!</b>\nAll daily and weekly reviews have been reactivated for <b>{scope}</b>.", "parse_mode": "HTML"}
        )
        return

def get_reflect_summary_data() -> dict:
    """Gathers structured review data across the GTD task lists and projects.

    All parsing goes through backend.vault.tasks so this view can never drift from
    what /status, /done and the dashboard sweeper see.
    """
    from backend.config import (
        CAPTURED_MD_FILE,
        COMPLETED_TASKS_MD_FILE,
        SOMEDAY_MAYBE_MD_FILE,
        TASKS_MD_FILE,
        WAITING_FOR_MD_FILE,
    )

    today_dt = datetime.now()
    today = today_dt.date()
    seven_days_ago = today - timedelta(days=7)

    # 1. Captured items (plain bullets, not checkboxes)
    captured_items = []
    if os.path.exists(CAPTURED_MD_FILE):
        with open(CAPTURED_MD_FILE, encoding="utf-8") as f:
            for line in f:
                ls = line.strip()
                if ls.startswith("- ") and not _is_placeholder(ls):
                    clean = strip_task_metadata(ls)
                    if clean:
                        captured_items.append(clean)

    # 2. Next actions (excluding items deferred by a future tickler date)
    next_items = []
    active_project_tags = set()
    for task in iter_task_lines(TASKS_MD_FILE, include_checked=False):
        if task.is_deferred:
            continue
        if task.project:
            active_project_tags.add(task.project.lower())
        next_items.append(task.raw)

    # 3. Waiting items, flagging anything captured more than 5 days ago
    waiting_items = []
    stale_waitings = []
    for task in iter_task_lines(WAITING_FOR_MD_FILE, include_checked=False):
        waiting_items.append(task.raw)
        m_cap = re.search(r'\(Captured .*? on (\d{4}-\d{2}-\d{2})\)', task.raw)
        if m_cap:
            cap_date = parse_flexible_date(m_cap.group(1))
            if cap_date and (today - cap_date).days > 5:
                stale_waitings.append(task.raw)

    # 4. Someday items
    someday_items = []
    if os.path.exists(SOMEDAY_MAYBE_MD_FILE):
        with open(SOMEDAY_MAYBE_MD_FILE, encoding="utf-8") as f:
            for line in f:
                ls = line.strip()
                if ls.startswith("- ") and not _is_placeholder(ls):
                    clean = strip_task_metadata(ls)
                    if clean:
                        someday_items.append(clean)

    # 5. Completed in the last 7 days
    completed_7d_count = 0
    for task in iter_task_lines(COMPLETED_TASKS_MD_FILE):
        if task.checked and task.completed_on and task.completed_on >= seven_days_ago:
            completed_7d_count += 1

    # 6. Upcoming ticklers — tasks deferred to today or the next 14 days.
    # The reflection hub has always been labelled "Deadlines & Ticklers", but only
    # project due_dates were ever counted, so a deferred task showed up nowhere.
    upcoming_ticklers = []
    for path in (TASKS_MD_FILE, WAITING_FOR_MD_FILE, SOMEDAY_MAYBE_MD_FILE):
        for task in iter_task_lines(path, include_checked=False):
            if not task.tickler:
                continue
            days_until = (task.tickler - today).days
            if 0 <= days_until <= 14:
                upcoming_ticklers.append(
                    (task.display, days_until, task.tickler.strftime("%d-%m-%Y"))
                )
    upcoming_ticklers.sort(key=lambda t: t[1])

    # 7. Projects, orphans (no active next action), deadlines and roadmaps
    all_projects = []
    orphaned_projects = []
    project_deadlines = []
    project_roadmaps = {}

    for project in iter_projects():
        if project.status in ("completed", "archived"):
            continue

        all_projects.append((project.name, project.category, project.path))

        tag_variants = {project.tag.lower(), project.name.lower()}
        if not (tag_variants & active_project_tags):
            orphaned_projects.append((project.name, project.category, project.path))

        due = project.due_date
        if due:
            days_left = (due - today).days
            if days_left <= 14:
                project_deadlines.append((project.name, days_left, due.strftime("%d-%m-%Y")))

        if project.steps:
            project_roadmaps[project.name] = project.steps

    return {
        "captured_items": captured_items,
        "next_items": next_items,
        "waiting_items": waiting_items,
        "stale_waitings": stale_waitings,
        "someday_items": someday_items,
        "completed_7d_count": completed_7d_count,
        "all_projects": all_projects,
        "orphaned_projects": orphaned_projects,
        "project_deadlines": project_deadlines,
        "upcoming_ticklers": upcoming_ticklers,
        "project_roadmaps": project_roadmaps
    }

def _build_reflect_hub(data: dict) -> tuple[str, dict]:
    """Renders the reflection hub screen. Shared by /reflect and the 'reflect:hub'
    button so the two can never drift apart."""
    num_cap = len(data["captured_items"])
    num_wait = len(data["waiting_items"])
    num_stale_wait = len(data["stale_waitings"])
    num_orphans = len(data["orphaned_projects"])
    num_someday = len(data["someday_items"])
    num_deadlines = len(data["project_deadlines"])
    num_ticklers = len(data["upcoming_ticklers"])

    stale_text = f" ({num_stale_wait} stale &gt;5d)" if num_stale_wait > 0 else ""

    text = (
        f"🧘 <b>GTD Reflection & Guided Review</b>\n\n"
        f"Keep your Second Brain clear, current, and complete.\n\n"
        f"1. 📥 <b>Get Clear:</b> {num_cap} captured items awaiting clarify\n"
        f"2. ⏳ <b>Audit Delegations:</b> {num_wait} waiting items{stale_text}\n"
        f"3. 🎯 <b>Project Health:</b> {len(data['all_projects'])} total, {num_orphans} orphaned (0 active actions)\n"
        f"4. 💡 <b>Someday / Maybe:</b> {num_someday} ideas on hold\n"
        f"5. 🚨 <b>Deadlines &amp; Ticklers:</b> {num_deadlines} project deadline(s), {num_ticklers} tickler(s) due\n\n"
        f"Tap <b>Start Guided Review</b> to walk through in sequence, or pick a specific check:"
    )

    reply_markup = {
        "inline_keyboard": [
            [{"text": "▶️ Start Guided Review", "callback_data": "reflect:step:1"}],
            [{"text": f"1. 📥 Get Clear ({num_cap})", "callback_data": "reflect:step:1"},
             {"text": f"2. ⏳ Waitings ({num_wait})", "callback_data": "reflect:step:2"}],
            [{"text": f"3. 🎯 Orphaned ({num_orphans})", "callback_data": "reflect:step:3"},
             {"text": f"4. 💡 Someday ({num_someday})", "callback_data": "reflect:step:4"}],
            [{"text": f"5. 🚨 Deadlines ({num_deadlines + num_ticklers})", "callback_data": "reflect:step:5"},
             {"text": "📊 Full Sitrep", "callback_data": "reflect:sitrep"}]
        ]
    }
    return text, reply_markup


async def _edit(http_client, token: str, chat_id, message_id, text: str, buttons: list = None):
    """Edit a Telegram message in place; the reflection flow does this on every step."""
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if buttons is not None:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    await http_client.post(f"https://api.telegram.org/bot{token}/editMessageText", json=payload)


async def handle_reflect_command(chat_id: int, token: str, http_client: httpx.AsyncClient):
    """Sends the interactive GTD Reflection & Guided Review Hub."""
    text, reply_markup = _build_reflect_hub(get_reflect_summary_data())
    await send_msg(chat_id, token, http_client, text, reply_markup=reply_markup)

async def handle_reflect_callback(callback_query: dict, token: str, http_client: httpx.AsyncClient):
    """Handles step navigation and 1-tap actions within the guided reflection process."""
    data_str = callback_query.get("data", "")
    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    
    if not chat_id:
        return
        
    data = get_reflect_summary_data()

    if data_str == "reflect:hub":
        text, reply_markup = _build_reflect_hub(data)
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text,
                  "parse_mode": "HTML", "reply_markup": reply_markup}
        )
        return

    # STEP 1: Get Clear (Inbox Zero)
    if data_str == "reflect:step:1":
        captured = data["captured_items"]
        if captured:
            items_preview = "\n".join(f"• <code>{i}</code>" for i in captured[:5])
            text = (
                f"📥 <b>Step 1 of 5: Get Clear (Inbox Zero)</b>\n\n"
                f"You have <b>{len(captured)}</b> unclarified capture(s) in <code>01_Tasks/Captured.md</code>:\n\n"
                f"{items_preview}\n\n"
                f"Emptying your inbox ensures every thought is triaged into an actionable next action or project roadmap."
            )
            buttons = [
                [{"text": "⚡ Launch /clarify", "callback_data": "reflect:launch_clarify"}],
                [{"text": "⏭️ Step 2: Delegations ➡️", "callback_data": "reflect:step:2"}],
                [{"text": "🏠 Reflection Hub", "callback_data": "reflect:hub"}]
            ]
        else:
            text = (
                "📥 <b>Step 1 of 5: Get Clear (Inbox Zero)</b>\n\n"
                "🎉 <b>Inbox Zero Achieved!</b>\n\n"
                "All captured notes and voice memos have been clarified."
            )
            buttons = [
                [{"text": "⏭️ Step 2: Delegations ➡️", "callback_data": "reflect:step:2"}],
                [{"text": "🏠 Reflection Hub", "callback_data": "reflect:hub"}]
            ]
            
        await _edit(http_client, token, chat_id, message_id, text, buttons)
        return

    # STEP 2: Audit Delegated Items (Waiting For)
    if data_str == "reflect:step:2":
        waitings = data["waiting_items"]
        stale_waitings = data["stale_waitings"]
        
        if waitings:
            wait_lines = []
            for w in waitings[:8]:
                clean_w = w.replace("- [ ]", "•").strip()
                if w in stale_waitings:
                    clean_w += " ⚠️ <b>STALE (&gt;5d)</b>"
                wait_lines.append(clean_w)
            wait_preview = "\n".join(wait_lines)
            text = (
                f"⏳ <b>Step 2 of 5: Audit Delegations (Waiting For)</b>\n\n"
                f"You have <b>{len(waitings)}</b> pending delegated item(s):\n\n"
                f"{wait_preview}\n\n"
                f"<i>Review each item: Has the person replied? Do you need to send a follow-up nudge? Run <code>/done &lt;item&gt;</code> if completed.</i>"
            )
        else:
            text = (
                "⏳ <b>Step 2 of 5: Audit Delegations (Waiting For)</b>\n\n"
                "✅ <b>No pending delegated items!</b>\n\n"
                "You are currently not waiting on anyone for any deliverables."
            )
            
        buttons = [
            [{"text": "⏮️ Step 1: Inbox", "callback_data": "reflect:step:1"},
             {"text": "⏭️ Step 3: Projects ➡️", "callback_data": "reflect:step:3"}],
            [{"text": "🏠 Reflection Hub", "callback_data": "reflect:hub"}]
        ]
        await _edit(http_client, token, chat_id, message_id, text, buttons)
        return

    # STEP 3: Project Health & Momentum (Orphaned Projects)
    if data_str == "reflect:step:3":
        orphans = data["orphaned_projects"]
        roadmaps = data["project_roadmaps"]
        
        buttons = []
        if orphans:
            orphan_lines = []
            for p_name, cat, p_path in orphans[:8]:
                steps = roadmaps.get(p_name, [])
                step_info = f" ({len(steps)} roadmap step(s) available)" if steps else " (0 roadmap steps)"
                orphan_lines.append(f"• <b>{p_name}</b> ({cat}){step_info}")
                if steps:
                    p_short = p_name[:20]
                    buttons.append([{"text": f"⚡ Promote Step: {p_short}", "callback_data": f"reflect:promote:{p_name}"}])
                    
            preview = "\n".join(orphan_lines)
            text = (
                f"🎯 <b>Step 3 of 5: Project Health &amp; Momentum</b>\n\n"
                f"⚠️ <b>{len(orphans)} project(s) have NO active Next Actions:</b>\n\n"
                f"{preview}\n\n"
                f"<i>Every active project requires at least one Next Action to maintain momentum. Tap a button below to promote the next step immediately:</i>"
            )
        else:
            text = (
                "🎯 <b>Step 3 of 5: Project Health &amp; Momentum</b>\n\n"
                "🎉 <b>All active projects have Next Actions!</b>\n\n"
                "Every single project in your system has active work underway."
            )
            
        buttons.append([
            {"text": "⏮️ Step 2: Waitings", "callback_data": "reflect:step:2"},
            {"text": "⏭️ Step 4: Someday ➡️", "callback_data": "reflect:step:4"}
        ])
        buttons.append([{"text": "🏠 Reflection Hub", "callback_data": "reflect:hub"}])
        
        await _edit(http_client, token, chat_id, message_id, text, buttons)
        return

    # STEP 4: Someday / Maybe Activation
    if data_str == "reflect:step:4":
        someday = data["someday_items"]
        if someday:
            preview = "\n".join(f"• {s}" for s in someday[:8])
            text = (
                f"💡 <b>Step 4 of 5: Someday / Maybe Review</b>\n\n"
                f"You have <b>{len(someday)}</b> idea(s) on hold in <code>01_Tasks/Someday_Maybe.md</code>:\n\n"
                f"{preview}\n\n"
                f"<i>Are any of these ideas ready to be activated into a project this week? Or should any be archived/deleted?</i>"
            )
        else:
            text = (
                "💡 <b>Step 4 of 5: Someday / Maybe Review</b>\n\n"
                "💡 <b>Someday/Maybe list is clear.</b>"
            )
            
        buttons = [
            [{"text": "⏮️ Step 3: Projects", "callback_data": "reflect:step:3"},
             {"text": "⏭️ Step 5: Deadlines ➡️", "callback_data": "reflect:step:5"}],
            [{"text": "🏠 Reflection Hub", "callback_data": "reflect:hub"}]
        ]
        await _edit(http_client, token, chat_id, message_id, text, buttons)
        return

    # STEP 5: Deadlines, Accomplishments & Wrap-up
    if data_str == "reflect:step:5":
        deadlines = data["project_deadlines"]
        comp_7d = data["completed_7d_count"]
        
        deadline_text = ""
        if deadlines:
            d_lines = []
            for p_name, days_left, d_disp in deadlines:
                if days_left < 0:
                    d_lines.append(f"• ⚠️ <b>{p_name}</b> — OVERDUE by {abs(days_left)}d ({d_disp})")
                elif days_left == 0:
                    d_lines.append(f"• 🚨 <b>{p_name}</b> — DUE TODAY! ({d_disp})")
                else:
                    d_lines.append(f"• 🚨 <b>{p_name}</b> — Due in {days_left}d ({d_disp})")
            deadline_text = "<b>Upcoming Project Deadlines (<=14d):</b>\n" + "\n".join(d_lines) + "\n\n"
        else:
            deadline_text = (
                "<b>Project Deadlines:</b> None within the next 14 days.\n"
                "<i>(Set <code>due_date</code> in a project note's frontmatter to track one.)</i>\n\n"
            )

        # Deferred tasks coming back into view. Previously the hub promised "Ticklers"
        # but nothing here ever listed them.
        ticklers = data["upcoming_ticklers"]
        if ticklers:
            t_lines = []
            for desc, days_until, t_disp in ticklers[:8]:
                short = desc if len(desc) <= 55 else desc[:52] + "..."
                if days_until == 0:
                    t_lines.append(f"• 🔔 <b>{short}</b> — surfaces TODAY ({t_disp})")
                elif days_until == 1:
                    t_lines.append(f"• 🔔 {short} — surfaces tomorrow ({t_disp})")
                else:
                    t_lines.append(f"• 🔔 {short} — in {days_until}d ({t_disp})")
            tickler_text = (
                "<b>Deferred Tasks Returning (&lt;=14d):</b>\n" + "\n".join(t_lines) + "\n\n"
            )
        else:
            tickler_text = "<b>Deferred Tasks:</b> None returning in the next 14 days.\n\n"

        text = (
            f"🏁 <b>Step 5 of 5: Deadlines &amp; Accomplishments</b>\n\n"
            f"{deadline_text}"
            f"{tickler_text}"
            f"🏆 <b>Weekly Accomplishments:</b> {comp_7d} action(s) completed in the last 7 days!\n\n"
            f"🎉 <b>Reflection Complete!</b>\n"
            f"Your Second Brain is 100% current, organized, and ready for high-impact execution."
        )
        
        buttons = [
            [{"text": "⚡ View Next Actions (/next)", "callback_data": "reflect:next_actions"}],
            [{"text": "⏮️ Step 4: Someday", "callback_data": "reflect:step:4"},
             {"text": "🏠 Return to Hub", "callback_data": "reflect:hub"}]
        ]
        await _edit(http_client, token, chat_id, message_id, text, buttons)
        return

    # Launch Clarify from Reflection
    if data_str == "reflect:launch_clarify":
        from backend.ingestion.handlers.clarify import handle_clarify_command
        await handle_clarify_command(chat_id, token, http_client)
        return

    # View Next Actions from Reflection
    if data_str == "reflect:next_actions":
        await handle_next_command(chat_id, "/next", token, http_client, show_category_picker=False)
        return

    # Step promotion from Reflection
    if data_str.startswith("reflect:promote:"):
        proj_name = data_str.split(":", 2)[2]
        await prompt_project_step_promotion(chat_id, proj_name, token, http_client)
        return

    # Sitrep
    if data_str == "reflect:sitrep":
        await handle_status_command(chat_id, token, http_client)
        return

async def handle_engage_commands(chat_id: int, text: str, token: str, http_client: httpx.AsyncClient) -> bool:
    cmd_part = text.split()[0].lower()
    print(f"Backend [Engage]: dispatching {cmd_part!r} (full: {text[:120]!r})")
    
    if cmd_part in ["/status", "/sitrep", "/weekly"]:
        await handle_status_command(chat_id, token, http_client)
        return True
    if cmd_part == "/daily":
        await handle_daily_command(chat_id, token, http_client)
        return True
    elif cmd_part in ["/next", "/actions"]:
        await handle_next_command(chat_id, text, token, http_client)
        return True
    elif cmd_part == "/waiting":
        await handle_waiting_command(chat_id, token, http_client)
        return True
    elif cmd_part == "/project":
        await handle_project_command(chat_id, text, token, http_client)
        return True
    elif cmd_part in ["/archive", "/archive-project"]:
        await handle_archive_project_command(chat_id, text, token, http_client)
        return True
    elif cmd_part == "/holiday":
        await handle_holiday_command(chat_id, text, token, http_client)
        return True
    elif cmd_part == "/done":
        await handle_done_command(chat_id, text, token, http_client)
        return True
    elif cmd_part == "/tickle":
        await handle_tickle_command(chat_id, text, token, http_client)
        return True
    elif cmd_part == "/reflect":
        await handle_reflect_command(chat_id, token, http_client)
        return True
        
    return False
