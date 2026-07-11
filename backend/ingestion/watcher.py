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
    GMAIL_APP_PASSWORD
)
from backend.processing.capture import process_capture_core, DuplicateURLError
from backend.ingestion.telegram import poll_telegram
from backend.ingestion.gmail import poll_gmail

async def scan_and_process_import():
    """Scans the consolidated Import/Inbox.md for any URLs,
    processes them one by one, and updates Inbox.md, Completed.md, and Failed.md."""
    if not os.path.exists(INBOX_MD_FILE):
        return

    while True:
        try:
            with open(INBOX_MD_FILE, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"Backend [Watcher]: Error reading {INBOX_MD_FILE}: {e}")
            break

        # Find all URLs in Inbox.md
        matches = list(re.finditer(r'(https?://[^\s)\]\n]+)', content))
        if not matches:
            break

        # Process the first URL match
        m = matches[0]
        url = m.group(1)
        
        # Determine the boundaries of this URL's block (including notes)
        block_end = matches[1].start() if len(matches) > 1 else len(content)
        notes = content[m.end():block_end].strip()
        
        # Prepare title hint and trigger processing
        title_hint = "Untitled Mobile Import"
        print(f"Backend [Watcher]: Processing URL from Import Inbox: {url}")
        
        success = False
        error_msg = ""
        result = None
        
        is_duplicate = False
        try:
            result = await process_capture_core(
                url=url,
                title=title_hint,
                markdown_text=notes
            )
            success = True
        except DuplicateURLError as e:
            is_duplicate = True
            success = True
            result = {"title": e.existing_title}
        except Exception as e:
            error_msg = str(e)
            print(f"Backend [Watcher]: Failed to process URL {url}: {e}")
            
        # Update Inbox.md immediately to remove the processed URL block
        new_content = content[:m.start()] + content[block_end:]
        
        try:
            with open(INBOX_MD_FILE, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as e:
            print(f"Backend [Watcher]: CRITICAL - Could not write back to {INBOX_MD_FILE}: {e}")
            # Break to avoid infinite loops if disk is full / read-only
            break
            
        # Append to Completed.md or Failed.md
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if success:
            safe_title = result.get("title", "Untitled")
            entry_suffix = " (Duplicate - Already Ingested)" if is_duplicate else ""
            completed_entry = f"- [[{safe_title}]] - {url} (Processed on {timestamp}){entry_suffix}\n"
            try:
                with open(COMPLETED_MD_FILE, "a", encoding="utf-8") as f:
                    f.write(completed_entry)
                print(f"Backend [Watcher]: Successfully processed {url} -> Added to {COMPLETED_MD_FILE}")
            except Exception as e:
                print(f"Backend [Watcher]: Error writing to {COMPLETED_MD_FILE}: {e}")
        else:
            failed_entry = f"- {url} - Failed on {timestamp}: {error_msg}\n"
            if notes:
                indented_notes = "\n".join([f"  {line}" for line in notes.splitlines()])
                failed_entry += f"  Notes:\n{indented_notes}\n"
            try:
                with open(FAILED_MD_FILE, "a", encoding="utf-8") as f:
                    f.write(failed_entry)
                print(f"Backend [Watcher]: Failed to process {url} -> Added to {FAILED_MD_FILE}")
            except Exception as e:
                print(f"Backend [Watcher]: Error writing to {FAILED_MD_FILE}: {e}")
                
        # Add a small delay between multiple items in the same batch
        await asyncio.sleep(1)

async def scan_and_process_inbox():
    """Scans INBOX_PATH for any .md files, parses the first URL,
    processes it, and handles cleanup or failure moves."""
    if not os.path.exists(INBOX_PATH):
        return
        
    # Get all markdown files in the inbox folder (non-recursively, skipping directories)
    files = [f for f in os.listdir(INBOX_PATH) if f.lower().endswith(".md") and os.path.isfile(os.path.join(INBOX_PATH, f))]
    
    if not files:
        return
        
    print(f"Backend [Watcher]: Found {len(files)} potential captures in inbox.")
    
    for filename in files:
        file_path = os.path.join(INBOX_PATH, filename)
        print(f"Backend [Watcher]: Processing file: {filename}")
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            # 1. Parse URL using regex
            url_match = re.search(r'(https?://[^\s)\]\n]+)', content)
            if not url_match:
                print(f"Backend [Watcher]: No URL found in {filename}. Moving to Failed/...")
                failed_dest = os.path.join(INBOX_FAILED_PATH, f"[No URL] - {filename}")
                shutil.move(file_path, failed_dest)
                continue
                
            url = url_match.group(1)
            
            # 2. Extract title hint from filename (strip "Capture - " prefixes and .md extension)
            title_hint, _ = os.path.splitext(filename)
            title_hint = re.sub(r'^(Capture|capture)\s*-\s*', '', title_hint).strip()
            if not title_hint or title_hint.lower() == "capture" or title_hint.lower() == "untitled":
                title_hint = "Untitled Mobile Capture"
                
            # 3. Extract remaining body text as markdown_text (if any)
            # Remove the URL line from content to avoid processing it as body text
            markdown_text = content.replace(url, "").strip()
            
            print(f"Backend [Watcher]: Triggering ingestion for URL: {url} (Title Hint: {title_hint})")
            
            # 4. Process capture natively using our extracted core logic!
            await process_capture_core(
                url=url,
                title=title_hint,
                markdown_text=markdown_text
            )
            
            # 5. On success, delete the processed inbox note
            print(f"Backend [Watcher]: Successfully processed {filename}. Deleting from inbox...")
            os.remove(file_path)
            
        except DuplicateURLError as e:
            print(f"Backend [Watcher]: File {filename} is a duplicate (already ingested as '{e.existing_title}'). Deleting from inbox...")
            try:
                os.remove(file_path)
            except Exception as del_err:
                print(f"Backend [Watcher]: Error deleting duplicate file: {del_err}")
        except Exception as e:
            print(f"Backend [Watcher]: Failed to process file {filename}: {e}")
            try:
                # To prevent infinite looping on rate limits or API issues, move file to Failed
                failed_name = f"[Failed] - {filename}"
                failed_dest = os.path.join(INBOX_FAILED_PATH, failed_name)
                # Overwrite if already exists in Failed
                if os.path.exists(failed_dest):
                    os.remove(failed_dest)
                shutil.move(file_path, failed_dest)
                print(f"Backend [Watcher]: Moved failed capture to {failed_dest}")
            except Exception as move_err:
                print(f"Backend [Watcher]: CRITICAL - Could not move failed file: {move_err}")

async def watch_inbox_loop():
    """Background polling loop that scans Telegram, Gmail, and/or the old Obsidian Import/Inbox.md paths."""
    print("Backend [Watcher]: Starting background watcher loop...")
    
    enable_obsidian = ENABLE_OBSIDIAN_INBOX_POLLING
    enable_telegram = ENABLE_TELEGRAM_BOT
    enable_gmail = ENABLE_GMAIL_POLLING

    print(f"Backend [Watcher]: Settings -> Obsidian polling: {enable_obsidian}, Telegram polling: {enable_telegram}, Gmail polling: {enable_gmail}")

    # Validate configuration on startup
    if enable_telegram and not TELEGRAM_BOT_TOKEN:
        print("Backend [Watcher]: WARNING - Telegram Bot is enabled but TELEGRAM_BOT_TOKEN is missing or empty in .env.")
    if enable_gmail and (not GMAIL_EMAIL or not GMAIL_APP_PASSWORD):
        print("Backend [Watcher]: WARNING - Gmail polling is enabled but GMAIL_EMAIL or GMAIL_APP_PASSWORD is missing or empty in .env.")

    counter = 0
    while True:
        try:
            if enable_obsidian:
                await scan_and_process_import()
                await scan_and_process_inbox()
            
            if enable_telegram:
                await poll_telegram()
                
            if enable_gmail and (counter % 4 == 0):
                await poll_gmail()
                
        except Exception as e:
            print(f"Backend [Watcher]: Error in watch loop iteration: {e}")
            
        counter += 1
        await asyncio.sleep(15)
