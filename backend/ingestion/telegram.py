import os
import re
import hashlib
from datetime import datetime
import httpx
from backend.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_QA_BOT_TOKEN,
    TELEGRAM_ALLOWED_CHAT_IDS,
    COMPLETED_MD_FILE,
    FAILED_MD_FILE
)
from backend.processing.capture import process_capture_core, DuplicateURLError
from backend.processing.transcriber import transcribe_audio_via_gemini
from backend.processing.extractor import analyze_image_via_gemini
from google.genai import types
from backend.config import GEMINI_MODEL
from backend.utils.gemini import call_gemini_with_retry
from backend.utils.sanitize import sanitize_filename

QA_CONTEXTS = {}

def get_qa_context(chat_id: int) -> list:
    if chat_id not in QA_CONTEXTS:
        QA_CONTEXTS[chat_id] = []
    return QA_CONTEXTS[chat_id]

def add_qa_context(chat_id: int, role: str, text: str):
    ctx = get_qa_context(chat_id)
    ctx.append({"role": role, "text": text})
    if len(ctx) > 10:  # 5 exchanges
        QA_CONTEXTS[chat_id] = ctx[-10:]

async def handle_qa_query(query_text: str, chat_id: int, token: str, http_client):
    await http_client.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "🔍 Searching your Second Brain..."}
    )
    
    from backend.rag.chat import query_brain
    
    # Add user message to context
    add_qa_context(chat_id, "user", query_text)
    chat_history = get_qa_context(chat_id)
    
    result = await query_brain(query_text, chat_history=chat_history)
    answer = result["answer"]
    
    # Add bot answer to context
    add_qa_context(chat_id, "bot", answer)
    
    await send_telegram_message_safe(http_client, token, chat_id, answer)

async def generate_title_from_text(text: str) -> str:
    prompt = "Create a brief, clean title (3 to 6 words) summarizing the following text. Do not use special characters or quotes. Output ONLY the title text."
    try:
        response = await call_gemini_with_retry(
            model=GEMINI_MODEL,
            contents=f"{prompt}\n\nText: {text[:1000]}"
        )
        title = response.text.strip().strip('"').strip("'")
        return title or "Quick Thought"
    except Exception:
        words = text.split()
        return " ".join(words[:4]) + "..." if len(words) > 4 else text

async def download_telegram_file(file_id: str, token: str, http_client: httpx.AsyncClient) -> bytes:
    file_info_url = f"https://api.telegram.org/bot{token}/getFile"
    response = await http_client.get(file_info_url, params={"file_id": file_id}, timeout=30.0)
    response.raise_for_status()
    file_path = response.json()["result"]["file_path"]
    
    file_download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    download_res = await http_client.get(file_download_url, timeout=120.0)
    download_res.raise_for_status()
    return download_res.content

async def log_import_result(result: dict, source_url: str, chat_id: int, token: str, http_client: httpx.AsyncClient):
    safe_title = result.get("title", "Untitled")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    completed_entry = f"- [[{safe_title}]] - {source_url} (Processed via Telegram on {timestamp})\n"
    with open(COMPLETED_MD_FILE, "a", encoding="utf-8") as f:
        f.write(completed_entry)
        
    await http_client.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": f"✅ Successfully Ingested: [[{safe_title}]]"}
    )

def markdown_to_telegram_html(text: str) -> str:
    """Converts standard markdown (**bold**, `code`) into safe Telegram HTML."""
    if not text:
        return ""
    # 1. Escape HTML special characters
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 2. Convert **bold** to <b>bold</b>
    t = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', t)
    # 3. Convert `code` to <code>code</code>
    t = re.sub(r'`(.*?)`', r'<code>\1</code>', t)
    return t

async def send_telegram_message_safe(client, token: str, chat_id: int | str, text: str, reply_markup: dict = None):
    """Sends a Telegram message with HTML formatting enabled, falling back to plain text if parsing fails."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    html_text = markdown_to_telegram_html(text)
    
    payload = {
        "chat_id": chat_id,
        "text": html_text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
        
    try:
        res = await client.post(url, json=payload, timeout=10.0)
        if res.status_code != 200:
            print(f"Backend [Telegram]: HTML parse status {res.status_code}: {res.text}. Falling back to plain text...")
            # Fallback to plain text if HTML parsing fails
            payload["text"] = text
            payload.pop("parse_mode", None)
            await client.post(url, json=payload, timeout=10.0)
    except Exception as e:
        print(f"Backend [Telegram]: Error sending message: {e}")

async def notify_via_telegram(message: str, reply_markup: dict = None):
    """Send a notification message to the Telegram bot chat if configured."""
    token = TELEGRAM_BOT_TOKEN
    allowed_ids = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
    if not token or not allowed_ids:
        return
    chat_id = allowed_ids.split(",")[0].strip()
    if not chat_id:
        return
    try:
        async with httpx.AsyncClient() as client:
            await send_telegram_message_safe(client, token, chat_id, message, reply_markup=reply_markup)
    except Exception as e:
        print(f"Backend [Notify]: Failed to send Telegram notification: {e}")

from backend.ingestion.queue import add_to_queue


async def process_telegram_message(message: dict, token: str, http_client):
    chat_id = message["chat"]["id"]
    text = message.get("text") or message.get("caption") or ""
    
    voice = message.get("voice")
    audio = message.get("audio")
    photo = message.get("photo")
    document = message.get("document")
    
    staging_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "staging")
    os.makedirs(staging_path, exist_ok=True)

    try:
        # Slash Command Router
        if text.startswith("/"):
            cmd_part = text.split()[0].lower()
            if cmd_part == "/in":
                capture_text = text[4:].strip()
                from datetime import datetime
                from backend.config import OBSIDIAN_VAULT_PATH
                
                timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                filename = f"{timestamp_str}_capture.md"
                inbox_path = os.path.join(OBSIDIAN_VAULT_PATH, "00_System", "Inbox")
                os.makedirs(inbox_path, exist_ok=True)
                file_path = os.path.join(inbox_path, filename)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(capture_text)
                
                summary = capture_text[:50].replace('\n', ' ') + ("..." if len(capture_text) > 50 else "")
                captured_md = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Captured.md")
                os.makedirs(os.path.dirname(captured_md), exist_ok=True)
                date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                with open(captured_md, "a", encoding="utf-8") as f:
                    f.write(f"- {summary} (Captured on {date_str} via Telegram) (Source: [[{filename}]])\n")
                
                await http_client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": f"📥 Captured: \"{summary}\"\n   → Saved to Captured.md. Process with /clarify when ready."}
                )
                return
            elif cmd_part == "/research":
                research_text = text[10:].strip()
                payload = {"chat_id": chat_id, "type": "text", "subject": "research"}
                from backend.ingestion.queue import add_to_queue
                add_to_queue(source_type="telegram", raw_payload=payload, content_text=f"/research {research_text}")
                await http_client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": f"🧠 Researching: \"{research_text[:50]}...\""}
                )
                return
            elif cmd_part in ["/ask", "/query"]:
                query_text = text[len(cmd_part):].strip()
                if not query_text:
                    await http_client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": "Please provide a query."})
                    return
                await handle_qa_query(query_text, chat_id, token, http_client)
                return
            elif cmd_part == "/help":
                help_text = """🧠 Second Brain — Your GTD + PARA Digital Assistant

━━━ CAPTURE ━━━
  /in <text/voice>    Quick capture (or just send a voice note)
  /research <URL>     Deep research ingestion with concept extraction

━━━ CLARIFY & ORGANISE ━━━
  /clarify             Process captured items through GTD workflow
  /done <item>         Mark a task as complete
  /tickle <date>       Defer an item — hidden until that date

━━━ ENGAGE ━━━
  /next                All your next actions
  /next @errands       Filter by context
  /waiting             View delegated items
  /project <name>      View project roadmap

━━━ REFLECT ━━━
  /reflect             Guided review

━━━ Q&A ━━━
  /ask <question>      Ask anything (or just type naturally)

━━━ SYSTEM ━━━
  /project <name> add <step>    Add step to project roadmap
  /undo                         Revert last vault change
  /help                         This message"""
                await http_client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": help_text})
                return
            elif cmd_part == "/clarify":
                from backend.ingestion.handlers.clarify import handle_clarify_command
                await handle_clarify_command(chat_id, token, http_client)
                return
            elif cmd_part in ["/next", "/waiting", "/project", "/done", "/tickle", "/reflect", "/undo"]:
                await http_client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": f"🚧 `{cmd_part}` handler coming in Phase 4!"})
                return
            elif cmd_part == "/jira":
                jira_text = text[5:].strip()
                if not jira_text:
                    await http_client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": "❓ Please provide a task description."})
                    return
                await http_client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": "⏳ Creating Jira issue..."})
                try:
                    lines = [line.strip() for line in jira_text.splitlines() if line.strip()]
                    if not lines: raise ValueError("Task description is empty")
                    summary = lines[0]
                    if len(summary) > 80: summary = summary[:77] + "..."
                    from backend.utils.jira import create_jira_issue
                    issue_key = await create_jira_issue(summary=summary, description=jira_text)
                    await http_client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": f"✅ Created Jira backlog issue: `{issue_key}`"})
                except Exception as e:
                    await http_client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": f"❌ Jira creation failed: {e}"})
                return

        if voice or audio:
            voice_obj = voice or audio
            file_id = voice_obj["file_id"]
            mime_type = voice_obj.get("mime_type", "audio/ogg")
            ext = "ogg" if "ogg" in mime_type else "mp3"
            
            file_bytes = await download_telegram_file(file_id, token, http_client)
            
            # Immediately notify user that we received it
            await http_client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": "🎤 Transcribing voice note..."}
            )
            
            from backend.processing.transcriber import transcribe_audio_via_gemini
            transcript = await transcribe_audio_via_gemini(file_bytes, mime_type)
            
            # Intent detection
            t_lower = transcript.lower().strip()
            is_question = any(t_lower.startswith(q) for q in ["what", "how", "where", "who", "tell me", "when", "why"]) or "?" in t_lower
            
            if is_question:
                await handle_qa_query(transcript, chat_id, token, http_client)
            else:
                from datetime import datetime
                from backend.config import OBSIDIAN_VAULT_PATH
                
                timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                filename = f"{timestamp_str}_voice.{ext}"
                inbox_path = os.path.join(OBSIDIAN_VAULT_PATH, "00_System", "Inbox")
                os.makedirs(inbox_path, exist_ok=True)
                file_path = os.path.join(inbox_path, filename)
                with open(file_path, "wb") as f:
                    f.write(file_bytes)
                    
                transcript_file_path = os.path.join(inbox_path, f"{timestamp_str}_voice.md")
                with open(transcript_file_path, "w", encoding="utf-8") as f:
                    f.write(transcript)
                
                summary = transcript[:50].replace('\n', ' ') + ("..." if len(transcript) > 50 else "")
                captured_md = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks", "Captured.md")
                os.makedirs(os.path.dirname(captured_md), exist_ok=True)
                date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                with open(captured_md, "a", encoding="utf-8") as f:
                    f.write(f"- {summary} (Captured on {date_str} via Telegram Voice) (Source: [[{filename}]])\n")
                
                await http_client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": f"📥 Captured: \"{summary}\"\n   → Saved to Captured.md. Process with /clarify when ready."}
                )
            return

        elif photo:
            photo_obj = photo[-1]
            file_id = photo_obj["file_id"]
            file_bytes = await download_telegram_file(file_id, token, http_client)
            import hashlib
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            file_hash = hashlib.md5(file_id.encode()).hexdigest()[:8]
            filename = f"telegram_photo_{timestamp}_{file_hash}.jpg"
            file_path = os.path.join(staging_path, filename)
            with open(file_path, "wb") as f:
                f.write(file_bytes)
            payload = {"chat_id": chat_id, "type": "photo", "filename": filename, "caption": text}
            from backend.ingestion.queue import add_to_queue
            add_to_queue(source_type="telegram", raw_payload=payload, content_text=text, file_path=file_path)
            await http_client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": "🧠 Image queued. Optical character reader (OCR) will process it."})
            return

        elif document:
            file_id = document["file_id"]
            from backend.utils.sanitize import sanitize_filename
            file_name = sanitize_filename(document.get("file_name", "document.dat"))
            file_bytes = await download_telegram_file(file_id, token, http_client)
            import hashlib
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            file_hash = hashlib.md5(file_id.encode()).hexdigest()[:8]
            unique_filename = f"telegram_doc_{timestamp}_{file_hash}_{file_name}"
            file_path = os.path.join(staging_path, unique_filename)
            with open(file_path, "wb") as f:
                f.write(file_bytes)
            doc_type = "document"
            mime = document.get("mime_type", "")
            if mime == "application/pdf" or file_name.lower().endswith(".pdf"): doc_type = "pdf"
            elif "presentation" in mime or file_name.lower().endswith((".pptx", ".ppt")): doc_type = "pptx"
            payload = {"chat_id": chat_id, "type": doc_type, "filename": file_name, "unique_filename": unique_filename, "caption": text}
            from backend.ingestion.queue import add_to_queue
            add_to_queue(source_type="telegram", raw_payload=payload, content_text=text, file_path=file_path)
            await http_client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": f"🧠 Document '{file_name}' queued for processing."})
            return

        else:
            # Plain text -> Route to RAG Q&A Engine (per requirements)
            if text:
                await handle_qa_query(text, chat_id, token, http_client)
            return

    except Exception as e:
        import traceback
        traceback.print_exc()
        await http_client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": f"❌ Error queueing your request: {e}"})

async def handle_telegram_callback_query(callback_query: dict, token: str, http_client: httpx.AsyncClient):
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    callback_query_id = callback_query["id"]
    data = callback_query.get("data", "")
    
    # 1. Immediately answer the callback query to stop loading spinner in Telegram
    try:
        await http_client.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": "Creating Jira issue..."}
        )
    except Exception as e:
        print(f"Backend [Telegram callback]: Failed to answer callback query: {e}")
        
    if data.startswith("jira:"):
        original_text = callback_query["message"]["text"]
        try:
            item_id = int(data.split(":")[1])
            
            # Fetch the queue item
            from backend.ingestion.queue import get_queue_item
            from backend.config import OBSIDIAN_VAULT_PATH
            import json
            
            item = get_queue_item(item_id)
            if not item:
                raise ValueError(f"Queue item {item_id} not found in database")
                
            raw_payload = json.loads(item["raw_payload"]) if item["raw_payload"] else {}
            processed_title = raw_payload.get("processed_title", "Ingested Task")
            saved_file_path = raw_payload.get("saved_file_path", "")
            
            # Construct description
            desc_parts = [
                f"Source: Ingested via {item['source_type'].title()}"
            ]
            if item["content_text"]:
                desc_parts.append(f"\nOriginal Content:\n{item['content_text'].strip()}")
            if saved_file_path:
                try:
                    rel_path = os.path.relpath(saved_file_path, OBSIDIAN_VAULT_PATH)
                    desc_parts.append(f"\nObsidian Vault Path: [[{rel_path}]]")
                except Exception:
                    pass
            description = "\n".join(desc_parts)
            
            # Notify user we are sending it
            # Edit original message to remove the button and show "Sending..."
            await http_client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"{original_text}\n\n⏳ Sending to Jira backlog..."
                }
            )
            
            # 2. Call Jira API
            from backend.utils.jira import create_jira_issue
            
            issue_key = await create_jira_issue(summary=processed_title, description=description)
            
            # 3. Edit message to indicate success
            await http_client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"{original_text}\n\n📤 **Sent to Jira Backlog**\nTicket: `{issue_key}`"
                }
            )
            
        except Exception as err:
            print(f"Backend [Telegram callback]: Error processing Jira callback: {err}")
            await http_client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"{original_text}\n\n❌ **Jira Send Failed**\nError: `{str(err)}`"
                }
            )

    elif data.startswith("clarify:"):
        from backend.ingestion.handlers.clarify import handle_clarify_callback
        await handle_clarify_callback(callback_query, token, http_client)



async def poll_telegram_unified():
    token = TELEGRAM_BOT_TOKEN
    if not token:
        return

    allowed_chat_ids = TELEGRAM_ALLOWED_CHAT_IDS
    offset_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".telegram_offset")
    offset = 0
    if os.path.exists(offset_file):
        try:
            with open(offset_file, "r") as f:
                offset = int(f.read().strip())
        except Exception:
            pass

    async with httpx.AsyncClient() as client:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        params = {"timeout": 5}
        if offset > 0:
            params["offset"] = offset

        try:
            response = await client.get(url, params=params, timeout=10.0)
            if response.status_code != 200:
                print(f"Backend [Telegram Watcher]: Error getting updates: {response.status_code}")
                return

            data = response.json()
            if not data.get("ok"):
                print(f"Backend [Telegram Watcher]: API returned error: {data}")
                return

            updates = data.get("result", [])
            if updates:
                print(f"Backend [Telegram Watcher]: Fetched {len(updates)} update(s) from Telegram.")
            for update in updates:
                new_offset = update["update_id"] + 1
                if new_offset > offset:
                    offset = new_offset
                    with open(offset_file, "w") as f:
                        f.write(str(offset))

                # Check for callback query first (inline button clicks)
                callback_query = update.get("callback_query")
                if callback_query:
                    chat_id = callback_query["message"]["chat"]["id"]
                    if allowed_chat_ids and chat_id not in allowed_chat_ids:
                        print(f"Backend [Telegram Watcher]: Ignored callback query from unauthorized chat: {chat_id}")
                        continue
                    await handle_telegram_callback_query(callback_query, token, client)
                    continue

                message = update.get("message") or update.get("channel_post")
                if not message:
                    continue

                chat_id = message["chat"]["id"]
                if allowed_chat_ids and chat_id not in allowed_chat_ids:
                    print(f"Backend [Telegram Watcher]: Ignored message from unauthorized chat: {chat_id}")
                    await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": f"Unauthorized chat ID ({chat_id}). Please add it to TELEGRAM_ALLOWED_CHAT_IDS in .env."}
                    )
                    continue

                await process_telegram_message(message, token, client)

        except Exception as e:
            print(f"Backend [Telegram Watcher]: Error polling Telegram: {type(e).__name__} - {e}")


