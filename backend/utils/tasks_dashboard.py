import os
import re
from datetime import datetime, timedelta
from backend.config import TASKS_MD_FILE
from backend.vault.writer import atomic_write
from backend.vault import task_history
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

SPLIT_FILES = ("Next_Actions.md", "Waiting_For.md", "Someday_Maybe.md")

FILE_META = {
    "Next_Actions.md": {
        "title": "# ⚡ Next Actions",
        "desc": "> Flexible, context-tagged action list. Filter in Telegram using `/next @context`.",
        "section_name": "⚡ Next Actions",
        "empty_all": "🎉 *All clear! No active next actions remaining.*",
    },
    "Waiting_For.md": {
        "title": "# ⏳ Waiting For",
        "desc": "> Delegated actions and items owed to you by others. View in Telegram using `/waiting`.",
        "section_name": "⏳ Waiting For",
        "empty_all": "🎉 *All clear! No pending items waiting on others.*",
    },
    "Someday_Maybe.md": {
        "title": "# 💡 Someday / Maybe",
        "desc": "> Future ideas, uncommitted desires, and projects to review during `/reflect`.",
        "section_name": "💡 Someday / Maybe",
        "empty_all": "🎉 *List clear! No someday/maybe items recorded.*",
    },
}

def order_tasks_by_due(tasks: list, today=None) -> list:
    """Orders task lines overdue -> due today -> upcoming (7d) -> backlog, resolving
    relative 📅 today/tomorrow markers to absolute dates along the way.

    For due-date *views* only. The task files themselves are kept in the user's manual
    priority order -- see render_split_task_file()."""
    today = today or datetime.now().date()
    overdue_tasks, due_today_tasks, upcoming_tasks, backlog_tasks = [], [], [], []
    for task in tasks:
        due_date = parse_deadline(task)

        if due_date:
            due_date_str = due_date.strftime("%Y-%m-%d")
            task = re.sub(r'📅\s*tomorrow', f'📅 {due_date_str}', task, flags=re.IGNORECASE)
            task = re.sub(r'📅\s*today', f'📅 {due_date_str}', task, flags=re.IGNORECASE)

        if due_date is None:
            backlog_tasks.append(task)
        elif due_date < today:
            overdue_tasks.append(task)
        elif due_date == today:
            due_today_tasks.append(task)
        elif today < due_date <= today + timedelta(days=7):
            upcoming_tasks.append(task)
        else:
            backlog_tasks.append(task)
    return overdue_tasks + due_today_tasks + upcoming_tasks + backlog_tasks

def split_by_category(items: list) -> tuple:
    """Splits tagged item lines into (personal, professional) buckets. Untagged items
    default to personal."""
    personal = [i for i in items if "#professional" not in i]
    professional = [i for i in items if "#professional" in i]
    return personal, professional

def resolve_relative_due(task: str) -> str:
    """Rewrites a relative ``📅 today`` / ``📅 tomorrow`` marker as an absolute date."""
    if not re.search(r'📅\s*(today|tomorrow)', task, re.IGNORECASE):
        return task
    due_date = parse_deadline(task)
    if not due_date:
        return task
    due_date_str = due_date.strftime("%Y-%m-%d")
    task = re.sub(r'📅\s*tomorrow', f'📅 {due_date_str}', task, flags=re.IGNORECASE)
    return re.sub(r'📅\s*today', f'📅 {due_date_str}', task, flags=re.IGNORECASE)

def is_placeholder_line(stripped: str) -> bool:
    """True for the "all clear" filler lines written into empty lists."""
    return ("🎉" in stripped or "Inbox Zero" in stripped or "All clear" in stripped
            or "List clear" in stripped or "*None*" in stripped)

def render_split_task_file(fname: str, items: list) -> str:
    """Renders one of SPLIT_FILES as Markdown, split into Personal / Professional.

    Items keep the order they are given in: the order of the lines in the file *is* the
    user's priority order (set by dragging in the actions web app), so nothing here
    re-sorts by due date. Use order_tasks_by_due() explicitly where a due-date view is
    wanted.
    """
    meta = FILE_META[fname]
    personal, professional = split_by_category([resolve_relative_due(i) for i in items])

    if not personal and not professional:
        return (f"{meta['title']}\n\n{meta['desc']}\n\n---\n\n"
                f"## {meta['section_name']}\n\n{meta['empty_all']}\n")

    content = f"{meta['title']}\n\n{meta['desc']}\n\n---\n\n## 🏠 Personal\n"
    content += ("\n".join(personal) + "\n") if personal else "*None*\n"
    content += "\n## 💼 Professional\n"
    content += ("\n".join(professional) + "\n") if professional else "*None*\n"
    return content

CAPTURED_HEADER = ("# 📥 Captured\n\n> Raw thoughts and voice captures awaiting clarification via "
                   "`/clarify`.\n\n---\n\n## 📥 Captured Items\n\n")
CAPTURED_EMPTY = "🎉 *Inbox Zero! No captured items awaiting clarification.*\n"

def render_captured_file(items: list) -> str:
    """Renders Captured.md: a flat list, since items haven't been categorised yet."""
    return CAPTURED_HEADER + ("\n".join(items) + "\n" if items else CAPTURED_EMPTY)

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
    """Appends an item to a GTD task file, automatically stripping any clear messages.

    Writes the same layout maintain_tasks_dashboard() produces, so the dashboard doesn't
    rewrite the file a second time moments later. Every extra write is another chance
    to collide with an edit syncing in from another device.
    """
    fname = os.path.basename(filepath)

    existing_items = []
    if os.path.exists(filepath):
        with open(filepath, encoding="utf-8") as f:
            for raw_line in f:
                s = raw_line.strip()
                if is_placeholder_line(s):
                    continue
                if fname == "Captured.md":
                    if s.startswith("- "):
                        existing_items.append(s)
                elif s.startswith("- ["):
                    if fname in SPLIT_FILES and s[3:4] == " ":
                        s = ensure_category_tag(s)
                    existing_items.append(s)

    clean_item = item_line.strip()
    if fname in SPLIT_FILES:
        clean_item = ensure_category_tag(clean_item)
    existing_items.append(clean_item)

    if fname in SPLIT_FILES:
        content = render_split_task_file(fname, existing_items)
    elif fname == "Captured.md":
        content = render_captured_file(existing_items)
    else:
        content = "\n".join(existing_items) + "\n"
    atomic_write(filepath, content)

async def _prompt_promotion_for(completed_lines: list):
    """Offers the next project step in Telegram for the first project-tagged line."""
    from backend.config import TELEGRAM_ALLOWED_CHAT_IDS, TELEGRAM_BOT_TOKEN
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_IDS):
        return
    primary_chat_id = TELEGRAM_ALLOWED_CHAT_IDS[0]
    for task_str in completed_lines:
        m_proj = re.search(r'#project/([a-zA-Z0-9_\-]+)', task_str)
        if m_proj:
            proj_tag = m_proj.group(1)
            try:
                from backend.ingestion.handlers.engage import prompt_project_step_promotion
                await prompt_project_step_promotion(primary_chat_id, proj_tag)
            except Exception as pe:
                print(f"Tasks Dashboard: Error prompting project promotion for {proj_tag}: {pe}")
            break

async def maintain_tasks_dashboard():
    """Scans all task files (Next_Actions.md, Waiting_For.md, Someday_Maybe.md, Captured.md),
    moves completed tasks to z_Completed.md (in reverse chronological order), writes List
    Clear messages for empty lists, and splits Next_Actions.md, Waiting_For.md and
    Someday_Maybe.md into Personal / Professional sections (Captured.md stays flat and
    category-agnostic since items haven't been clarified yet)."""
    from backend.config import WAITING_FOR_MD_FILE, SOMEDAY_MAYBE_MD_FILE, CAPTURED_MD_FILE

    split_file_paths = {
        "Next_Actions.md": TASKS_MD_FILE,
        "Waiting_For.md": WAITING_FOR_MD_FILE,
        "Someday_Maybe.md": SOMEDAY_MAYBE_MD_FILE,
    }

    try:
        newly_completed = []
        active_by_file = {}

        all_task_files = [
            ("Next_Actions.md", TASKS_MD_FILE),
            ("Waiting_For.md", WAITING_FOR_MD_FILE),
            ("Someday_Maybe.md", SOMEDAY_MAYBE_MD_FILE),
            ("Captured.md", CAPTURED_MD_FILE)
        ]

        skipped = set()
        for fname, fpath in all_task_files:
            if not os.path.exists(fpath):
                continue
            # Another device changed this file moments ago; rewriting it now is how
            # Syncthing conflicts start. Pick it up on a later pass instead.
            if task_history.edited_elsewhere_recently(fpath):
                skipped.add(fname)
                continue

            with open(fpath, encoding="utf-8") as f:
                lines = f.readlines()

            file_newly_completed = []
            file_active = []

            for line in lines:
                stripped = line.strip()
                if is_placeholder_line(stripped):
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
                            tagged_item = ensure_category_tag(stripped) if fname in SPLIT_FILES else stripped
                            file_active.append(tagged_item)

            active_by_file[fname] = file_active

            if file_newly_completed:
                newly_completed.extend(file_newly_completed)

            # Captured.md stays flat and category-agnostic (items aren't clarified yet)
            if fname == "Captured.md":
                content = render_captured_file(file_active)
                with open(fpath, encoding="utf-8") as rf:
                    curr_raw = rf.read()
                if curr_raw.strip() != content.strip():
                    atomic_write(fpath, content)

        # Move all newly completed tasks to z_Completed.md (reverse chronological)
        if newly_completed:
            archive_completed_lines(newly_completed)
            print(f"Tasks Dashboard: Moved {len(newly_completed)} completed tasks to z_Completed.md")

        # Rebuild Next_Actions.md, Waiting_For.md, Someday_Maybe.md, each split into
        # Personal / Professional sections. File order is preserved (it is the user's
        # priority order).
        for fname, fpath in split_file_paths.items():
            if fname in skipped:
                continue
            new_content = render_split_task_file(fname, active_by_file.get(fname, []))
            if os.path.exists(fpath):
                with open(fpath, encoding="utf-8") as f:
                    current_raw = f.read()
                if current_raw.strip() == new_content.strip():
                    continue
            atomic_write(fpath, new_content)
            print(f"Tasks Dashboard: Regenerated and categorized {fname} successfully.")

        # Only now, with every file written, prompt for project step promotion. This
        # awaits the Telegram API, and nothing may await between reading the task files
        # and rewriting them: an edit made through the actions API in that gap would be
        # overwritten by the stale copy read above.
        await _prompt_promotion_for(newly_completed)

    except Exception as e:
        print(f"Tasks Dashboard: Error maintaining task list: {e}")
