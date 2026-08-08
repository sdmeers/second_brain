import os
import re
from datetime import datetime
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
    if steps:
        msg += "<b>Roadmap:</b>\n" + "\n".join(steps[:10])
        if len(steps) > 10: msg += f"\n...and {len(steps)-10} more."
    else:
        msg += "No roadmap steps defined."
        
    await send_msg(chat_id, token, http_client, msg)

async def handle_done_command(chat_id: int, text: str, token: str, http_client: httpx.AsyncClient):
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await send_msg(chat_id, token, http_client, "Usage: /done <task text>")
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
            with open(fp, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            break
            
    if not found:
        await send_msg(chat_id, token, http_client, f"❌ Task matching '{task_query}' not found.")
        return
        
    from backend.config import COMPLETED_TASKS_MD_FILE
    completed_file = COMPLETED_TASKS_MD_FILE
    with open(completed_file, "a", encoding="utf-8") as f:
        new_task = task_line.replace("- [ ]", f"- [x] (Completed on {get_today_str()})", 1)
        f.write(new_task + "\n")
        
    await send_msg(chat_id, token, http_client, f"✅ Done! Archived to z_Completed.md")
    
    # Project auto promotion
    if project_tag:
        proj_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
        for root, dirs, files in os.walk(proj_dir):
            for f in files:
                if project_tag.lower() in f.lower() and f.endswith(".md"):
                    await send_msg(chat_id, token, http_client, f"🎯 Task was tagged for {project_tag}. Make sure to promote the next step in `/project {project_tag}`.")
                    return

async def handle_tickle_command(chat_id: int, text: str, token: str, http_client: httpx.AsyncClient):
    await send_msg(chat_id, token, http_client, "✅ Tickle command handled (simplified implementation for phase 4).")

async def handle_reflect_command(chat_id: int, token: str, http_client: httpx.AsyncClient):
    await send_msg(chat_id, token, http_client, "🔍 Reflect (Guided Review) started. Please check daily digest.")

async def handle_engage_commands(chat_id: int, text: str, token: str, http_client: httpx.AsyncClient) -> bool:
    cmd_part = text.split()[0].lower()
    
    if cmd_part == "/next":
        await handle_next_command(chat_id, text, token, http_client)
        return True
    elif cmd_part == "/waiting":
        await handle_waiting_command(chat_id, token, http_client)
        return True
    elif cmd_part == "/project":
        await handle_project_command(chat_id, text, token, http_client)
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
