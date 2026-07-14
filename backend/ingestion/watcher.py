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
from backend.ingestion.telegram import poll_telegram, poll_telegram_qa, notify_via_telegram
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
                
        # Check if the file is a PDF or PPTX and needs text extraction for classification
        elif file_path and file_path.lower().endswith(('.pdf', '.pptx')):
            if not content_text.strip():
                print(f"Backend [Queue Worker]: Document file detected ({os.path.basename(file_path)}). Running text extraction for classification...")
                try:
                    _, ext = os.path.splitext(file_path.lower())
                    extracted_text = ""
                    if ext == ".pdf":
                        from backend.processing.extractor import extract_pdf_text_from_bytes
                        with open(file_path, "rb") as f:
                            extracted_text = extract_pdf_text_from_bytes(f.read())
                    elif ext == ".pptx":
                        from backend.processing.extractor import extract_pptx_text
                        extracted_text = extract_pptx_text(file_path)
                    
                    if extracted_text.strip():
                        content_text = extracted_text
                        # Update database record with the extracted text to cache it
                        from backend.ingestion.queue import get_db_connection
                        db_conn = get_db_connection()
                        db_cursor = db_conn.cursor()
                        db_cursor.execute("UPDATE ingestion_queue SET content_text = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (extracted_text, item_id))
                        db_conn.commit()
                        db_conn.close()
                        print("Backend [Queue Worker]: Document text extraction completed and cached in database.")
                except Exception as ext_e:
                    print(f"Backend [Queue Worker]: Error extracting text for classification: {ext_e}")
                
        # Check if the file is an image file and needs OCR/text extraction
        elif file_path and file_path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.tiff', '.bmp')):
            print(f"Backend [Queue Worker]: Image file detected ({os.path.basename(file_path)}). Running multimodal text extraction...")
            try:
                # Determine MIME type
                _, ext = os.path.splitext(file_path.lower())
                mime_type = "image/png"
                if ext in [".jpg", ".jpeg"]:
                    mime_type = "image/jpeg"
                elif ext == ".webp":
                    mime_type = "image/webp"
                
                with open(file_path, "rb") as img_f:
                    img_bytes = img_f.read()
                    
                from backend.config import GEMINI_MODEL
                from google.genai import types
                from backend.utils.gemini import call_gemini_with_retry
                
                image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
                
                # If the user provided a caption or instruction, use it to guide extraction
                if content_text.strip():
                    prompt = (
                        f"Analyze the image and follow this instruction: {content_text.strip()}\n"
                        "Return only the extracted information requested."
                    )
                else:
                    prompt = (
                        "You are an OCR and document extraction system. Read this image and extract all text inside it.\n"
                        "If it is an ID card, driving license, passport, receipt, or document, extract and list all fields clearly.\n"
                        "Return only the extracted text, keeping the layout logical."
                    )
                
                response = await call_gemini_with_retry(
                    model=GEMINI_MODEL,
                    contents=[image_part, prompt]
                )
                
                extracted_text = response.text.strip()
                if extracted_text:
                    content_text = extracted_text
                    # Update database record with the extracted text to cache it
                    from backend.ingestion.queue import get_db_connection
                    db_conn = get_db_connection()
                    db_cursor = db_conn.cursor()
                    db_cursor.execute("UPDATE ingestion_queue SET content_text = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (extracted_text, item_id))
                    db_conn.commit()
                    db_conn.close()
                    print("Backend [Queue Worker]: Image text extraction completed and cached in database.")
            except Exception as img_e:
                print(f"Backend [Queue Worker]: Error extracting text from image: {img_e}")

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
        
        # Check if Research Brain is explicitly requested
        research_requested = False
        triggers = [
            "/research_brain", "/research", "#research", "add to research brain", "add to research",
            "/centaur", "add to centaur brain", "#centaur", "centaur brain"
        ]
        text_lower = classification_input.lower()
        subject_lower = str(raw_payload.get("subject", "")).lower()
        
        for trigger in triggers:
            if trigger in text_lower or trigger in subject_lower:
                research_requested = True
                break
                
        # Clean trigger tags from input text so they don't appear in the final note body
        clean_input = classification_input
        for trigger in triggers:
            pattern = re.compile(re.escape(trigger), re.IGNORECASE)
            clean_input = pattern.sub("", clean_input).strip()
            
        saved_file_path = None
        if research_requested or classification.category == "deep_research" or (file_path and file_path.lower().endswith((".pdf", ".pptx"))):
            # Full Research Brain Deep Research workflow ONLY if explicitly requested, otherwise light capture
            target_url = url or f"https://local.brain/deep-research/{item_id}"
            target_title = raw_payload.get("title") or os.path.basename(file_path or "").rsplit(".", 1)[0] or "Deep Research Capture"
            notes = clean_input.replace(target_url, "").strip() if target_url in clean_input else clean_input
            
            result = await process_capture_core(
                url=target_url,
                title=target_title,
                markdown_text=notes,
                file_path=file_path,
                deep_extraction=research_requested
            )
            result_title = result.get("title", target_title)
            saved_file_path = result.get("node_path")
            success = True
            
        elif classification.category == "task":
            # Task Handler: extract check-items and append to Tasks.md
            msg = await process_task_item(classification_input, source_label)
            result_title = "Appended Task(s)"
            saved_file_path = os.path.join(OBSIDIAN_VAULT_PATH, "Tasks.md")
            success = True
            
        elif classification.category == "voice_dump":
            # Voice Splitter Handler: split multi-topic dump and re-queue items
            msg = await process_voice_dump_item(classification_input, source_label)
            result_title = "Split Voice Dump"
            success = True
            
        elif classification.category in ["quick_note", "idea", "reference", "meeting_transcript", "project_note"]:
            # Quick Note/Idea/Reference/Meeting/Project Note: check if it matches an active project or area folder
            # If yes: saves inside project or area. If no: saves as lightweight summary or future project idea
            from pydantic import BaseModel, Field
            class PARARoutingResult(BaseModel):
                matched_type: str = Field(description="The type of folder matched. Must be one of: 'project', 'area', or 'None'.")
                matched_name: str = Field(description="The exact name of the project or area matched, or 'None'.")
                note_title: str = Field(description="A clean, descriptive title for this note (3 to 6 words).")
                reasoning: str = Field(description="Brief explanation of the match.")

            from backend.config import OBSIDIAN_VAULT_PATH, GEMINI_MODEL
            from google.genai import types
            from backend.utils.gemini import call_gemini_with_retry
            from backend.utils.sanitize import sanitize_filename
            
            projects_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
            active_projects = []
            if os.path.exists(projects_dir):
                active_projects = [d for d in os.listdir(projects_dir) if os.path.isdir(os.path.join(projects_dir, d)) and not d.startswith(".")]
                
            areas_dir = os.path.join(OBSIDIAN_VAULT_PATH, "03_Areas")
            active_areas_map = {}
            if os.path.exists(areas_dir):
                for root, dirs, files in os.walk(areas_dir):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for d in dirs:
                        full_path = os.path.join(root, d)
                        rel_path = os.path.relpath(full_path, areas_dir)
                        active_areas_map[d] = rel_path
            
            system_instruction = (
                f"You are a personal information librarian for Steven's Second Brain.\n"
                f"Your task is to analyze the incoming note and map it to exactly ONE of the active Projects or Areas if it fits.\n\n"
                f"Active Projects (cased exactly): {active_projects}\n"
                f"Active Areas (cased exactly): {list(active_areas_map.keys())}\n\n"
                f"CRITICAL INSTRUCTION:\n"
                f"- If the note is about an ongoing area of life or a domain of interest (e.g., family/Rosie, cookery, health, music, finance/legal, server administration, coding, or work domains TF RAID, Dstl, Southampton), map it to 'area' with the matched area name.\n"
                f"- If the note is about a specific temporary project with a deadline/goal (e.g., 'Second Brain', 'Hermes', 'VibeCook'), map it to 'project' with the matched project name.\n"
                f"- If it doesn't clearly fit any of these, set matched_type to 'None'.\n"
                f"- Generatively produce a clean, brief note title (3 to 6 words) in note_title (do not use quotes or special characters)."
            )
            
            try:
                config = types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=PARARoutingResult
                )
                response = await call_gemini_with_retry(
                    model=GEMINI_MODEL,
                    contents=f"Map this note:\n\n{classification_input}",
                    config=config
                )
                match = PARARoutingResult.model_validate_json(response.text.strip())
            except Exception as e:
                print(f"Watcher Router: Error matching PARA category via Gemini: {e}")
                match = PARARoutingResult(
                    matched_type="None",
                    matched_name="None",
                    note_title="Quick Capture",
                    reasoning=f"Fallback due to error: {e}"
                )
                
            if match.matched_type == "project" and match.matched_name in active_projects:
                # Save inside the matched project folder
                result = await process_project_item(
                    classification_input, source_label,
                    category=classification.category, raw_file_path=file_path,
                    matched_project=match.matched_name, matched_title=match.note_title
                )
                result_title = f"Project Note: {result.get('title')} ({match.matched_name})"
                saved_file_path = result.get("file_path")
                success = True
                
            elif match.matched_type == "area" and match.matched_name in active_areas_map:
                # Save inside the matched area folder!
                target_area_rel_path = active_areas_map[match.matched_name]
                target_area_dir = os.path.join(OBSIDIAN_VAULT_PATH, "03_Areas", target_area_rel_path)
                
                safe_title = sanitize_filename(match.note_title)
                file_path_dest = os.path.join(target_area_dir, f"{safe_title}.md")
                
                # Resolve name collisions
                counter = 1
                base_file_path = file_path_dest
                while os.path.exists(file_path_dest):
                    file_path_dest = base_file_path.replace(".md", f"_{counter}.md")
                    counter += 1
                    
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # Copy raw source file if provided (excluding .md and .txt duplicates)
                source_link = source_label
                if file_path and os.path.exists(file_path):
                    _, ext = os.path.splitext(file_path.lower())
                    if ext not in [".md", ".txt"]:
                        try:
                            import shutil
                            raw_dest_name = f"{os.path.basename(file_path_dest).rsplit('.', 1)[0]}_raw{ext}"
                            raw_dest_path = os.path.join(target_area_dir, raw_dest_name)
                            shutil.copy2(file_path, raw_dest_path)
                            source_link = f"[[{raw_dest_name}]]"
                        except Exception as copy_e:
                            print(f"Area Router: Failed to copy raw file: {copy_e}")
                        
                file_content = f"""---
title: "{match.note_title}"
area: "{match.matched_name}"
date_created: "{timestamp}"
source: "{source_link}"
tags: [area, {match.matched_name.lower().replace(' ', '_')}]
---
# {match.note_title}

*Created on {timestamp} via {source_link}*

---

{clean_input}
"""
                from backend.vault.writer import atomic_write
                atomic_write(file_path_dest, file_content)
                result_title = f"Area Note: {match.note_title} ({match.matched_name})"
                saved_file_path = file_path_dest
                success = True
                
            elif classification.category == "idea":
                # Save general project ideas in the 02_Projects/Ideas/ folder!
                ideas_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects", "Ideas")
                os.makedirs(ideas_dir, exist_ok=True)
                safe_title = sanitize_filename(match.note_title)
                file_path_dest = os.path.join(ideas_dir, f"{safe_title}.md")
                
                # Check for name collisions
                counter = 1
                base_file_path = file_path_dest
                while os.path.exists(file_path_dest):
                    file_path_dest = base_file_path.replace(".md", f"_{counter}.md")
                    counter += 1
                
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # Copy raw source file if provided (excluding .md and .txt duplicates)
                source_link = source_label
                if file_path and os.path.exists(file_path):
                    _, ext = os.path.splitext(file_path.lower())
                    if ext not in [".md", ".txt"]:
                        try:
                            import shutil
                            raw_dest_name = f"{os.path.basename(file_path_dest).rsplit('.', 1)[0]}_raw{ext}"
                            raw_dest_path = os.path.join(ideas_dir, raw_dest_name)
                            shutil.copy2(file_path, raw_dest_path)
                            source_link = f"[[{raw_dest_name}]]"
                        except Exception as copy_e:
                            print(f"Project Router: Failed to copy raw idea file: {copy_e}")
                
                file_content = f"""---
title: "{match.note_title}"
date_created: "{timestamp}"
source: "{source_link}"
tags: [future_project, idea]
---
# {match.note_title}

*Created on {timestamp} via {source_link}*

---

{clean_input}
"""
                from backend.vault.writer import atomic_write
                atomic_write(file_path_dest, file_content)
                result_title = f"Future Project Idea: {match.note_title}"
                saved_file_path = file_path_dest
                success = True
                
            else:
                # Save as standard lightweight summary in Reference_Notes/ (no concepts updated)
                target_url = url or f"https://local.brain/bookmark/{item_id}"
                target_title = raw_payload.get("title") or match.note_title or "Bookmark Capture"
                notes_clean = clean_input.replace(target_url, "").strip() if target_url in clean_input else clean_input
                
                result = await process_capture_core(
                    url=target_url,
                    title=target_title,
                    markdown_text=notes_clean,
                    file_path=file_path,
                    deep_extraction=False
                )
                result_title = result.get("title", target_title)
                saved_file_path = result.get("node_path")
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
            saved_file_path = result.get("node_path")
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
            saved_file_path = result.get("node_path")
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
                
            # Extract display location relative to vault root
            location_str = "Unknown"
            if saved_file_path:
                try:
                    rel_path = os.path.relpath(saved_file_path, OBSIDIAN_VAULT_PATH)
                    location_str = os.path.dirname(rel_path) or "root"
                except Exception:
                    pass
                
            # Save the processed title and node path in raw_payload so callback/Jira can access it later
            try:
                import json
                from backend.ingestion.queue import get_db_connection
                db_conn = get_db_connection()
                db_cursor = db_conn.cursor()
                db_cursor.execute("SELECT raw_payload FROM ingestion_queue WHERE id = ?", (item_id,))
                row = db_cursor.fetchone()
                if row:
                    payload_dict = json.loads(row["raw_payload"]) if row["raw_payload"] else {}
                    payload_dict["processed_title"] = result_title
                    payload_dict["saved_file_path"] = saved_file_path
                    db_cursor.execute("UPDATE ingestion_queue SET raw_payload = ? WHERE id = ?", (json.dumps(payload_dict), item_id))
                db_conn.commit()
                db_conn.close()
            except Exception as payload_e:
                print(f"Backend [Queue Worker]: Failed to save processed_title in database: {payload_e}")

            # Present a "Send to Jira" inline button
            reply_markup = None
            if item_id:
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "📤 Send to Jira",
                                "callback_data": f"jira:{item_id}"
                            }
                        ]
                    ]
                }

            # Notify Steven
            await notify_via_telegram(
                f"🧠✅ Processed: [[{result_title}]]\n"
                f"Category: {classification.category} (conf: {classification.confidence})\n"
                f"Location: {location_str}\n"
                f"Reason: {classification.reasoning}",
                reply_markup=reply_markup
            )
            
    except DuplicateURLError as e:
        update_item_status(item_id, "completed")
        print(f"Backend [Queue Worker]: Duplicate detected, marked completed: {e}")
        await notify_via_telegram(f"ℹ️ Already Ingested: [[{e.existing_title}]]")
    except Exception as e:
        update_item_status(item_id, "failed", error_message=str(e))
        print(f"Backend [Queue Worker]: Error processing item ID {item_id}: {e}")
        await notify_via_telegram(f"🧠❌ Processing Failed for item ID {item_id}: {e}")
    finally:
        # Clean up temporary staged Telegram files
        if file_path and os.path.exists(file_path):
            if "staging" in os.path.abspath(file_path):
                try:
                    os.remove(file_path)
                    print(f"Backend [Queue Worker]: Cleaned up staged file: {os.path.basename(file_path)}")
                except Exception as cleanup_e:
                    print(f"Backend [Queue Worker]: Failed to remove staged file {file_path}: {cleanup_e}")

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
                await poll_telegram_qa()
                
            if enable_gmail and (counter % 4 == 0):
                await poll_gmail()
                
            # Queue processing step (pull from queue)
            await process_queue_worker()
            
            # Scan for Syncthing conflicts
            await scan_for_syncthing_conflicts()
            
            # Reorganize Tasks.md dashboard and archive completed items
            try:
                from backend.utils.tasks_dashboard import maintain_tasks_dashboard
                await maintain_tasks_dashboard()
            except Exception as dashboard_e:
                print(f"Backend [Watcher]: Error running tasks dashboard maintenance: {dashboard_e}")
                
        except Exception as e:
            print(f"Backend [Watcher]: Error in watch loop iteration: {e}")
            
        counter += 1
        await asyncio.sleep(15)
