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

async def maintain_tasks_dashboard():
    """Reads Tasks.md, moves completed tasks to Completed_Tasks.md,
    and reorganizes active tasks into Overdue, Due Today, Upcoming, and Backlog sections."""
    tasks_file = TASKS_MD_FILE
    completed_file = COMPLETED_TASKS_MD_FILE
    
    if not os.path.exists(tasks_file):
        return
        
    try:
        # Read the current task file contents
        with open(tasks_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        active_tasks = []
        newly_completed = []
        
        for line in lines:
            stripped = line.strip()
            # Match task checkmarks
            if stripped.startswith("- [") and len(stripped) >= 6:
                checkbox = stripped[3:4]
                
                is_completed = checkbox.lower() in ["x", "v"] # support x or checked mark
                
                if is_completed:
                    newly_completed.append(stripped)
                else:
                    active_tasks.append(stripped)
                    
        # 1. Handle newly completed tasks
        if newly_completed:
            today_str = datetime.now().strftime("%Y-%m-%d")
            # Load existing completed tasks if any
            existing_completed = ""
            if os.path.exists(completed_file):
                try:
                    with open(completed_file, "r", encoding="utf-8") as f:
                        existing_completed = f.read()
                except Exception:
                    pass
            
            if not existing_completed.strip():
                existing_completed = "# 🎓 Completed Tasks Archive\n\n> Historical log of checked off tasks.\n\n"
                
            # Append completed tasks with completion date
            updated_completed = existing_completed
            if not updated_completed.endswith("\n") and updated_completed.strip():
                updated_completed += "\n"
                
            for task in newly_completed:
                # Add completion timestamp if not already present
                if "Completed on" not in task:
                    task_clean = task.replace("- [x]", "- [x] ~~").replace("- [X]", "- [x] ~~") + f"~~ (Completed on {today_str})"
                else:
                    task_clean = task
                updated_completed += f"{task_clean}\n"
                
            atomic_write(completed_file, updated_completed)
            print(f"Tasks Dashboard: Moved {len(newly_completed)} completed tasks to Completed_Tasks.md")
            
        # 2. Categorize active tasks
        today = datetime.now().date()
        overdue_tasks = []
        due_today_tasks = []
        upcoming_tasks = []
        backlog_tasks = []
        
        for task in active_tasks:
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
                    
        # 3. Rebuild Tasks.md content
        new_content = """# 📋 Master Tasks List

> Master cross-project action list. Managed by the AI orchestrator. Steven can also edit directly via Obsidian.

---

## 🚨 Overdue
"""
        if overdue_tasks:
            for task in overdue_tasks:
                new_content += f"{task}\n"
        else:
            new_content += "*None*\n"
            
        new_content += "\n## 📅 Due Today\n"
        if due_today_tasks:
            for task in due_today_tasks:
                new_content += f"{task}\n"
        else:
            new_content += "*None*\n"
            
        new_content += "\n## 🗓️ Upcoming (Next 7 Days)\n"
        if upcoming_tasks:
            for task in upcoming_tasks:
                new_content += f"{task}\n"
        else:
            new_content += "*None*\n"
            
        new_content += "\n## 🗃️ Backlog\n"
        if backlog_tasks:
            for task in backlog_tasks:
                new_content += f"{task}\n"
        else:
            new_content += "*None*\n"
            
        # Write back to file if changed
        with open(tasks_file, "r", encoding="utf-8") as f:
            current_raw = f.read()
            
        if current_raw.strip() != new_content.strip():
            atomic_write(tasks_file, new_content)
            print("Tasks Dashboard: Regenerated and categorized Tasks.md successfully.")
            
    except Exception as e:
        print(f"Tasks Dashboard: Error maintaining task list: {e}")
