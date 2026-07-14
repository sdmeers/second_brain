import os
import re
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
        payload = {"chat_id": chat_id, "text": message}
        if reply_markup:
            payload["reply_markup"] = reply_markup
            
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload
            )
    except Exception as e:
        print(f"Backend [Notify]: Failed to send Telegram notification: {e}")

from backend.ingestion.queue import add_to_queue

async def process_telegram_message(message: dict, token: str, http_client: httpx.AsyncClient):
    chat_id = message["chat"]["id"]
    text = message.get("text") or message.get("caption") or ""
    
    voice = message.get("voice")
    audio = message.get("audio")
    photo = message.get("photo")
    document = message.get("document")    # Staging path for incoming Telegram files (private temp folder)
    staging_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "staging")
    os.makedirs(staging_path, exist_ok=True)

    try:
        if text.startswith("/query") or text.startswith("/ask"):
            query_text = re.sub(r'^/(query|ask)\s*', '', text).strip()
            if not query_text:
                await http_client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "Please provide a query, e.g. /query Syncthing IDs"}
                )
                return
            
            await http_client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": "🔍 Searching your Second Brain..."}
            )
            
            from backend.rag.chat import query_brain
            result = await query_brain(query_text)
            answer = result["answer"]
            
            await http_client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": answer}
            )
            return

        if text.startswith("/jira"):
            jira_text = re.sub(r'^/jira\s*', '', text).strip()
            if not jira_text:
                await http_client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "❓ Please provide a task description, e.g. `/jira Fix the TF RAID configuration`"}
                )
                return
                
            await http_client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": "⏳ Creating Jira issue..."}
            )
            
            try:
                # Clean up and split lines to use the first line as summary
                lines = [line.strip() for line in jira_text.splitlines() if line.strip()]
                if not lines:
                    raise ValueError("Task description is empty")
                
                summary = lines[0]
                if len(summary) > 80:
                    summary = summary[:77] + "..."
                
                from backend.utils.jira import create_jira_issue
                issue_key = await create_jira_issue(
                    summary=summary,
                    description=jira_text
                )
                
                await http_client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": f"✅ Created Jira backlog issue: `{issue_key}`"}
                )
            except Exception as e:
                print(f"Backend [Telegram /jira]: Failed to create Jira issue: {e}")
                await http_client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": f"❌ Jira creation failed: {e}"}
                )
            return

        if voice or audio:
            voice_obj = voice or audio
            file_id = voice_obj["file_id"]
            mime_type = voice_obj.get("mime_type", "audio/ogg")
            ext = "ogg" if "ogg" in mime_type else "mp3"
            
            file_bytes = await download_telegram_file(file_id, token, http_client)
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"telegram_voice_{timestamp}.{ext}"
            file_path = os.path.join(staging_path, filename)
            
            with open(file_path, "wb") as f:
                f.write(file_bytes)
                
            payload = {
                "chat_id": chat_id,
                "type": "voice",
                "mime_type": mime_type,
                "filename": filename
            }
            add_to_queue(source_type="telegram", raw_payload=payload, file_path=file_path)
            
            await http_client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": "🧠 Voice note queued. Local transcriber will process it shortly."}
            )
            
        elif photo:
            photo_obj = photo[-1]
            file_id = photo_obj["file_id"]
            file_bytes = await download_telegram_file(file_id, token, http_client)
            
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"telegram_photo_{timestamp}.jpg"
            file_path = os.path.join(staging_path, filename)
            
            with open(file_path, "wb") as f:
                f.write(file_bytes)
                
            payload = {
                "chat_id": chat_id,
                "type": "photo",
                "filename": filename,
                "caption": text
            }
            add_to_queue(source_type="telegram", raw_payload=payload, content_text=text, file_path=file_path)
            
            await http_client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": "🧠 Image queued. Optical character reader (OCR) will process it."}
            )
 
        elif document:
            file_id = document["file_id"]
            file_name = sanitize_filename(document.get("file_name", "document.dat"))
            file_bytes = await download_telegram_file(file_id, token, http_client)
            
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            unique_filename = f"telegram_doc_{timestamp}_{file_name}"
            file_path = os.path.join(staging_path, unique_filename)
            
            with open(file_path, "wb") as f:
                f.write(file_bytes)
                
            doc_type = "document"
            mime = document.get("mime_type", "")
            if mime == "application/pdf" or file_name.lower().endswith(".pdf"):
                doc_type = "pdf"
            elif "presentation" in mime or file_name.lower().endswith((".pptx", ".ppt")):
                doc_type = "pptx"
                
            payload = {
                "chat_id": chat_id,
                "type": doc_type,
                "filename": file_name,
                "unique_filename": unique_filename,
                "caption": text
            }
            add_to_queue(source_type="telegram", raw_payload=payload, content_text=text, file_path=file_path)
            
            await http_client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": f"🧠 Document '{file_name}' queued for processing."}
            )

        else:
            # Handle standard text or links
            payload = {
                "chat_id": chat_id,
                "type": "text"
            }
            add_to_queue(source_type="telegram", raw_payload=payload, content_text=text)
            
            await http_client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": "🧠 Message received and queued."}
            )

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Backend [Telegram Ingest]: Error queueing capture: {e}")
        await http_client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"❌ Error queueing your request: {e}"}
        )


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


async def poll_telegram():
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


async def process_telegram_message_qa(message: dict, token: str, http_client: httpx.AsyncClient):
    chat_id = message["chat"]["id"]
    text = message.get("text") or message.get("caption") or ""
    
    if not text.strip():
        await http_client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "❓ Please send a text question to query your Second Brain."}
        )
        return
        
    await http_client.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "🔍 Searching your Second Brain..."}
    )
    
    try:
        from backend.rag.chat import query_brain
        result = await query_brain(text.strip())
        answer = result["answer"]
        
        await http_client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": answer}
        )
    except Exception as e:
        print(f"Backend [Telegram Q&A]: Error answering query: {e}")
        await http_client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"❌ Error retrieving answer: {e}"}
        )


async def poll_telegram_qa():
    token = TELEGRAM_QA_BOT_TOKEN
    if not token:
        return

    allowed_chat_ids = TELEGRAM_ALLOWED_CHAT_IDS
    offset_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".telegram_qa_offset")
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
                print(f"Backend [Telegram Q&A Watcher]: Error getting updates: {response.status_code}")
                return

            data = response.json()
            if not data.get("ok"):
                print(f"Backend [Telegram Q&A Watcher]: API returned error: {data}")
                return

            updates = data.get("result", [])
            if updates:
                print(f"Backend [Telegram Q&A Watcher]: Fetched {len(updates)} update(s) from Telegram Q&A.")
            for update in updates:
                new_offset = update["update_id"] + 1
                if new_offset > offset:
                    offset = new_offset
                    with open(offset_file, "w") as f:
                        f.write(str(offset))

                message = update.get("message") or update.get("channel_post")
                if not message:
                    continue

                chat_id = message["chat"]["id"]
                if allowed_chat_ids and chat_id not in allowed_chat_ids:
                    print(f"Backend [Telegram Q&A Watcher]: Ignored message from unauthorized chat: {chat_id}")
                    await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": f"Unauthorized chat ID ({chat_id}). Please add it to TELEGRAM_ALLOWED_CHAT_IDS in .env."}
                    )
                    continue

                await process_telegram_message_qa(message, token, client)

        except Exception as e:
            print(f"Backend [Telegram Q&A Watcher]: Error polling Telegram Q&A: {type(e).__name__} - {e}")
