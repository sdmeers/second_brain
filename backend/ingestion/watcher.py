import os
import re
import shutil
import asyncio
import httpx
from datetime import datetime
from pydantic import BaseModel, Field
from backend.config import (
    INBOX_MD_FILE,
    INBOX_PATH,
    COMPLETED_MD_FILE,
    FAILED_MD_FILE,
    ENABLE_OBSIDIAN_INBOX_POLLING,
    ENABLE_TELEGRAM_BOT,
    ENABLE_GMAIL_POLLING,
    SOURCES_PATH,
    OBSIDIAN_VAULT_PATH,
    TASKS_MD_FILE
)
from backend.ingestion.queue import (
    init_queue_db,
    add_to_queue,
    get_next_pending_item,
    update_item_status
)
from backend.ingestion.classifier import classify_input
from backend.processing.capture import process_capture_core, DuplicateURLError
from backend.ingestion.telegram import run_telegram_bot_loop, notify_via_telegram
from backend.ingestion.gmail import poll_gmail

# Specific Ingestion Handlers
from backend.ingestion.handlers.task import process_task_item
from backend.ingestion.handlers.project import process_project_item
from backend.ingestion.handlers.voice import process_voice_dump_item
from backend.prompts import load_prompt


async def scan_and_process_import():
    """Scans the consolidated Import/Inbox.md file,
    extracts URLs and notes, queues them in SQLite, and cleans Inbox.md."""
    if not os.path.exists(INBOX_MD_FILE):
        return

    try:
        with open(INBOX_MD_FILE, encoding="utf-8") as f:
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
    """Scans 00_System/Inbox/ folder for dropped files (markdown, PDF, images, etc.).
    Queues them in the database and cleans up the folder (transient queue)."""
    if not os.path.exists(INBOX_PATH):
        return
        
    files = [
        f for f in os.listdir(INBOX_PATH) 
        if os.path.isfile(os.path.join(INBOX_PATH, f)) 
        and f not in ["Inbox.md", "Completed.md", "Failed.md"]
        and not any(f.endswith(ext) for ext in ["_capture.md", "_voice.md", "_voice.ogg", "_voice.mp3"])
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
                with open(file_path, encoding="utf-8") as f:
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

AUDIO_EXTS = ('.ogg', '.mp3', '.wav', '.m4a', '.3gp', '.opus')
DOC_EXTS = ('.pdf', '.pptx')
IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.tiff', '.bmp')

IMAGE_MIME_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

# Phrases that deterministically force the Deep Research pipeline, bypassing the classifier.
RESEARCH_TRIGGERS = (
    "/research_brain", "/research", "#research", "add to research brain", "add to research",
    "/centaur", "add to centaur brain", "#centaur", "centaur brain",
)

PARA_NOTE_CATEGORIES = ("quick_note", "idea", "reference", "personal_record", "meeting_transcript", "project_note")


def build_routing_text(content: str, raw_payload: dict, file_path: str = None) -> str:
    """Prefix the content with its provenance (email subject, original filename, email
    body) so the classifier and PARA matcher can use it. Not saved into the note."""
    header = []
    if raw_payload.get("subject"):
        header.append(f"Subject: {raw_payload['subject']}")
    filename = raw_payload.get("filename") or (os.path.basename(file_path) if file_path else "")
    if filename:
        header.append(f"Filename: {filename}")
    body = (raw_payload.get("body") or "").strip()
    if body:
        header.append(f"Email note: {body[:1000]}")
    if not header:
        return content
    return "\n".join(header) + "\n\n" + content


def cache_content_text(item_id: int, text: str):
    """Persist extracted/transcribed text back onto the queue row so a retry of this
    item does not re-run transcription or OCR."""
    from backend.ingestion.queue import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE ingestion_queue SET content_text = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (text, item_id)
        )
        conn.commit()
    finally:
        conn.close()


async def _extract_image_text(file_path: str, caption: str) -> str:
    """OCR / describe an image via the multimodal model."""
    _, ext = os.path.splitext(file_path.lower())
    mime_type = IMAGE_MIME_TYPES.get(ext, "image/png")

    img_bytes = await asyncio.to_thread(_read_file_bytes, file_path)

    from google.genai import types

    from backend.config import GEMINI_MODEL
    from backend.utils.gemini import call_gemini_with_retry

    image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime_type)

    if caption.strip():
        prompt = load_prompt("image_instruction", caption=caption.strip())
    else:
        prompt = load_prompt("image_ocr")

    response = await call_gemini_with_retry(model=GEMINI_MODEL, contents=[image_part, prompt])
    return response.text.strip()


def _read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def prepare_content(item_id: int, file_path: str, content_text: str) -> str | None:
    """Turn a queued attachment into classifiable text.

    Returns the text to classify, or None if the item should be dropped (a blank
    voice note). Extracted text is cached back to the queue row.
    """
    if not file_path:
        return content_text

    lowered = file_path.lower()

    if lowered.endswith(AUDIO_EXTS):
        if content_text.strip():
            return content_text
        print(f"Backend [Queue Worker]: Audio file detected ({os.path.basename(file_path)}). Running transcription...")
        from backend.processing.transcriber import transcribe_audio_local
        transcription = await transcribe_audio_local(file_path)
        cache_content_text(item_id, transcription)

        if not transcription.strip():
            print(f"Backend [Queue Worker]: Audio transcription for {os.path.basename(file_path)} "
                  "was blank/silent. Skipping ingestion.")
            return None
        return transcription

    if lowered.endswith(DOC_EXTS):
        if content_text.strip():
            return content_text
        print(f"Backend [Queue Worker]: Document file detected ({os.path.basename(file_path)}). "
              "Running text extraction for classification...")
        try:
            _, ext = os.path.splitext(lowered)
            if ext == ".pdf":
                from backend.processing.extractor import extract_pdf_text_from_bytes
                raw = await asyncio.to_thread(_read_file_bytes, file_path)
                extracted = extract_pdf_text_from_bytes(raw)
            else:
                from backend.processing.extractor import extract_pptx_text
                extracted = await asyncio.to_thread(extract_pptx_text, file_path)

            if extracted.strip():
                cache_content_text(item_id, extracted)
                print("Backend [Queue Worker]: Document text extraction completed and cached in database.")
                return extracted
        except Exception as ext_e:
            print(f"Backend [Queue Worker]: Error extracting text for classification: {ext_e}")
        return content_text

    if lowered.endswith(IMAGE_EXTS):
        print(f"Backend [Queue Worker]: Image file detected ({os.path.basename(file_path)}). "
              "Running multimodal text extraction...")
        try:
            extracted = await _extract_image_text(file_path, content_text)
            if extracted:
                cache_content_text(item_id, extracted)
                print("Backend [Queue Worker]: Image text extraction completed and cached in database.")
                return extracted
        except Exception as img_e:
            print(f"Backend [Queue Worker]: Error extracting text from image: {img_e}")
        return content_text

    return content_text


def detect_research_request(text: str, subject: str = "") -> bool:
    """True when the input explicitly asks for the Deep Research pipeline.

    Text and subject are searched separately so a trigger cannot match by spanning
    the boundary between them.
    """
    text_lower = (text or "").lower()
    subject_lower = (subject or "").lower()
    return any(t in text_lower or t in subject_lower for t in RESEARCH_TRIGGERS)


def strip_research_triggers(text: str) -> str:
    """Remove trigger phrases so they don't leak into the saved note body."""
    for trigger in RESEARCH_TRIGGERS:
        text = re.compile(re.escape(trigger), re.IGNORECASE).sub("", text).strip()
    return text


async def looks_like_document(url: str, file_path: str) -> bool:
    """Whether this capture should take the deep document-extraction path."""
    if file_path and file_path.lower().endswith(DOC_EXTS):
        return True
    if not url:
        return False

    clean_u = url.lower().split("?")[0]
    if clean_u.endswith(DOC_EXTS) or "/pdf/" in clean_u or "/manuals/" in clean_u:
        return True

    # Fall back to a HEAD request for URLs that serve documents without a file
    # extension. This previously raised NameError (httpx was never imported) and the
    # error was swallowed, so the probe never actually ran.
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=5.0) as check_client:
            head_res = await check_client.head(url, headers={"User-Agent": "Mozilla/5.0"})
            ct = head_res.headers.get("content-type", "").lower()
            return "application/pdf" in ct or "powerpoint" in ct
    except Exception as e:
        print(f"Backend [Queue Worker]: Content-type probe failed for {url}: {e}")
        return False


def write_para_note(target_dir: str, title: str, body: str, tags: list[str],
                    source_label: str, file_path: str = None,
                    extra_frontmatter: dict = None) -> str:
    """Write a note into a PARA folder, copying any raw source file alongside it.

    Single implementation of what were two near-identical 45-line blocks in the
    queue worker (one for 03_Areas matches, one for 02_Projects/Ideas).
    """
    from backend.utils.sanitize import sanitize_filename
    from backend.vault.tasks import dump_frontmatter

    os.makedirs(target_dir, exist_ok=True)
    safe_title = sanitize_filename(title) or "Untitled Capture"
    dest_path = os.path.join(target_dir, f"{safe_title}.md")

    # Resolve name collisions
    counter = 1
    stem = dest_path[:-3]
    while os.path.exists(dest_path):
        dest_path = f"{stem}_{counter}.md"
        counter += 1

    # Copy the raw source file next to the note so the capture stays traceable
    source_link = source_label
    if file_path and os.path.exists(file_path):
        _, ext = os.path.splitext(file_path.lower())
        if ext not in (".md", ".txt"):
            try:
                raw_dest_name = f"{os.path.basename(dest_path)[:-3]}_raw{ext}"
                shutil.copy2(file_path, os.path.join(target_dir, raw_dest_name))
                source_link = f"[[{raw_dest_name}]]"
            except OSError as copy_e:
                print(f"Backend [PARA Writer]: Failed to copy raw file: {copy_e}")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    frontmatter = {"title": title, "date_created": timestamp, "source": source_link, "tags": tags}
    if extra_frontmatter:
        frontmatter.update(extra_frontmatter)

    content = (
        f"{dump_frontmatter(frontmatter)}"
        f"# {title}\n\n"
        f"*Created on {timestamp} via {source_link}*\n\n"
        f"---\n\n"
        f"{body}\n"
    )

    from backend.vault.writer import atomic_write
    atomic_write(dest_path, content)
    return dest_path


class PARARoutingResult(BaseModel):
    matched_type: str = Field(description="The type of folder matched. Must be one of: 'project', 'area', or 'None'.")
    matched_name: str = Field(description="The exact name of the project or area matched, or 'None'.")
    note_title: str = Field(description="A clean, descriptive title for this note (3 to 6 words).")
    reasoning: str = Field(description="Brief explanation of the match.")


async def match_para_folder(text: str) -> tuple[PARARoutingResult, list[str], dict]:
    """Ask the model which active Project or Area this note belongs to.

    Returns (match, active_project_names, active_area_name_to_relpath).
    """
    from google.genai import types

    from backend.config import GEMINI_MODEL
    from backend.utils.gemini import call_gemini_with_retry
    from backend.vault.paths import list_active_projects, list_areas

    active_projects = list(list_active_projects())
    active_areas_map = list_areas()

    system_instruction = load_prompt(
        "para_matcher", active_projects=active_projects, active_areas=list(active_areas_map.keys())
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
            contents=f"Map this note:\n\n{text}",
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

    return match, active_projects, active_areas_map


# Where an unmatched personal record lands. Records must never fall through to the
# Research_Brain/Resources capture pipeline (that is how an insurance schedule ended up in
# Research_Brain/Summaries with its policy details merged into concept pages).
PERSONAL_RECORD_DEFAULT_AREA = "Finance_and_legal"


async def route_para_note(item_id: int, classification, classification_input: str, clean_input: str,
                          url: str, file_path: str, raw_payload: dict, source_label: str,
                          routing_text: str = None) -> tuple[str, str, str]:
    """Route a quick_note/idea/reference/personal_record/meeting/project_note to its PARA home.

    routing_text is what the matcher sees (the content plus Subject/Filename context);
    clean_input is what gets saved. Returns (result_title, saved_file_path, pipeline).
    """
    match, active_projects, active_areas_map = await match_para_folder(routing_text or classification_input)
    print(f"Backend [Queue Worker]: PARA match: {match.matched_type} '{match.matched_name}' ({match.reasoning})")

    # 1. Matched an active project -> save inside the project folder
    if match.matched_type == "project" and match.matched_name in active_projects:
        result = await process_project_item(
            classification_input, source_label,
            category=classification.category, raw_file_path=file_path,
            matched_project=match.matched_name, matched_title=match.note_title
        )
        return result.get("title"), result.get("file_path"), f"PARA Project ({match.matched_name})"

    area = match.matched_name if match.matched_type == "area" else None
    if area not in active_areas_map:
        area = None
    if area is None and classification.category == "personal_record":
        area = PERSONAL_RECORD_DEFAULT_AREA

    # 2. Matched an area (or an unmatched personal record) -> save inside the area folder
    if area:
        relpath = active_areas_map.get(area, area)
        target_dir = os.path.join(OBSIDIAN_VAULT_PATH, "03_Areas", relpath)
        tags = ["area", area.lower().replace(" ", "_")]
        if classification.category == "personal_record":
            tags.append("record")
        dest = write_para_note(
            target_dir, match.note_title, clean_input,
            tags=tags, source_label=source_label, file_path=file_path,
            extra_frontmatter={"area": area},
        )
        return match.note_title, dest, f"PARA Area ({relpath})"

    # 3. Unmatched idea -> park it in the project ideas folder
    if classification.category == "idea":
        target_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects", "Ideas")
        dest = write_para_note(
            target_dir, match.note_title, clean_input,
            tags=["future_project", "idea"],
            source_label=source_label, file_path=file_path,
        )
        return match.note_title, dest, "PARA Ideas"

    # 4. Otherwise fall back to a lightweight reference summary in 04_Resources
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
    return result.get("title", target_title), result.get("node_path"), "Light reference (04_Resources)"


async def finalise_item(item_id: int, classification, result_title: str, saved_file_path: str,
                        source_type: str, clean_input: str, research_requested: bool,
                        instruction_msg: str = None, pipeline: str = None):
    """Post-processing shared by every successful ingest: log, commit, index, enrich, notify.

    Each step is independently guarded -- a failure in indexing must not lose the note.
    """
    update_item_status(item_id, "completed")

    location_str = "Unknown"
    if saved_file_path:
        try:
            rel_path = os.path.relpath(saved_file_path, OBSIDIAN_VAULT_PATH)
            location_str = os.path.dirname(rel_path) or "root"
        except ValueError:
            pass

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    completed_entry = f"- [[{result_title}]] - {source_type.title()} Source (Processed on {timestamp})\n"

    try:
        from backend.vault.writer import atomic_write
        old_content = ""
        if os.path.exists(COMPLETED_MD_FILE):
            try:
                with open(COMPLETED_MD_FILE, encoding="utf-8") as f:
                    old_content = f.read()
            except OSError:
                old_content = ""
        atomic_write(COMPLETED_MD_FILE, old_content + completed_entry)
    except Exception as e:
        print(f"Backend [Queue Worker]: Error writing to {COMPLETED_MD_FILE}: {e}")

    # Git commit (blocking subprocess -> off the event loop)
    try:
        from backend.utils.git import commit_vault_changes
        commit_message = f"[orchestrator] Ingested: [[{result_title}]] (Category: {classification.category})"
        await asyncio.to_thread(commit_vault_changes, commit_message)
    except Exception as git_e:
        print(f"Backend [Queue Worker]: Git commit failed: {git_e}")

    try:
        from backend.utils.activity_log import log_librarian_activity
        log_librarian_activity(
            title=result_title,
            location=location_str,
            source_type=f'{classification.category} via {source_type}'
        )
    except Exception as log_e:
        print(f'Backend [Queue Worker]: Activity log write failed: {log_e}')

    try:
        from backend.rag.vectorstore import index_vault
        print("Backend [Queue Worker]: Ingest complete. Running incremental vector indexer...")
        await index_vault()
    except Exception as index_e:
        print(f"Backend [Queue Worker]: Incremental indexing failed: {index_e}")

    try:
        from backend.utils.librarian import enrich_note_metadata
        if saved_file_path and saved_file_path.endswith('.md'):
            print('Backend [Queue Worker]: Enriching note with tags and wikilinks...')
            await enrich_note_metadata(
                saved_file_path=saved_file_path,
                note_text=clean_input,
                classification_category=classification.category,
                is_research=research_requested
            )
    except Exception as enrich_e:
        print(f'Backend [Queue Worker]: Note enrichment failed (non-fatal): {enrich_e}')

    # Stash the result on the queue row so the Jira callback can find it later
    try:
        import json

        from backend.ingestion.queue import get_db_connection
        conn = get_db_connection()
        try:
            row = conn.execute("SELECT raw_payload FROM ingestion_queue WHERE id = ?", (item_id,)).fetchone()
            if row:
                payload_dict = json.loads(row["raw_payload"]) if row["raw_payload"] else {}
                payload_dict["processed_title"] = result_title
                payload_dict["saved_file_path"] = saved_file_path
                conn.execute("UPDATE ingestion_queue SET raw_payload = ? WHERE id = ?",
                             (json.dumps(payload_dict), item_id))
                conn.commit()
        finally:
            conn.close()
    except Exception as payload_e:
        print(f"Backend [Queue Worker]: Failed to save processed_title in database: {payload_e}")

    if classification.category == "instruction":
        await notify_via_telegram(instruction_msg or "🧠✅ Vault instruction executed.")
        return

    reply_markup = {
        "inline_keyboard": [[{"text": "📤 Send to Jira", "callback_data": f"jira:{item_id}"}]]
    }
    await notify_via_telegram(
        f"🧠✅ Processed: [[{result_title}]]\n"
        f"Category: {classification.category} (conf: {classification.confidence})\n"
        f"Pipeline: {pipeline or 'Unknown'}\n"
        f"Location: {location_str}\n"
        f"Reason: {classification.reasoning}",
        reply_markup=reply_markup
    )


async def classify_item(classification_input: str, research_requested: bool):
    """Run the LLM router, honouring the deterministic /research override."""
    if research_requested:
        from backend.ingestion.classifier import ClassificationResult
        print("Backend [Queue Worker]: Router detected explicit /research trigger (Category: deep_research)")
        return ClassificationResult(
            category="deep_research",
            confidence=1.0,
            reasoning="Explicit /research command trigger."
        )

    classification = await classify_input(classification_input)
    print(f"Backend [Queue Worker]: Router classified item as '{classification.category}' "
          f"(confidence: {classification.confidence})")
    return classification


async def process_queue_worker(max_items: int = 5):
    """Drain up to max_items from the SQLite queue.

    Previously handled exactly one item per 15s tick, so a backlog of 20 captures took
    five minutes to clear.
    """
    for _ in range(max_items):
        item = get_next_pending_item()
        if not item:
            return
        await _process_one_item(item)


async def _process_one_item(item):
    item_id = item["id"]
    source_type = item["source_type"]
    raw_payload = json_loads_safe(item["raw_payload"])
    content_text = item["content_text"] or ""
    file_path = item["file_path"]

    print(f"\nBackend [Queue Worker]: Processing item ID {item_id} (Source: {source_type})")
    update_item_status(item_id, "processing")

    try:
        # Guard: a staged file already consumed by a timestamp-collision duplicate
        if file_path and not os.path.exists(file_path) and "staging" in file_path:
            print(f"Backend [Queue Worker]: Staged file missing for item {item_id} "
                  f"({os.path.basename(file_path)}). Likely already consumed by a "
                  f"duplicate queue entry. Marking completed.")
            update_item_status(item_id, "completed",
                               error_message="Staging file already consumed by duplicate queue entry")
            return

        # 1. Turn attachments into classifiable text
        prepared = await prepare_content(item_id, file_path, content_text)
        if prepared is None:
            update_item_status(item_id, "completed", error_message="Blank/silent voice note ignored.")
            if file_path and os.path.exists(file_path) and ("staging" in file_path or "00_System/Inbox" in file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            return
        content_text = prepared

        classification_input = content_text
        if file_path and not classification_input:
            classification_input = f"File: {os.path.basename(file_path)}"

        # What the classifier and PARA matcher see: the content plus where it came from.
        # The subject line ("Insurance schedule") was previously never shown to either.
        routing_text = build_routing_text(classification_input, raw_payload, file_path)

        # 2. Classify
        research_requested = detect_research_request(
            classification_input, str(raw_payload.get("subject", ""))
        )
        classification = await classify_item(routing_text, research_requested)

        # 3. Route. The classifier decides; a file merely being a PDF no longer forces the
        # Deep Research pipeline (that sent an insurance schedule classified 'reference'
        # into Research_Brain). Only explicit /research or a 'deep_research' verdict goes
        # deep -- plus a bare document URL, which the classifier can only call url_capture.
        urls = re.findall(r'(https?://[^\s)\]\n]+)', classification_input)
        url = urls[0] if urls else None
        source_label = f"{source_type.title()} Input"
        clean_input = strip_research_triggers(classification_input)
        instruction_msg = None
        is_document_file = bool(file_path) and file_path.lower().endswith(DOC_EXTS)

        go_deep = research_requested or classification.category == "deep_research"
        if not go_deep and classification.category == "url_capture" and url and not file_path:
            go_deep = await looks_like_document(url, None)

        if go_deep:
            # Full Research / Document Deep Extraction workflow
            target_url = url or f"https://local.brain/deep-research/{item_id}"
            target_title = raw_payload.get("title") or (
                os.path.basename(file_path).rsplit(".", 1)[0] if file_path else ""
            )
            notes = clean_input.replace(target_url, "").strip() if target_url in clean_input else clean_input

            result = await process_capture_core(
                url=target_url, title=target_title, markdown_text=notes,
                file_path=file_path, deep_extraction=True, is_research=True
            )
            result_title = result.get("title", target_title)
            saved_file_path = result.get("node_path")
            pipeline = "Deep Research"

        elif is_document_file or classification.category in PARA_NOTE_CATEGORIES:
            # A document is always filed so the file itself is kept, even when the
            # classifier calls it a task or instruction (those paths only keep text).
            result_title, saved_file_path, pipeline = await route_para_note(
                item_id, classification, classification_input, clean_input,
                url, file_path, raw_payload, source_label, routing_text=routing_text
            )

        elif classification.category == "task":
            await process_task_item(classification_input, source_label)
            result_title = "Tasks"
            saved_file_path = TASKS_MD_FILE
            pipeline = "Task"

        elif classification.category == "voice_dump":
            await process_voice_dump_item(classification_input, source_label)
            result_title = "Split Voice Dump"
            saved_file_path = None
            pipeline = "Voice dump split"

        elif classification.category == "instruction":
            from backend.ingestion.handlers.instruction import process_vault_instruction
            instruction_msg = await process_vault_instruction(classification_input, source_label)
            result_title = "Vault Instruction Executed"
            saved_file_path = None
            pipeline = "Instruction"

        elif classification.category == "url_capture" or url:
            target_url = url or f"https://local.brain/bookmark/{item_id}"
            target_title = raw_payload.get("title") or "Bookmark Capture"
            notes = classification_input.replace(target_url, "").strip() \
                if target_url in classification_input else classification_input
            result = await process_capture_core(
                url=target_url, title=target_title, markdown_text=notes, deep_extraction=False
            )
            result_title = result.get("title", target_title)
            saved_file_path = result.get("node_path")
            pipeline = "Light reference (URL)"

        else:
            target_url = f"https://local.brain/fallback/{item_id}"
            target_title = f"Capture {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            result = await process_capture_core(
                url=target_url, title=target_title,
                markdown_text=classification_input, deep_extraction=False
            )
            result_title = result.get("title", target_title)
            saved_file_path = result.get("node_path")
            pipeline = "Fallback capture"

        print(f"Backend [Queue Worker]: Routed item {item_id} via {pipeline}")

        # 4. Log, commit, index, enrich, notify
        await finalise_item(
            item_id, classification, result_title, saved_file_path,
            source_type, clean_input, research_requested, instruction_msg, pipeline
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
        if file_path and os.path.exists(file_path) and "staging" in os.path.abspath(file_path):
            try:
                os.remove(file_path)
                print(f"Backend [Queue Worker]: Cleaned up staged file: {os.path.basename(file_path)}")
            except OSError as cleanup_e:
                print(f"Backend [Queue Worker]: Failed to remove staged file {file_path}: {cleanup_e}")


def json_loads_safe(s: str) -> dict:
    try:
        import json
        return json.loads(s)
    except Exception:
        return {}


_notified_conflicts = set()

# Strong references to long-lived background tasks (see watch_inbox_loop).
_WATCHER_TASKS: set = set()

# Syncthing's own marker. The device-suffix variant is intentionally narrow: an earlier
# `-[A-Z0-9]{7}\.md$` also matched ordinary notes (e.g. "Notes-ABC1234.md") and the healer
# would then move or delete them. Requiring the base name to also exist means we only
# treat a file as a conflict copy when there is a real original for it to conflict with.
SYNC_CONFLICT_MARKER = ".sync-conflict-"
DEVICE_SUFFIX_RE = re.compile(r'^(?P<base>.+)-(?P<device>[A-Z0-9]{7})\.md$')


def is_sync_conflict_file(filename: str, directory: str = None) -> bool:
    """Whether ``filename`` looks like a Syncthing conflict copy."""
    if SYNC_CONFLICT_MARKER in filename:
        return True

    m = DEVICE_SUFFIX_RE.match(filename)
    if not m:
        return False

    # Only a conflict copy if the original it shadows is actually present.
    if directory is None:
        return False
    return os.path.exists(os.path.join(directory, f"{m.group('base')}.md"))

# Task-file conflicts the healer refused to merge; left for the user, never retried.
_refused_task_heals: set = set()


def _archive_new_completions(lines: list[str]) -> int:
    """Archives completed lines that aren't already in z_Completed.md under today's date."""
    from backend.config import COMPLETED_TASKS_MD_FILE
    from backend.vault.tasks import archive_completed_lines, format_completed_line
    existing = set()
    if os.path.exists(COMPLETED_TASKS_MD_FILE):
        with open(COMPLETED_TASKS_MD_FILE, encoding="utf-8") as f:
            existing = {line.strip() for line in f}
    fresh = [line for line in lines if format_completed_line(line) not in existing]
    return archive_completed_lines(fresh)


async def heal_task_file_conflict(rel_path: str, full_path: str, target_path: str, fname: str) -> bool:
    """Union-merges a conflict copy of a GTD task file into the original.

    Both copies are backed up outside the vault and committed to git *before* anything
    is written, and the conflict copy is removed only after the merged file is on disk.
    If the merge would drop an open task it is refused and both files are left alone.
    """
    from backend.vault import task_history
    from backend.vault.task_merge import merge_task_file
    from backend.vault.writer import atomic_write

    if rel_path in _refused_task_heals:
        return False
    print(f"Backend [Auto-Healer]: Merging task conflict file {rel_path}...")

    with open(full_path, encoding="utf-8") as f:
        conflict_text = f.read()
    original_text, original_mtime = "", None
    if os.path.exists(target_path):
        original_mtime = os.stat(target_path).st_mtime_ns
        with open(target_path, encoding="utf-8") as f:
            original_text = f.read()

    backup_dir = await asyncio.to_thread(
        task_history.backup_files, [target_path, full_path], f"conflict-{fname}")
    await asyncio.to_thread(task_history.commit_task_snapshot, f"Before merging {rel_path}")

    result = merge_task_file(fname, original_text, conflict_text)
    if result.lost:
        _refused_task_heals.add(rel_path)
        print(f"Backend [Auto-Healer]: Refusing to merge {rel_path}: would drop {len(result.lost)} task(s).")
        preview = "\n".join(f"- {line}" for line in result.lost[:10])
        await notify_via_telegram(
            f"⚠️ **Task Sync Conflict Needs You**\n"
            f"Merging `{rel_path}` would have dropped {len(result.lost)} open task(s), so I left "
            f"both files untouched:\n{preview}\n\nBackup: `{backup_dir}`"
        )
        return False

    # Syncthing may have replaced the original while we were merging; if so, start over
    # on the next pass rather than overwrite what just arrived.
    current_mtime = os.stat(target_path).st_mtime_ns if os.path.exists(target_path) else None
    if current_mtime != original_mtime:
        print(f"Backend [Auto-Healer]: {fname} changed during merge; retrying next pass.")
        return False

    archived = _archive_new_completions(result.completed)
    if result.content.strip() != original_text.strip():
        atomic_write(target_path, result.content)
    os.remove(full_path)
    await asyncio.to_thread(task_history.commit_task_snapshot, f"Merged {rel_path}")

    print(f"Backend [Auto-Healer]: Task conflict {rel_path} merged "
          f"({len(result.open_items)} open, {len(result.recovered)} recovered, {archived} archived).")
    lines = [f"🧠⚡ **Merged Task Sync Conflict** in `{fname}`",
             f"• {len(result.open_items)} open task(s) kept"]
    if result.recovered:
        lines.append(f"• {len(result.recovered)} only in the other device's copy, now restored:")
        lines += [f"   - {line}" for line in result.recovered[:10]]
    if archived:
        lines.append(f"• {archived} completed task(s) moved to z_Completed.md")
    lines.append(f"Both copies backed up to `{backup_dir}`.")
    await notify_via_telegram("\n".join(lines))
    return True


async def auto_heal_syncthing_conflict(rel_path: str, full_path: str) -> bool:
    """Attempts to automatically resolve and merge Syncthing conflict files without data loss.
    Returns True if fully resolved and deleted, False if manual user intervention is needed.
    """
    try:
        dir_name = os.path.dirname(full_path)
        base_name = os.path.basename(full_path)
        
        # Determine the target original file name (e.g. Next_Actions.md from Next_Actions.sync-conflict-*.md or Next_Actions-OROPVGA.md)
        target_name = re.sub(r'\.sync-conflict-\d+-\d+-\w+', '', base_name)
        target_name = re.sub(r'\.sync-conflict-\d+-\w+', '', target_name)
        target_name = re.sub(r'-[A-Z0-9]{7}(?=\.md$)', '', target_name)
        target_path = os.path.join(dir_name, target_name)

        # Case A: Conflict is on one of the GTD task lists in 01_Tasks/
        from backend.config import TASKS_PATH
        from backend.vault.task_merge import canonical_task_file_name
        task_fname = canonical_task_file_name(target_name)
        if task_fname and os.path.abspath(dir_name) == os.path.abspath(TASKS_PATH):
            return await heal_task_file_conflict(rel_path, full_path, target_path, task_fname)

        # Case B: Target file does not exist on disk
        if not os.path.exists(target_path):
            print(f"Backend [Auto-Healer]: Restoring missing file {target_name} from conflict {rel_path}...")
            shutil.move(full_path, target_path)
            await notify_via_telegram(f"🧠⚡ **Auto-Restored File**: Restored `[{target_name}]` from `{rel_path}`.")
            return True
            
        # Case C: Both files exist and are identical
        else:
            with open(full_path, encoding="utf-8") as f:
                c_content = f.read()
            with open(target_path, encoding="utf-8") as f:
                t_content = f.read()
            if c_content.strip() == t_content.strip():
                print(f"Backend [Auto-Healer]: Conflict file {rel_path} is identical to target. Removing conflict file.")
                os.remove(full_path)
                return True
                
    except Exception as e:
        print(f"Backend [Auto-Healer]: Error during auto-heal for {rel_path}: {e}")
        
    return False

async def scan_for_syncthing_conflicts():
    """Scans the vault directory for Syncthing conflict files, auto-heals where possible, and notifies for remainder."""
    global _notified_conflicts
    if not OBSIDIAN_VAULT_PATH:
        return
        
    current_conflicts = set()
    try:
        def walk_files():
            found = set()
            for root, _, files in os.walk(OBSIDIAN_VAULT_PATH):
                if any(part.startswith(".") for part in root.split(os.sep)):
                    continue
                for file in files:
                    if is_sync_conflict_file(file, root):
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, OBSIDIAN_VAULT_PATH)
                        found.add((rel_path, full_path))
            return found
            
        found_conflicts = await asyncio.to_thread(walk_files)
    except Exception as e:
        print(f"Backend [Watcher]: Error scanning for Syncthing conflicts: {e}")
        return
        
    for rel_path, full_path in found_conflicts:
        if os.path.exists(full_path):
            healed = await auto_heal_syncthing_conflict(rel_path, full_path)
            if not healed:
                current_conflicts.add(rel_path)
                
    # Determine new conflicts requiring manual attention
    new_conflicts = current_conflicts - _notified_conflicts
    
    if new_conflicts:
        print(f"Backend [Watcher]: Found {len(new_conflicts)} new unresolvable Syncthing conflict files!")
        conflict_list = "\n".join([f"- `{c}`" for c in sorted(new_conflicts)])
        await notify_via_telegram(
            f"⚠️ **Syncthing Sync Conflicts Detected**\n"
            f"The following new sync conflict file(s) were detected in your vault:\n\n"
            f"{conflict_list}\n\n"
            f"Please resolve these conflicts manually to avoid data loss."
        )
        
    _notified_conflicts = current_conflicts

async def watch_inbox_loop():
    """Background polling loop that runs DB migrations, checks active inputs,
    and runs the queue processing worker."""
    print("Backend [Watcher]: Starting background watcher loop...")
    from backend.config import ensure_vault_layout
    ensure_vault_layout()
    init_queue_db()
    
    enable_obsidian = ENABLE_OBSIDIAN_INBOX_POLLING
    enable_telegram = ENABLE_TELEGRAM_BOT
    enable_gmail = ENABLE_GMAIL_POLLING

    print(f"Backend [Watcher]: Settings -> Obsidian polling: {enable_obsidian}, Telegram polling: {enable_telegram}, Gmail polling: {enable_gmail}")

    if enable_telegram:
        # Hold a strong reference; a task the loop only weakly references can be GC'd.
        bot_task = asyncio.create_task(run_telegram_bot_loop())
        _WATCHER_TASKS.add(bot_task)
        bot_task.add_done_callback(_WATCHER_TASKS.discard)

    counter = 0
    while True:
        try:
            # Ingestion steps run in parallel so one source never blocks another
            tasks = []
            if enable_obsidian:
                tasks.append(scan_and_process_import())
                tasks.append(scan_and_process_inbox())
                
            if enable_gmail and (counter % 4 == 0):
                tasks.append(poll_gmail())
                
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                
            # Queue processing step (pull from queue)
            await process_queue_worker()
            
            # Scan for Syncthing conflicts
            await scan_for_syncthing_conflicts()
            
            # Periodically run incremental vector indexing (every 2 minutes)
            if counter % 8 == 0:
                try:
                    from backend.rag.vectorstore import index_vault
                    print("Backend [Watcher]: Running periodic incremental vector indexer...")
                    await index_vault()
                except Exception as index_e:
                    print(f"Backend [Watcher]: Periodic indexing failed: {index_e}")
            
            # Reorganize Next_Actions.md dashboard and archive completed items.
            # Stays on the loop: it awaits the project-promotion prompt, and measured at
            # well under a millisecond on a real vault.
            try:
                from backend.utils.tasks_dashboard import maintain_tasks_dashboard
                await maintain_tasks_dashboard()
            except Exception as dashboard_e:
                print(f"Backend [Watcher]: Error running tasks dashboard maintenance: {dashboard_e}")

            # Commit 01_Tasks/ to the vault's git once the server's writes have settled
            from backend.vault.task_history import maybe_commit_task_changes
            await maybe_commit_task_changes()

        except Exception as e:
            print(f"Backend [Watcher]: Error in watch loop iteration: {e}")
            
        counter += 1
        await asyncio.sleep(15)
