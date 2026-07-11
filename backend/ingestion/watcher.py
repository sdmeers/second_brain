import os
import re
import shutil
import asyncio
from datetime import datetime
from backend.config import (
    INBOX_MD_FILE,
    INBOX_PATH,
    INBOX_FAILED_PATH,
    COMPLETED_MD_FILE,
    FAILED_MD_FILE,
    ENABLE_OBSIDIAN_INBOX_POLLING,
    ENABLE_TELEGRAM_BOT,
    ENABLE_GMAIL_POLLING,
    TELEGRAM_BOT_TOKEN,
    GMAIL_EMAIL,
    GMAIL_APP_PASSWORD,
    SOURCES_PATH,
    OBSIDIAN_VAULT_PATH
)
from backend.ingestion.queue import (
    init_queue_db,
    add_to_queue,
    get_next_pending_item,
    update_item_status
)
from backend.ingestion.classifier import classify_input
from backend.processing.capture import process_capture_core, DuplicateURLError
from backend.ingestion.telegram import poll_telegram, notify_via_telegram
from backend.ingestion.gmail import poll_gmail

# Specific Ingestion Handlers
from backend.ingestion.handlers.task import process_task_item
from backend.ingestion.handlers.project import process_project_item
from backend.ingestion.handlers.voice import process_voice_dump_item


async def scan_and_process_import():
    """Scans the consolidated Import/Inbox.md file,
    extracts URLs and notes, queues them in SQLite, and cleans Inbox.md."""
    if not os.path.exists(INBOX_MD_FILE):
        return

    try:
        with open(INBOX_MD_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"Backend [Watcher]: Error reading {INBOX_MD_FILE}: {e}")
        return

    # Find all URLs in Inbox.md
    matches = list(re.finditer(r'(https?://[^\s)\]\n]+)', content))
    if not matches:
        return

    # Process the first URL match
    m = matches[0]
    url = m.group(1)
    
    # Determine the boundaries of this URL's block (including notes)
    block_end = matches[1].start() if len(matches) > 1 else len(content)
    notes = content[m.end():block_end].strip()
    
    print(f"Backend [Watcher]: Queueing URL from Import Inbox: {url}")
    
    try:
        payload = {
            "source": "import_inbox_md",
            "url": url,
            "title": "Mobile Link Import"
        }
        add_to_queue(
            source_type="file_drop",
            raw_payload=payload,
            content_text=f"{url}\n\n{notes}" if notes else url
        )
        success = True
    except Exception as e:
        print(f"Backend [Watcher]: Failed to queue URL {url}: {e}")
        success = False

    # Update Inbox.md immediately to remove the processed URL block
    new_content = content[:m.start()] + content[block_end:]
    
    try:
        with open(INBOX_MD_FILE, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        print(f"Backend [Watcher]: CRITICAL - Could not write back to {INBOX_MD_FILE}: {e}")
        return
        
    if not success:
        # Append back as failed if we couldn't write it to the DB queue
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        failed_entry = f"- {url} - Failed to queue on {timestamp}\n"
        with open(FAILED_MD_FILE, "a", encoding="utf-8") as f:
            f.write(failed_entry)

async def scan_and_process_inbox():
    """Scans 01_Inbox/ folder for dropped files (markdown, PDF, images, etc.).
    Queues them in the database and cleans up the folder (transient queue)."""
    if not os.path.exists(INBOX_PATH):
        return
        
    files = [
        f for f in os.listdir(INBOX_PATH) 
        if os.path.isfile(os.path.join(INBOX_PATH, f)) 
        and f not in ["Inbox.md", "Completed.md", "Failed.md"]
    ]
    
    if not files:
        return
        
    print(f"Backend [Watcher]: Found {len(files)} dropped file(s) in inbox.")
    
    for filename in files:
        file_path = os.path.join(INBOX_PATH, filename)
        print(f"Backend [Watcher]: Queueing dropped file: {filename}")
        
        try:
            if filename.lower().endswith(".md"):
                # For markdown notes: read text, queue, delete
                with open(file_path, "r", encoding="utf-8") as f:
                    text_content = f.read()
                
                payload = {
                    "filename": filename,
                    "type": "note"
                }
                add_to_queue(source_type="file_drop", raw_payload=payload, content_text=text_content)
                os.remove(file_path)
                print(f"Backend [Watcher]: Queued and removed markdown file {filename}")
                
            else:
                # For non-markdown files (PDF, DOCX, etc.):
                # Copy to permanent sources directory, queue with file path reference, delete from inbox
                os.makedirs(SOURCES_PATH, exist_ok=True)
                dest_path = os.path.join(SOURCES_PATH, filename)
                shutil.copy2(file_path, dest_path)
                
                payload = {
                    "filename": filename,
                    "type": "file"
                }
                add_to_queue(source_type="file_drop", raw_payload=payload, file_path=dest_path)
                os.remove(file_path)
                print(f"Backend [Watcher]: Queued, moved to Sources/ and removed file {filename}")
                
        except Exception as e:
            print(f"Backend [Watcher]: Error queueing dropped file {filename}: {e}")

async def process_queue_worker():
    """Background worker that pulls items from the SQLite queue,
    runs the LLM classifier, routes, and processes them."""
    item = get_next_pending_item()
    if not item:
        return
        
    item_id = item["id"]
    source_type = item["source_type"]
    raw_payload = json_loads_safe(item["raw_payload"])
    content_text = item["content_text"] or ""
    file_path = item["file_path"]
    
    print(f"\nBackend [Queue Worker]: Processing item ID {item_id} (Source: {source_type})")
    update_item_status(item_id, "processing")
    
    try:
        # Check if the file is an audio file and needs transcription
        if file_path and (file_path.lower().endswith(('.ogg', '.mp3', '.wav', '.m4a', '.3gp', '.opus'))):
            if not content_text.strip():
                print(f"Backend [Queue Worker]: Audio file detected ({os.path.basename(file_path)}). Running transcription...")
                from backend.processing.transcriber import transcribe_audio_local
                transcription = await transcribe_audio_local(file_path)
                content_text = transcription
                
                # Update database record with the transcribed text to cache it
                from backend.ingestion.queue import get_db_connection
                db_conn = get_db_connection()
                db_cursor = db_conn.cursor()
                db_cursor.execute("UPDATE ingestion_queue SET content_text = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (transcription, item_id))
                db_conn.commit()
                db_conn.close()
                print("Backend [Queue Worker]: Transcription completed and cached in database.")
                
        # Determine raw classification text
        classification_input = content_text
        if file_path and not classification_input:
            classification_input = f"File: {os.path.basename(file_path)}"
            
        # 1. Run LLM Router (Classifier)
        classification = await classify_input(classification_input)
        print(f"Backend [Queue Worker]: Router classified item as '{classification.category}' (confidence: {classification.confidence})")
        
        # 2. Process based on classification
        success = False
        result_title = "Ingested Capture"
        
        # Pull out URL if present
        urls = re.findall(r'(https?://[^\s)\]\n]+)', classification_input)
        url = urls[0] if urls else None
        
        source_label = f"{source_type.title()} Input"
        
        if classification.category == "deep_research" or (file_path and file_path.lower().endswith(".pdf")):
            # Full Centaur Brain Deep Research workflow
            target_url = url or f"https://local.brain/deep-research/{item_id}"
            target_title = raw_payload.get("title") or os.path.basename(file_path or "").rsplit(".", 1)[0] or "Deep Research Capture"
            notes = classification_input.replace(target_url, "").strip() if target_url in classification_input else classification_input
            
            pdf_bytes = None
            if file_path and file_path.lower().endswith(".pdf"):
                with open(file_path, "rb") as f:
                    pdf_bytes = f.read()
            
            result = await process_capture_core(
                url=target_url,
                title=target_title,
                markdown_text=notes,
                pdf_bytes=pdf_bytes,
                deep_extraction=True
            )
            result_title = result.get("title", target_title)
            success = True
            
        elif classification.category == "task":
            # Task Handler: extract check-items and append to Tasks.md
            msg = await process_task_item(classification_input, source_label)
            result_title = "Appended Task(s)"
            success = True
            
        elif classification.category == "voice_dump":
            # Voice Splitter Handler: split multi-topic dump and re-queue items
            msg = await process_voice_dump_item(classification_input, source_label)
            result_title = "Split Voice Dump"
            success = True
            
        elif classification.category in ["meeting_transcript", "project_note"]:
            # Project Handler: save note in matching 02_Projects folder
            result = await process_project_item(classification_input, source_label)
            result_title = f"Project Note: {result.get('title')}"
            success = True
            
        elif classification.category in ["quick_note", "idea", "reference"]:
            # Quick Note/Idea/Reference: check if it matches an active project folder
            # If yes: saves inside project. If no: matches to 'None' and we save as lightweight summary
            from backend.config import OBSIDIAN_VAULT_PATH, GEMINI_MODEL
            from google.genai import types
            from backend.utils.gemini import call_gemini_with_retry
            projects_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
            active_projects = []
            if os.path.exists(projects_dir):
                active_projects = [d for d in os.listdir(projects_dir) if os.path.isdir(os.path.join(projects_dir, d)) and not d.startswith(".")]
            
            # Map using project match helper
            from backend.ingestion.handlers.project import ProjectMatchResult
            system_instruction = f"Analyze note and map to exactly ONE of the active projects: {active_projects}. Return 'None' if it does not fit."
            try:
                config = types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.1, response_mime_type="application/json", response_schema=ProjectMatchResult)
                response = await call_gemini_with_retry(model=GEMINI_MODEL, contents=f"Map this to project:\n\n{classification_input}", config=config)
                match = ProjectMatchResult.model_validate_json(response.text.strip())
            except Exception:
                match = ProjectMatchResult(matched_project="None", note_title="Quick Capture", reasoning="Error")
                
            if match.matched_project in active_projects:
                # Save inside the matched project folder!
                result = await process_project_item(classification_input, source_label)
                result_title = f"Project Note: {result.get('title')} ({match.matched_project})"
                success = True
            else:
                # Save as standard lightweight summary in Summaries/ (no concepts updated)
                target_url = url or f"https://local.brain/bookmark/{item_id}"
                target_title = raw_payload.get("title") or match.note_title or "Bookmark Capture"
                notes = classification_input.replace(target_url, "").strip() if target_url in classification_input else classification_input
                
                result = await process_capture_core(
                    url=target_url,
                    title=target_title,
                    markdown_text=notes,
                    deep_extraction=False
                )
                result_title = result.get("title", target_title)
                success = True
            
        elif classification.category == "url_capture" or url:
            # Lightweight URL Capture (without concept updates)
            target_url = url or f"https://local.brain/bookmark/{item_id}"
            target_title = raw_payload.get("title") or "Bookmark Capture"
            notes = classification_input.replace(target_url, "").strip() if target_url in classification_input else classification_input
            
            result = await process_capture_core(
                url=target_url,
                title=target_title,
                markdown_text=notes,
                deep_extraction=False
            )
            result_title = result.get("title", target_title)
            success = True
            
        else:
            # Standard fallback (lightweight summary, no concept updates)
            target_url = f"https://local.brain/fallback/{item_id}"
            target_title = f"Capture {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            result = await process_capture_core(
                url=target_url,
                title=target_title,
                markdown_text=classification_input,
                deep_extraction=False
            )
            result_title = result.get("title", target_title)
            success = True

            
        if success:
            update_item_status(item_id, "completed")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            completed_entry = f"- [[{result_title}]] - {source_type.title()} Source (Processed on {timestamp})\n"
            
            # Log to completed list atomically to prevent corruption
            try:
                from backend.vault.writer import atomic_write
                if os.path.exists(COMPLETED_MD_FILE):
                    try:
                        with open(COMPLETED_MD_FILE, "r", encoding="utf-8") as f:
                            old_content = f.read()
                    except Exception:
                        old_content = ""
                else:
                    old_content = ""
                atomic_write(COMPLETED_MD_FILE, old_content + completed_entry)
            except Exception as e:
                print(f"Backend [Queue Worker]: Error writing to {COMPLETED_MD_FILE}: {e}")
                
            # Auto-commit to Git
            try:
                from backend.utils.git import commit_vault_changes
                commit_message = f"[orchestrator] Ingested: [[{result_title}]] (Category: {classification.category})"
                commit_vault_changes(commit_message)
            except Exception as git_e:
                print(f"Backend [Queue Worker]: Git commit failed: {git_e}")
                
            # Notify Steven
            await notify_via_telegram(
                f"🧠✅ Processed: [[{result_title}]]\n"
                f"Category: {classification.category} (conf: {classification.confidence})\n"
                f"Reason: {classification.reasoning}"
            )
            
    except DuplicateURLError as e:
        update_item_status(item_id, "completed")
        print(f"Backend [Queue Worker]: Duplicate detected, marked completed: {e}")
        await notify_via_telegram(f"ℹ️ Already Ingested: [[{e.existing_title}]]")
    except Exception as e:
        update_item_status(item_id, "failed", error_message=str(e))
        print(f"Backend [Queue Worker]: Error processing item ID {item_id}: {e}")
        await notify_via_telegram(f"🧠❌ Processing Failed for item ID {item_id}: {e}")

def json_loads_safe(s: str) -> dict:
    try:
        import json
        return json.loads(s)
    except Exception:
        return {}


_notified_conflicts = set()

async def scan_for_syncthing_conflicts():
    """Scans the vault directory for Syncthing conflict files and notifies the user via Telegram."""
    global _notified_conflicts
    if not OBSIDIAN_VAULT_PATH:
        return
        
    current_conflicts = set()
    try:
        def walk_files():
            found = set()
            for root, _, files in os.walk(OBSIDIAN_VAULT_PATH):
                # Ignore hidden folders
                if any(part.startswith(".") for part in root.split(os.sep)):
                    continue
                for file in files:
                    if ".sync-conflict-" in file:
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, OBSIDIAN_VAULT_PATH)
                        found.add(rel_path)
            return found
            
        current_conflicts = await asyncio.to_thread(walk_files)
    except Exception as e:
        print(f"Backend [Watcher]: Error scanning for Syncthing conflicts: {e}")
        return
        
    # Determine new conflicts we haven't notified about yet
    new_conflicts = current_conflicts - _notified_conflicts
    
    if new_conflicts:
        print(f"Backend [Watcher]: Found {len(new_conflicts)} new Syncthing conflict files!")
        conflict_list = "\n".join([f"- `{c}`" for c in sorted(new_conflicts)])
        await notify_via_telegram(
            f"⚠️ **Syncthing Sync Conflicts Detected**\n"
            f"The following new sync conflict file(s) were detected in your vault:\n\n"
            f"{conflict_list}\n\n"
            f"Please resolve these conflicts manually to avoid data loss."
        )
        
    # Update state: keep only the conflicts that still exist
    _notified_conflicts = current_conflicts

async def watch_inbox_loop():
    """Background polling loop that runs DB migrations, checks active inputs,
    and runs the queue processing worker."""
    print("Backend [Watcher]: Starting background watcher loop...")
    init_queue_db()
    
    enable_obsidian = ENABLE_OBSIDIAN_INBOX_POLLING
    enable_telegram = ENABLE_TELEGRAM_BOT
    enable_gmail = ENABLE_GMAIL_POLLING

    print(f"Backend [Watcher]: Settings -> Obsidian polling: {enable_obsidian}, Telegram polling: {enable_telegram}, Gmail polling: {enable_gmail}")

    counter = 0
    while True:
        try:
            # Ingestion steps (push to queue)
            if enable_obsidian:
                await scan_and_process_import()
                await scan_and_process_inbox()
            
            if enable_telegram:
                await poll_telegram()
                
            if enable_gmail and (counter % 4 == 0):
                await poll_gmail()
                
            # Queue processing step (pull from queue)
            await process_queue_worker()
            
            # Scan for Syncthing conflicts
            await scan_for_syncthing_conflicts()
                
        except Exception as e:
            print(f"Backend [Watcher]: Error in watch loop iteration: {e}")
            
        counter += 1
        await asyncio.sleep(15)
