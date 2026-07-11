import os
import re
from datetime import datetime
import httpx
from backend.config import (
    TELEGRAM_BOT_TOKEN,
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
    response = await http_client.get(file_info_url, params={"file_id": file_id})
    response.raise_for_status()
    file_path = response.json()["result"]["file_path"]
    
    file_download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    download_res = await http_client.get(file_download_url)
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

async def notify_via_telegram(message: str):
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
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message}
            )
    except Exception as e:
        print(f"Backend [Notify]: Failed to send Telegram notification: {e}")

async def process_telegram_message(message: dict, token: str, http_client: httpx.AsyncClient):
    chat_id = message["chat"]["id"]
    text = message.get("text") or message.get("caption") or ""
    
    voice = message.get("voice")
    audio = message.get("audio")
    photo = message.get("photo")
    document = message.get("document")

    await http_client.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "🧠 Centaur Brain: Processing capture request..."}
    )

    try:
        if voice or audio:
            voice_obj = voice or audio
            file_id = voice_obj["file_id"]
            mime_type = voice_obj.get("mime_type", "audio/ogg")
            file_bytes = await download_telegram_file(file_id, token, http_client)
            transcribed_text = await transcribe_audio_via_gemini(file_bytes, mime_type)
            
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            result = await process_capture_core(
                url=f"https://local.brain/voice/{timestamp}",
                title=f"Voice Note {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                markdown_text=transcribed_text
            )
            await log_import_result(result, f"https://local.brain/voice/{timestamp}", chat_id, token, http_client)
            
        elif photo:
            photo_obj = photo[-1]
            file_id = photo_obj["file_id"]
            file_bytes = await download_telegram_file(file_id, token, http_client)
            extracted_text = await analyze_image_via_gemini(file_bytes, text)
            
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            result = await process_capture_core(
                url=f"https://local.brain/photo/{timestamp}",
                title=f"Image Capture {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                markdown_text=extracted_text
            )
            await log_import_result(result, f"https://local.brain/photo/{timestamp}", chat_id, token, http_client)

        elif document and document.get("mime_type") == "application/pdf":
            file_id = document["file_id"]
            file_name = document.get("file_name", "document.pdf")
            file_bytes = await download_telegram_file(file_id, token, http_client)
            
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            result = await process_capture_core(
                url=f"https://local.brain/document/{timestamp}/{file_name}",
                title=file_name.rsplit(".", 1)[0],
                pdf_bytes=file_bytes
            )
            await log_import_result(result, f"https://local.brain/document/{timestamp}/{file_name}", chat_id, token, http_client)

        else:
            urls = re.findall(r'(https?://[^\s)\]\n]+)', text)
            if urls:
                url = urls[0]
                notes = text.replace(url, "").strip()
                result = await process_capture_core(
                    url=url,
                    title="Telegram Link Import",
                    markdown_text=notes
                )
                await log_import_result(result, url, chat_id, token, http_client)
            elif text:
                timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                title_hint = await generate_title_from_text(text)
                result = await process_capture_core(
                    url=f"https://local.brain/thought/{timestamp}",
                    title=title_hint,
                    markdown_text=text
                )
                await log_import_result(result, f"https://local.brain/thought/{timestamp}", chat_id, token, http_client)
            else:
                await http_client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "⚠️ Could not determine the content format. Please send a link, text, photo, voice note, or PDF."}
                )

    except DuplicateURLError as e:
        dup_title = e.existing_title
        await http_client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"ℹ️ Already Ingested: This is a duplicate of [[{dup_title}]]."}
        )
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        completed_entry = f"- [[{dup_title}]] - (Duplicate via Telegram) (Processed on {timestamp})\n"
        with open(COMPLETED_MD_FILE, "a", encoding="utf-8") as f:
            f.write(completed_entry)
            
    except Exception as e:
        print(f"Backend [Telegram Watcher]: Error processing capture: {e}")
        await http_client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"❌ Error processing capture request: {e}"}
        )
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        failed_entry = f"- Telegram Message - Failed on {timestamp}: {e}\n"
        if text:
            indented_text = "\n".join([f"  {line}" for line in text.splitlines()])
            failed_entry += f"  Content:\n{indented_text}\n"
        with open(FAILED_MD_FILE, "a", encoding="utf-8") as f:
            f.write(failed_entry)

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
