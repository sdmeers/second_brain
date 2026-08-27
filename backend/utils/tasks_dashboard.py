import os
import re
from datetime import datetime, timedelta
from backend.config import TASKS_MD_FILE
from backend.vault.writer import atomic_write
from backend.vault.tasks import archive_completed_lines

def parse_deadline(line: str) -> datetime:
    # 1. Look for explicit YYYY-MM-DD after calendar emoji
    match_date = re.search(r'📅\s*(\d{4}-\d{2}-\d{2})', line)
    if match_date:
        try:
            return datetime.strptime(match_date.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
            
    # 2. Look for Due: YYYY-MM-DD
    match_due = re.search(r'Due:\s*(\d{4}-\d{2}-\d{2})', line, re.IGNORECASE)
    if match_due:
        try:
            return datetime.strptime(match_due.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass

    # 3. Look for capture date in parentheses to resolve relative dates
    capture_date = None
    match_captured = re.search(r'Captured from .* on (\d{4}-\d{2}-\d{2})', line)
    if match_captured:
        try:
            capture_date = datetime.strptime(match_captured.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass

    # 4. Check for tomorrow
    if "📅 tomorrow" in line.lower() or "📅tomorrow" in line.lower():
        base_date = capture_date or datetime.now().date()
        return base_date + timedelta(days=1)

    # 5. Check for today
    if "📅 today" in line.lower() or "📅today" in line.lower():
        return capture_date or datetime.now().date()

    return None

def get_project_category(project_name: str) -> str:
    """Determines if project lives under Personal or Professional directory in 02_Projects."""
    from backend.config import OBSIDIAN_VAULT_PATH
    if not project_name:
        return "Personal"
    proj_clean = project_name.strip().lower().replace(" ", "_")
    proj_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
    
    prof_dir = os.path.join(proj_dir, "Professional")
    if os.path.exists(prof_dir):
        for root, dirs, files in os.walk(prof_dir):
            r_name = os.path.basename(root).lower().replace(" ", "_")
            if r_name == proj_clean:
                return "Professional"
            for f in files:
                f_name = f.replace(".md", "").lower().replace(" ", "_")
                if f_name == proj_clean:
                    return "Professional"

    pers_dir = os.path.join(proj_dir, "Personal")
    if os.path.exists(pers_dir):
        for root, dirs, files in os.walk(pers_dir):
            r_name = os.path.basename(root).lower().replace(" ", "_")
            if r_name == proj_clean:
                return "Personal"
            for f in files:
                f_name = f.replace(".md", "").lower().replace(" ", "_")
                if f_name == proj_clean:
                    return "Personal"

    return "Personal"

def ensure_category_tag(task_line: str, category: str = None, project_name: str = None) -> str:
    """Ensures task_line has either #personal or #professional tag."""
    line = task_line.strip()
    if "#personal" in line or "#professional" in line:
        return line

    if not project_name:
        proj_match = re.search(r'#project/([A-Za-z0-9_\-]+)', line)
        if proj_match:
            project_name = proj_match.group(1)

    if not category and project_name:
        category = get_project_category(project_name)

    cat_tag = "#professional" if (category and category.lower() == "professional") else "#personal"

    if " (Captured" in line:
        parts = line.split(" (Captured", 1)
        return f"{parts[0].strip()} {cat_tag} (Captured{parts[1]}"
    else:
        return f"{line} {cat_tag}"

def append_gtd_item(filepath: str, item_line: str):
    """Appends an item to a GTD task file, automatically stripping any clear messages."""
    from backend.vault.writer import atomic_write
    fname = os.path.basename(filepath)
    if fname == "Captured.md":
        header = "# 📥 Captured\n\n> Raw thoughts and voice captures awaiting clarification via `/clarify`.\n\n---\n\n## 📥 Captured Items\n\n"
    elif fname == "Next_Actions.md":
        header = "# ⚡ Next Actions\n\n> Flexible, context-tagged action list. Filter in Telegram using `/next @context`.\n\n---\n\n## ⚡ Next Actions\n\n"
    elif fname == "Waiting_For.md":
        header = "# ⏳ Waiting For\n\n> Delegated actions and items owed to you by others. View in Telegram using `/waiting`.\n\n---\n\n## ⏳ Waiting For\n\n"
    elif fname == "Someday_Maybe.md":
        header = "# 💡 Someday / Maybe\n\n> Future ideas, uncommitted desires, and projects to review during `/reflect`.\n\n---\n\n## 💡 Someday / Maybe\n\n"
    else:
        header = None

    existing_items = []
    if os.path.exists(filepath):
        with open(filepath, encoding="utf-8") as f:
            for raw_line in f:
                s = raw_line.strip()
                if "🎉" in s or "Inbox Zero" in s or "All clear" in s or "List clear" in s or "*None*" in s:
                    continue
                if fname == "Captured.md":
                    if s.startswith("- "):
                        existing_items.append(s)
                else:
                    if s.startswith("- ["):
                        existing_items.append(s)

    clean_item = item_line.strip()
    if fname in ["Next_Actions.md", "Waiting_For.md"]:
        clean_item = ensure_category_tag(clean_item)

    existing_items.append(clean_item)
    content = (header or "") + "\n".join(existing_items) + "\n"
    atomic_write(filepath, content)

async def maintain_tasks_dashboard():
    """Scans all task files (Next_Actions.md, Waiting_For.md, Someday_Maybe.md, Captured.md),
    moves completed tasks to z_Completed.md (in reverse chronological order),
    writes List Clear messages for empty lists, and categorizes Next_Actions.md."""
    from backend.config import WAITING_FOR_MD_FILE, SOMEDAY_MAYBE_MD_FILE, CAPTURED_MD_FILE
    
    try:
        newly_completed = []
        active_next_actions = []

        all_task_files = [
            ("Next_Actions.md", TASKS_MD_FILE),
            ("Waiting_For.md", WAITING_FOR_MD_FILE),
            ("Someday_Maybe.md", SOMEDAY_MAYBE_MD_FILE),
            ("Captured.md", CAPTURED_MD_FILE)
        ]

        for fname, fpath in all_task_files:
            if not os.path.exists(fpath):
                continue
                
            with open(fpath, encoding="utf-8") as f:
                lines = f.readlines()
                
            file_newly_completed = []
            file_active = []
            
            for line in lines:
                stripped = line.strip()
                if "🎉" in stripped or "Inbox Zero" in stripped or "All clear" in stripped or "List clear" in stripped or "*None*" in stripped:
                    continue
                if fname == "Captured.md":
                    if stripped.startswith("- "):
                        file_active.append(stripped)
                else:
                    if stripped.startswith("- [") and len(stripped) >= 6:
                        checkbox = stripped[3:4]
                        if checkbox.lower() in ["x", "v"]:
                            file_newly_completed.append(stripped)
                        else:
                            tagged_item = ensure_category_tag(stripped) if fname in ["Next_Actions.md", "Waiting_For.md"] else stripped
                            file_active.append(tagged_item)

            if fname == "Next_Actions.md":
                active_next_actions = file_active

            if file_newly_completed:
                newly_completed.extend(file_newly_completed)
                
            # Maintain non-Next_Actions files immediately
            if fname != "Next_Actions.md":
                if fname == "Waiting_For.md":
                    header = "# ⏳ Waiting For\n\n> Delegated actions and items owed to you by others. View in Telegram using `/waiting`.\n\n---\n\n## ⏳ Waiting For\n\n"
                    clear_msg = "🎉 *All clear! No pending items waiting on others.*\n"
                elif fname == "Someday_Maybe.md":
                    header = "# 💡 Someday / Maybe\n\n> Future ideas, uncommitted desires, and projects to review during `/reflect`.\n\n---\n\n## 💡 Someday / Maybe\n\n"
                    clear_msg = "🎉 *List clear! No someday/maybe items recorded.*\n"
                elif fname == "Captured.md":
                    header = "# 📥 Captured\n\n> Raw thoughts and voice captures awaiting clarification via `/clarify`.\n\n---\n\n## 📥 Captured Items\n\n"
                    clear_msg = "🎉 *Inbox Zero! No captured items awaiting clarification.*\n"
                
                content = header + ("\n".join(file_active) + "\n" if file_active else clear_msg)
                with open(fpath, encoding="utf-8") as rf:
                    curr_raw = rf.read()
                if curr_raw.strip() != content.strip():
                    atomic_write(fpath, content)

        # Move all newly completed tasks to z_Completed.md (reverse chronological)
        if newly_completed:
            archive_completed_lines(newly_completed)
            print(f"Tasks Dashboard: Moved {len(newly_completed)} completed tasks to z_Completed.md")
            
            # Check newly completed tasks for #project/<tag> and prompt Telegram promotion
            from backend.config import TELEGRAM_ALLOWED_CHAT_IDS, TELEGRAM_BOT_TOKEN
            if TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_IDS:
                primary_chat_id = TELEGRAM_ALLOWED_CHAT_IDS[0] if isinstance(TELEGRAM_ALLOWED_CHAT_IDS, list) else int(str(TELEGRAM_ALLOWED_CHAT_IDS).split(",")[0].strip())
                for task_str in newly_completed:
                    m_proj = re.search(r'#project/([a-zA-Z0-9_\-]+)', task_str)
                    if m_proj:
                        proj_tag = m_proj.group(1)
                        try:
                            from backend.ingestion.handlers.engage import prompt_project_step_promotion
                            await prompt_project_step_promotion(primary_chat_id, proj_tag)
                        except Exception as pe:
                            print(f"Tasks Dashboard: Error prompting project promotion for {proj_tag}: {pe}")
                        break

        # 2. Categorize active tasks by due date, split into Personal / Professional
        today = datetime.now().date()

        def _order_by_due(tasks):
            overdue_tasks, due_today_tasks, upcoming_tasks, backlog_tasks = [], [], [], []
            for task in tasks:
                due_date = parse_deadline(task)

                # Replace relative date keywords with standard YYYY-MM-DD for compatibility
                if due_date:
                    due_date_str = due_date.strftime("%Y-%m-%d")
                    task = re.sub(r'📅\s*tomorrow', f'📅 {due_date_str}', task, flags=re.IGNORECASE)
                    task = re.sub(r'📅\s*today', f'📅 {due_date_str}', task, flags=re.IGNORECASE)

                if due_date is None:
                    backlog_tasks.append(task)
                else:
                    if due_date < today:
                        overdue_tasks.append(task)
                    elif due_date == today:
                        due_today_tasks.append(task)
                    elif today < due_date <= today + timedelta(days=7):
                        upcoming_tasks.append(task)
                    else:
                        backlog_tasks.append(task)
            return overdue_tasks + due_today_tasks + upcoming_tasks + backlog_tasks

        personal_raw = [t for t in active_next_actions if "#professional" not in t]
        professional_raw = [t for t in active_next_actions if "#professional" in t]

        personal_tasks = _order_by_due(personal_raw)
        professional_tasks = _order_by_due(professional_raw)

        # 3. Rebuild Next_Actions.md content, split into Personal / Professional sections
        new_content = """# ⚡ Next Actions

> Flexible, context-tagged action list. Filter in Telegram using `/next @context`.

---

## 🏠 Personal
"""
        if personal_tasks:
            for task in personal_tasks:
                new_content += f"{task}\n"
        else:
            new_content += "*None*\n"

        new_content += "\n## 💼 Professional\n"
        if professional_tasks:
            for task in professional_tasks:
                new_content += f"{task}\n"
        else:
            new_content += "*None*\n"

        if not personal_tasks and not professional_tasks:
            new_content = """# ⚡ Next Actions

> Flexible, context-tagged action list. Filter in Telegram using `/next @context`.

---

## ⚡ Next Actions

🎉 *All clear! No active next actions remaining.*
"""
            
        # Write back to file if changed
        if os.path.exists(TASKS_MD_FILE):
            with open(TASKS_MD_FILE, encoding="utf-8") as f:
                current_raw = f.read()
                
            if current_raw.strip() != new_content.strip():
                atomic_write(TASKS_MD_FILE, new_content)
                print("Tasks Dashboard: Regenerated and categorized Tasks.md successfully.")
            
    except Exception as e:
        print(f"Tasks Dashboard: Error maintaining task list: {e}")
