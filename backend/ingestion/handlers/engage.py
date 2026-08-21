import os
import re
import shutil
from datetime import datetime, timedelta
import httpx
from backend.config import OBSIDIAN_VAULT_PATH

def get_today_str():
    return datetime.now().strftime("%Y-%m-%d")

async def send_msg(chat_id: int, token: str, client: httpx.AsyncClient, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup: payload["reply_markup"] = reply_markup
    await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)

async def handle_next_command(chat_id: int, text: str, token: str, http_client: httpx.AsyncClient):
    cmd_parts = text.split(maxsplit=1)
    context_filter = cmd_parts[1].strip() if len(cmd_parts) > 1 else None
    
    tasks_file = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Next_Actions.md")
    if not os.path.exists(tasks_file):
        await send_msg(chat_id, token, http_client, "No Next_Actions.md found.")
        return
        
    with open(tasks_file, "r", encoding="utf-8") as f:
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
                
        filtered_tasks.append(line.strip())
        
    if not filtered_tasks:
        await send_msg(chat_id, token, http_client, "No active next actions found.")
        return
        
    msg = f"⚡ <b>Next Actions</b> {'(' + context_filter + ')' if context_filter else ''}\n\n"
    for t in filtered_tasks:
        clean_t = re.sub(r'\[\[.*?\]\]', '', t).replace('(Source: )', '').replace('- [ ]', '•').strip()
        msg += f"{clean_t}\n"
        
    await send_msg(chat_id, token, http_client, msg)

async def handle_waiting_command(chat_id: int, token: str, http_client: httpx.AsyncClient):
    file_path = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Waiting_For.md")
    if not os.path.exists(file_path):
        await send_msg(chat_id, token, http_client, "No Waiting_For.md found.")
        return
        
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    today = datetime.now()
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
        
    msg = f"⏳ <b>Waiting For</b>\n\n" + "\n".join(results)
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
        
    # Find project note
    found_path = None
    proj_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
    # 1. Exact match pass
    for root, dirs, files in os.walk(proj_dir):
        for f in files:
            if f.lower() == f"{proj_query.lower()}.md":
                found_path = os.path.join(root, f)
                break
        if found_path: break
        
    # 2. Substring match fallback
    if not found_path:
        for root, dirs, files in os.walk(proj_dir):
            for f in files:
                if proj_query.lower() in f.lower() and f.endswith(".md"):
                    found_path = os.path.join(root, f)
                    break
            if found_path: break
        
    if not found_path:
        # Check if project folder exists or auto-create project note in Personal
        proj_clean = proj_query.strip().title()
        personal_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects", "Personal")
        target_folder = os.path.join(personal_dir, proj_clean)
        
        if os.path.exists(target_folder) and os.path.isdir(target_folder):
            found_path = os.path.join(target_folder, f"{proj_clean}.md")
        else:
            found_path = os.path.join(personal_dir, f"{proj_clean}.md")
            
        if not os.path.exists(found_path):
            os.makedirs(os.path.dirname(found_path), exist_ok=True)
            today_str = datetime.now().strftime("%Y-%m-%d")
            proj_tag = proj_clean.strip().replace(" ", "_")
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
> View via Telegram: `/project {proj_clean}` or search `#project/{proj_tag}` in Obsidian.

```dataview
TASK
FROM "01_Tasks"
WHERE contains(tags, "#project/{proj_tag}") AND !completed
```

## Reference Material
> Notes and resources tagged with `#project/{proj_tag}` across the vault.
"""
            with open(found_path, "w", encoding="utf-8") as f:
                f.write(template)
            print(f"Auto-created project master note: {found_path}")
        
    if is_add:
        with open(found_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        if "## Project Steps" in content:
            content = content.replace("## Project Steps", f"## Project Steps\n- [ ] {step_to_add}")
        else:
            content += f"\n\n## Project Steps\n- [ ] {step_to_add}\n"
            
        with open(found_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        await send_msg(chat_id, token, http_client, f"✅ Added step to roadmap:\n`{step_to_add}`")
        return
        
    # Show project view
    with open(found_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    title = os.path.basename(found_path).replace(".md", "")
    outcome = "No outcome defined"
    m_out = re.search(r'outcome:\s*"(.*?)"', content)
    if m_out: outcome = m_out.group(1)
    
    steps = []
    m_steps = re.search(r'## Project Steps\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
    if m_steps:
        steps = [s.strip() for s in m_steps.group(1).strip().splitlines() if s.strip()]
        
    msg = f"📁 <b>{title}</b>\n🎯 Outcome: <i>{outcome}</i>\n\n"
    
    # 1. Active Actions search across task files
    active_actions = []
    proj_tag_space = f"#project/{title}".lower()
    proj_tag_underscore = f"#project/{title.replace(' ', '_')}".lower()
    
    for task_file in ["Next_Actions.md", "Waiting_For.md"]:
        tf_path = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", task_file)
        if os.path.exists(tf_path):
            with open(tf_path, "r", encoding="utf-8") as f:
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
        msg += "📌 <b>Roadmap Steps:</b>\n" + "\n".join(f"• {s.lstrip('- [ ]')}" for s in steps[:10])
        if len(steps) > 10: msg += f"\n...and {len(steps)-10} more."
    else:
        msg += "📌 <b>Roadmap Steps:</b> <i>No roadmap steps defined.</i>"
        
    await send_msg(chat_id, token, http_client, msg)

PROMOTION_SESSIONS = {}

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
        with open(fp, "r", encoding="utf-8") as f:
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
            has_active = any("- [ ]" in l for l in new_lines)
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
        
    from backend.config import COMPLETED_TASKS_MD_FILE
    from backend.vault.writer import atomic_write
    completed_file = COMPLETED_TASKS_MD_FILE
    
    header_str = "# ✅ Completed Tasks Archive\n\n> Archive of completed actions and quick 2-minute tasks. View in Telegram using `/done`.\n\n---\n\n## ✅ Completed Tasks\n"
    existing_tasks = []
    if os.path.exists(completed_file):
        with open(completed_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("- [x]") or line.strip().startswith("- [X]"):
                    existing_tasks.append(line.strip())

    clean_task_name = re.sub(r'\(Captured .*?\)', '', task_line)
    clean_task_name = re.sub(r'\(Source:.*?\)', '', clean_task_name).strip("- [ ]\n").strip()

    new_task = f"- [x] ~~{clean_task_name}~~ (Completed on {get_today_str()})"
    
    all_completed = [new_task] + existing_tasks
    out_content = header_str + "\n".join(all_completed) + "\n"
    atomic_write(completed_file, out_content)
        
    await send_msg(chat_id, token, http_client, f"✅ <b>Task Completed:</b>\n<s>{clean_task_name}</s>")
    
    # Project auto-promotion flow with interactive Telegram buttons
    if project_tag:
        await prompt_project_step_promotion(chat_id, project_tag, token, http_client)

async def prompt_project_step_promotion(chat_id: int, project_tag: str, token: str = None, http_client: httpx.AsyncClient = None):
    from backend.config import TELEGRAM_BOT_TOKEN
    tok = token or TELEGRAM_BOT_TOKEN
    if not tok or not chat_id:
        return
        
    proj_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
    proj_found_path = None
    proj_clean = project_tag.strip().replace("_", " ").title()
    
    # 1. Search for project master note
    for root, dirs, files in os.walk(proj_dir):
        for f in files:
            if f.lower() in [f"{proj_clean.lower()}.md", f"{project_tag.lower()}.md", f"{project_tag.replace(' ', '_').lower()}.md"]:
                proj_found_path = os.path.join(root, f)
                break
        if proj_found_path: break
        
    if not proj_found_path:
        for root, dirs, files in os.walk(proj_dir):
            for f in files:
                if project_tag.lower() in f.lower() and f.endswith(".md"):
                    proj_found_path = os.path.join(root, f)
                    break
            if proj_found_path: break

    if not proj_found_path or not os.path.exists(proj_found_path):
        return

    with open(proj_found_path, "r", encoding="utf-8") as pf:
        proj_content = pf.read()

    # Determine category
    category = "Personal"
    m_cat = re.search(r'category:\s*"?([a-zA-Z0-9_\-]+)"?', proj_content)
    if m_cat:
        category = m_cat.group(1)
    elif "Professional" in proj_found_path:
        category = "Professional"

    # Parse steps under ## Project Steps
    steps_match = re.search(r'## Project Steps\s*([\s\S]*?)(?=\n## |\Z)', proj_content)
    candidate_steps = []
    if steps_match:
        for line in steps_match.group(1).splitlines():
            if line.strip().startswith("- [ ]"):
                clean_step = re.sub(r'\(Captured .*?\)', '', line)
                clean_step = re.sub(r'\(Source:.*?\)', '', clean_step).strip("- [ ]\n").strip()
                if clean_step:
                    candidate_steps.append(clean_step)

    # Check currently active tasks in Next_Actions.md to avoid duplicate promotion
    next_actions_fp = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Next_Actions.md")
    active_next_texts = []
    if os.path.exists(next_actions_fp):
        with open(next_actions_fp, "r", encoding="utf-8") as nf:
            active_next_texts = [nl.strip().lower() for nl in nf if nl.strip().startswith("- [ ]")]

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
        
        clean_step = re.sub(r'\(Captured .*?\)', '', selected_step)
        clean_step = re.sub(r'\(Source:.*?\)', '', clean_step).strip("- [ ]\n").strip()
        
        # 1. Add to Next_Actions.md
        task_str = f"- [ ] {clean_step} {chosen_ctx} #project/{project_tag} #{category.lower()}"
        tasks_md = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Next_Actions.md")
        
        from backend.utils.tasks_dashboard import append_gtd_item
        append_gtd_item(tasks_md, task_str)

        # 2. Delete the promoted step from ## Project Steps in the project master note
        if not proj_path or not os.path.exists(proj_path):
            proj_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
            proj_clean = project_tag.strip().replace("_", " ").title()
            for root, dirs, files in os.walk(proj_dir):
                for f in files:
                    if f.lower() in [f"{proj_clean.lower()}.md", f"{project_tag.lower()}.md", f"{project_tag.replace(' ', '_').lower()}.md"]:
                        proj_path = os.path.join(root, f)
                        break
                if proj_path: break

        if proj_path and os.path.exists(proj_path):
            with open(proj_path, "r", encoding="utf-8") as pf:
                proj_lines = pf.readlines()

            new_proj_lines = []
            removed = False
            clean_target = clean_step.lower()

            for pl in proj_lines:
                if not removed and pl.strip().startswith("- [ ]"):
                    pl_clean = re.sub(r'\(Captured .*?\)', '', pl)
                    pl_clean = re.sub(r'\(Source:.*?\)', '', pl_clean).strip("- [ ]\n").strip().lower()
                    if clean_target == pl_clean or clean_target in pl_clean or pl_clean in clean_target:
                        removed = True
                        continue  # Skip to delete from project roadmap
                new_proj_lines.append(pl)

            if removed:
                from backend.vault.writer import atomic_write
                atomic_write(proj_path, "".join(new_proj_lines))
        
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
    
    today = datetime.now().date()
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
            with open(fp, "r", encoding="utf-8") as f:
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
            clean_task = re.sub(r'\(Captured on.*?\)', '', matched_line).strip("- [ ]\n")
            clean_task = re.sub(r'🔔\s*\d{4}-\d{2}-\d{2}', '', clean_task).strip()
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
            with open(fp, "r", encoding="utf-8") as f:
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
        with open(comp_fp, "r", encoding="utf-8") as f:
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
        with open(note_to_update, "r", encoding="utf-8") as f:
            content = f.read()
        
        if "status:" in content:
            content = re.sub(r'status:\s*"?.*?"?', 'status: "completed"', content)
        else:
            content = content.replace("---", f"---\nstatus: \"completed\"\ndate_completed: \"{today_str}\"", 1)

        if "date_completed:" not in content:
            content = re.sub(r'(status:\s*"completed")', r'\1\ndate_completed: "' + today_str + '"', content)

        with open(note_to_update, "w", encoding="utf-8") as f:
            f.write(content)

    # Move filesystem entry to 05_Archive/<Category>/
    archive_dir = os.path.join(OBSIDIAN_VAULT_PATH, "05_Archive", category)
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
    from backend.utils.holiday_manager import set_holiday_pause, clear_holiday_pause, get_holiday_state, is_scope_paused
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
    """Gathers structured review data across the GTD task lists and projects."""
    today_dt = datetime.now()
    today_str = today_dt.strftime("%Y-%m-%d")
    seven_days_ago = today_dt - timedelta(days=7)
    
    # 1. Captured items
    captured_items = []
    cap_path = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Captured.md")
    if os.path.exists(cap_path):
        with open(cap_path, "r", encoding="utf-8") as f:
            for line in f:
                ls = line.strip()
                if ls.startswith("- "):
                    clean = re.sub(r'\(Source:.*?\)', '', ls)
                    clean = re.sub(r'\(Captured on.*?\)', '', clean).replace("- ", "").strip()
                    if clean:
                        captured_items.append(clean)
                        
    # 2. Next actions
    next_items = []
    active_project_tags = set()
    next_path = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Next_Actions.md")
    if os.path.exists(next_path):
        with open(next_path, "r", encoding="utf-8") as f:
            for line in f:
                ls = line.strip()
                if ls.startswith("- [ ]"):
                    m_tick = re.search(r'🔔 (\d{4}-\d{2}-\d{2})', ls)
                    if m_tick and m_tick.group(1) > today_str:
                        continue
                    m_proj = re.search(r'#project/([a-zA-Z0-9_\-]+)', ls)
                    if m_proj:
                        active_project_tags.add(m_proj.group(1).lower())
                    next_items.append(ls)

    # 3. Waiting items
    waiting_items = []
    stale_waitings = []
    wait_path = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Waiting_For.md")
    if os.path.exists(wait_path):
        with open(wait_path, "r", encoding="utf-8") as f:
            for line in f:
                ls = line.strip()
                if ls.startswith("- [ ]"):
                    waiting_items.append(ls)
                    m_cap = re.search(r'\(Captured .*? on (\d{4}-\d{2}-\d{2})\)', ls)
                    if m_cap:
                        try:
                            cap_date = datetime.strptime(m_cap.group(1), "%Y-%m-%d")
                            if (today_dt - cap_date).days > 5:
                                stale_waitings.append(ls)
                        except Exception:
                            pass

    # 4. Someday items
    someday_items = []
    some_path = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Someday_Maybe.md")
    if os.path.exists(some_path):
        with open(some_path, "r", encoding="utf-8") as f:
            for line in f:
                ls = line.strip()
                if ls.startswith("- [ ]") or ls.startswith("- "):
                    clean = ls.replace("- [ ]", "").replace("- ", "").strip()
                    if clean:
                        someday_items.append(clean)

    # 5. Completed last 7 days
    completed_7d_count = 0
    comp_path = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Completed.md")
    if os.path.exists(comp_path):
        with open(comp_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("- [x]"):
                    m_comp = re.search(r'\(Completed on (\d{4}-\d{2}-\d{2})\)', line)
                    if m_comp:
                        try:
                            c_date = datetime.strptime(m_comp.group(1), "%Y-%m-%d")
                            if c_date >= seven_days_ago:
                                completed_7d_count += 1
                        except Exception:
                            pass

    # 6. Projects & Orphaned Projects
    proj_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
    all_projects = []
    orphaned_projects = []
    project_deadlines = []
    project_roadmaps = {}

    for category in ["Personal", "Professional"]:
        cat_dir = os.path.join(proj_dir, category)
        if not os.path.exists(cat_dir):
            continue
        for entry in sorted(os.listdir(cat_dir)):
            if entry.startswith(".") or entry.startswith("_") or entry in ["archive", "docs", "Notes", "Meetings", "Outputs", "Sessions", "old", "Syncthing"]:
                continue
            entry_path = os.path.join(cat_dir, entry)
            if os.path.isdir(entry_path):
                p_name = entry
                p_file = os.path.join(entry_path, f"{entry}.md")
            elif entry.endswith(".md"):
                p_name = entry[:-3]
                p_file = entry_path
            else:
                continue

            if not os.path.exists(p_file):
                continue

            all_projects.append((p_name, category, p_file))
            p_tag_clean = p_name.strip().replace(" ", "_").lower()
            if p_tag_clean not in active_project_tags and p_name.lower() not in active_project_tags:
                orphaned_projects.append((p_name, category, p_file))

            try:
                with open(p_file, "r", encoding="utf-8") as pf:
                    p_text = pf.read()
                    
                # Parse frontmatter
                fm_match = re.search(r'^---\s*\n(.*?)\n---', p_text, re.DOTALL)
                if fm_match:
                    frontmatter = fm_match.group(1)
                    m_status = re.search(r'^status:\s*"?([a-zA-Z0-9_\-]+)"?', frontmatter, re.MULTILINE)
                    if m_status and m_status.group(1).lower() in ["completed", "archived"]:
                        continue
                    m_due = re.search(r'^due_date:\s*"?(\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4})"?', frontmatter, re.MULTILINE)
                    if m_due:
                        due_raw = m_due.group(1)
                        if "-" in due_raw and len(due_raw.split("-")[0]) == 4:
                            due_dt = datetime.strptime(due_raw, "%Y-%m-%d").date()
                        else:
                            due_dt = datetime.strptime(due_raw, "%d-%m-%Y").date()
                        days_left = (due_dt - today_dt.date()).days
                        date_disp = due_dt.strftime("%d-%m-%Y")
                        if days_left <= 14:
                            project_deadlines.append((p_name, days_left, date_disp))
                            
                # Parse roadmap steps
                m_steps = re.search(r'## Project Steps\n(.*?)(?=\n## |\Z)', p_text, re.DOTALL)
                if m_steps:
                    steps = [s.replace("- [ ]", "").strip() for s in m_steps.group(1).strip().splitlines() if s.strip() and s.strip().startswith("- [ ]")]
                    if steps:
                        project_roadmaps[p_name] = steps
            except Exception:
                pass

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
        "project_roadmaps": project_roadmaps
    }

async def handle_reflect_command(chat_id: int, token: str, http_client: httpx.AsyncClient):
    """Sends the interactive GTD Reflection & Guided Review Hub."""
    data = get_reflect_summary_data()
    
    num_cap = len(data["captured_items"])
    num_wait = len(data["waiting_items"])
    num_stale_wait = len(data["stale_waitings"])
    num_orphans = len(data["orphaned_projects"])
    num_someday = len(data["someday_items"])
    num_deadlines = len(data["project_deadlines"])
    
    stale_text = f" ({num_stale_wait} stale &gt;5d)" if num_stale_wait > 0 else ""
    
    text = (
        f"🧘 <b>GTD Reflection & Guided Review</b>\n\n"
        f"Keep your Second Brain clear, current, and complete.\n\n"
        f"1. 📥 <b>Get Clear:</b> {num_cap} captured items awaiting clarify\n"
        f"2. ⏳ <b>Audit Delegations:</b> {num_wait} waiting items{stale_text}\n"
        f"3. 🎯 <b>Project Health:</b> {len(data['all_projects'])} total, {num_orphans} orphaned (0 active actions)\n"
        f"4. 💡 <b>Someday / Maybe:</b> {num_someday} ideas on hold\n"
        f"5. 🚨 <b>Deadlines &amp; Ticklers:</b> {num_deadlines} upcoming deadline(s)\n\n"
        f"Tap <b>Start Guided Review</b> to walk through in sequence, or pick a specific check:"
    )
    
    reply_markup = {
        "inline_keyboard": [
            [{"text": "▶️ Start Guided Review", "callback_data": "reflect:step:1"}],
            [{"text": f"1. 📥 Get Clear ({num_cap})", "callback_data": "reflect:step:1"},
             {"text": f"2. ⏳ Waitings ({num_wait})", "callback_data": "reflect:step:2"}],
            [{"text": f"3. 🎯 Orphaned ({num_orphans})", "callback_data": "reflect:step:3"},
             {"text": f"4. 💡 Someday ({num_someday})", "callback_data": "reflect:step:4"}],
            [{"text": f"5. 🚨 Deadlines ({num_deadlines})", "callback_data": "reflect:step:5"},
             {"text": "📊 Full Sitrep", "callback_data": "reflect:sitrep"}]
        ]
    }
    
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
        num_cap = len(data["captured_items"])
        num_wait = len(data["waiting_items"])
        num_stale_wait = len(data["stale_waitings"])
        num_orphans = len(data["orphaned_projects"])
        num_someday = len(data["someday_items"])
        num_deadlines = len(data["project_deadlines"])
        
        stale_text = f" ({num_stale_wait} stale &gt;5d)" if num_stale_wait > 0 else ""
        
        text = (
            f"🧘 <b>GTD Reflection & Guided Review</b>\n\n"
            f"Keep your Second Brain clear, current, and complete.\n\n"
            f"1. 📥 <b>Get Clear:</b> {num_cap} captured items awaiting clarify\n"
            f"2. ⏳ <b>Audit Delegations:</b> {num_wait} waiting items{stale_text}\n"
            f"3. 🎯 <b>Project Health:</b> {len(data['all_projects'])} total, {num_orphans} orphaned (0 active actions)\n"
            f"4. 💡 <b>Someday / Maybe:</b> {num_someday} ideas on hold\n"
            f"5. 🚨 <b>Deadlines &amp; Ticklers:</b> {num_deadlines} upcoming deadline(s)\n\n"
            f"Tap <b>Start Guided Review</b> to walk through in sequence, or pick a specific check:"
        )
        reply_markup = {
            "inline_keyboard": [
                [{"text": "▶️ Start Guided Review", "callback_data": "reflect:step:1"}],
                [{"text": f"1. 📥 Get Clear ({num_cap})", "callback_data": "reflect:step:1"},
                 {"text": f"2. ⏳ Waitings ({num_wait})", "callback_data": "reflect:step:2"}],
                [{"text": f"3. 🎯 Orphaned ({num_orphans})", "callback_data": "reflect:step:3"},
                 {"text": f"4. 💡 Someday ({num_someday})", "callback_data": "reflect:step:4"}],
                [{"text": f"5. 🚨 Deadlines ({num_deadlines})", "callback_data": "reflect:step:5"},
                 {"text": "📊 Full Sitrep", "callback_data": "reflect:sitrep"}]
            ]
        }
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML", "reply_markup": reply_markup}
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
                f"📥 <b>Step 1 of 5: Get Clear (Inbox Zero)</b>\n\n"
                f"🎉 <b>Inbox Zero Achieved!</b>\n\n"
                f"All captured notes and voice memos have been clarified."
            )
            buttons = [
                [{"text": "⏭️ Step 2: Delegations ➡️", "callback_data": "reflect:step:2"}],
                [{"text": "🏠 Reflection Hub", "callback_data": "reflect:hub"}]
            ]
            
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML", "reply_markup": {"inline_keyboard": buttons}}
        )
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
                f"⏳ <b>Step 2 of 5: Audit Delegations (Waiting For)</b>\n\n"
                f"✅ <b>No pending delegated items!</b>\n\n"
                f"You are currently not waiting on anyone for any deliverables."
            )
            
        buttons = [
            [{"text": "⏮️ Step 1: Inbox", "callback_data": "reflect:step:1"},
             {"text": "⏭️ Step 3: Projects ➡️", "callback_data": "reflect:step:3"}],
            [{"text": "🏠 Reflection Hub", "callback_data": "reflect:hub"}]
        ]
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML", "reply_markup": {"inline_keyboard": buttons}}
        )
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
                f"🎯 <b>Step 3 of 5: Project Health &amp; Momentum</b>\n\n"
                f"🎉 <b>All active projects have Next Actions!</b>\n\n"
                f"Every single project in your system has active work underway."
            )
            
        buttons.append([
            {"text": "⏮️ Step 2: Waitings", "callback_data": "reflect:step:2"},
            {"text": "⏭️ Step 4: Someday ➡️", "callback_data": "reflect:step:4"}
        ])
        buttons.append([{"text": "🏠 Reflection Hub", "callback_data": "reflect:hub"}])
        
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML", "reply_markup": {"inline_keyboard": buttons}}
        )
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
                f"💡 <b>Step 4 of 5: Someday / Maybe Review</b>\n\n"
                f"💡 <b>Someday/Maybe list is clear.</b>"
            )
            
        buttons = [
            [{"text": "⏮️ Step 3: Projects", "callback_data": "reflect:step:3"},
             {"text": "⏭️ Step 5: Deadlines ➡️", "callback_data": "reflect:step:5"}],
            [{"text": "🏠 Reflection Hub", "callback_data": "reflect:hub"}]
        ]
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML", "reply_markup": {"inline_keyboard": buttons}}
        )
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
            deadline_text = "<b>Project Deadlines:</b> None within the next 14 days.\n\n"
            
        text = (
            f"🏁 <b>Step 5 of 5: Deadlines &amp; Accomplishments</b>\n\n"
            f"{deadline_text}"
            f"🏆 <b>Weekly Accomplishments:</b> {comp_7d} action(s) completed in the last 7 days!\n\n"
            f"🎉 <b>Reflection Complete!</b>\n"
            f"Your Second Brain is 100% current, organized, and ready for high-impact execution."
        )
        
        buttons = [
            [{"text": "⚡ View Next Actions (/next)", "callback_data": "reflect:next_actions"}],
            [{"text": "⏮️ Step 4: Someday", "callback_data": "reflect:step:4"},
             {"text": "🏠 Return to Hub", "callback_data": "reflect:hub"}]
        ]
        await http_client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML", "reply_markup": {"inline_keyboard": buttons}}
        )
        return

    # Launch Clarify from Reflection
    if data_str == "reflect:launch_clarify":
        from backend.ingestion.handlers.clarify import handle_clarify_command
        await handle_clarify_command(chat_id, token, http_client)
        return

    # View Next Actions from Reflection
    if data_str == "reflect:next_actions":
        await handle_next_command(chat_id, "/next", token, http_client)
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
    
    if cmd_part in ["/status", "/sitrep"]:
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
