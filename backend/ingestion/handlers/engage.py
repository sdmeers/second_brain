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
        await send_msg(chat_id, token, http_client, f"❌ Task matching '{task_query}' not found.")
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

    new_task = task_line.replace("- [ ]", f"- [x] ~~", 1) + f"~~ (Completed on {get_today_str()})" if "Completed on" not in task_line else task_line.replace("- [ ]", "- [x]", 1)
    
    all_completed = [new_task] + existing_tasks
    out_content = header_str + "\n".join(all_completed) + "\n"
    atomic_write(completed_file, out_content)
        
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
    cmd_parts = text.split(maxsplit=2)
    if len(cmd_parts) < 3:
        await send_msg(
            chat_id, token, http_client,
            "⚠️ Usage: `/tickle <date/days> <task_search>`\n\n"
            "Examples:\n"
            "• `/tickle 7d Buy birthday card` (Defers for 7 days)\n"
            "• `/tickle 2026-09-01 Renew car insurance` (Defers until Sept 1st)\n"
            "• `/tickle tomorrow Call garage` (Defers until tomorrow)"
        )
        return

    date_arg = cmd_parts[1].strip()
    task_query = cmd_parts[2].strip().lower()
    
    from backend.ingestion.handlers.clarify import parse_natural_date_str
    target_date = parse_natural_date_str(date_arg)
    defer_str = target_date.strftime("%Y-%m-%d")

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
                    line = re.sub(r'🔔\s*\d{4}-\d{2}-\d{2}', f'🔔 {defer_str}', line)
                else:
                    line = line.strip() + f" 🔔 {defer_str}\n"
            new_lines.append(line)

        if found_file:
            from backend.vault.writer import atomic_write
            atomic_write(fp, "".join(new_lines))
            break

    if matched_line:
        clean_task = re.sub(r'\(Captured on.*?\)', '', matched_line).strip("- [ ]\n")
        await send_msg(chat_id, token, http_client, f"🔔 Deferred until {defer_str}:\n{clean_task}")
    else:
        await send_msg(chat_id, token, http_client, f"❌ Task matching '{task_query}' not found in active task lists.")

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

async def handle_engage_commands(chat_id: int, text: str, token: str, http_client: httpx.AsyncClient) -> bool:
    cmd_part = text.split()[0].lower()
    
    if cmd_part in ["/status", "/sitrep"]:
        await handle_status_command(chat_id, token, http_client)
        return True
    elif cmd_part == "/next":
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
