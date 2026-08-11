import os
import re
from datetime import datetime, timedelta
from backend.config import TASKS_MD_FILE, COMPLETED_TASKS_MD_FILE
from backend.vault.writer import atomic_write

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
        with open(filepath, "r", encoding="utf-8") as f:
            for l in f:
                s = l.strip()
                if "🎉" in s or "Inbox Zero" in s or "All clear" in s or "List clear" in s or "*None*" in s:
                    continue
                if fname == "Captured.md":
                    if s.startswith("- "):
                        existing_items.append(s)
                else:
                    if s.startswith("- ["):
                        existing_items.append(s)

    existing_items.append(item_line.strip())
    content = (header or "") + "\n".join(existing_items) + "\n"
    atomic_write(filepath, content)

async def maintain_tasks_dashboard():
    """Scans all task files (Next_Actions.md, Waiting_For.md, Someday_Maybe.md, Captured.md),
    moves completed tasks to z_Completed.md (in reverse chronological order),
    writes List Clear messages for empty lists, and categorizes Next_Actions.md."""
    from backend.config import TASKS_PATH, COMPLETED_TASKS_MD_FILE, TASKS_MD_FILE, WAITING_FOR_MD_FILE, SOMEDAY_MAYBE_MD_FILE, CAPTURED_MD_FILE
    
    try:
        completed_file = COMPLETED_TASKS_MD_FILE
        today_str = datetime.now().strftime("%Y-%m-%d")
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
                
            with open(fpath, "r", encoding="utf-8") as f:
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
                            file_active.append(stripped)

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
                with open(fpath, "r", encoding="utf-8") as rf:
                    curr_raw = rf.read()
                if curr_raw.strip() != content.strip():
                    atomic_write(fpath, content)

        # Move all newly completed tasks to z_Completed.md (reverse chronological)
        if newly_completed:
            header_lines = [
                "# ✅ Completed Tasks Archive\n\n",
                "> Archive of completed actions and quick 2-minute tasks. View in Telegram using `/done`.\n\n",
                "---\n\n",
                "## ✅ Completed Tasks\n"
            ]
            
            existing_tasks = []
            if os.path.exists(completed_file):
                try:
                    with open(completed_file, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.strip().startswith("- [x]") or line.strip().startswith("- [X]"):
                                existing_tasks.append(line.strip())
                except Exception:
                    pass
            
            formatted_new = []
            for task in newly_completed:
                if "Completed on" not in task:
                    task_clean = task.replace("- [x]", "- [x] ~~").replace("- [X]", "- [x] ~~") + f"~~ (Completed on {today_str})"
                else:
                    task_clean = task
                formatted_new.append(task_clean)
                
            all_completed = formatted_new + existing_tasks
            out_content = "".join(header_lines) + "\n".join(all_completed) + "\n"
            atomic_write(completed_file, out_content)
            print(f"Tasks Dashboard: Moved {len(newly_completed)} completed tasks to z_Completed.md")
                
        # 2. Categorize active tasks
        today = datetime.now().date()
        overdue_tasks = []
        due_today_tasks = []
        upcoming_tasks = []
        backlog_tasks = []
        
        for task in active_next_actions:
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
                    
        # 3. Rebuild Next_Actions.md content
        new_content = """# ⚡ Next Actions

> Flexible, context-tagged action list. Filter in Telegram using `/next @context`.

---

## ⚡ Next Actions
"""
        all_active = overdue_tasks + due_today_tasks + upcoming_tasks + backlog_tasks
        if all_active:
            for task in all_active:
                new_content += f"{task}\n"
        else:
            new_content += "\n🎉 *All clear! No active next actions remaining.*\n"
            
        # Write back to file if changed
        if os.path.exists(TASKS_MD_FILE):
            with open(TASKS_MD_FILE, "r", encoding="utf-8") as f:
                current_raw = f.read()
                
            if current_raw.strip() != new_content.strip():
                atomic_write(TASKS_MD_FILE, new_content)
                print("Tasks Dashboard: Regenerated and categorized Tasks.md successfully.")
            
    except Exception as e:
        print(f"Tasks Dashboard: Error maintaining task list: {e}")
