import os
import re
import imaplib
import email
from email.header import decode_header
import asyncio
from datetime import datetime
from backend.config import GMAIL_EMAIL, GMAIL_APP_PASSWORD, COMPLETED_MD_FILE, FAILED_MD_FILE
from backend.utils.sanitize import sanitize_filename
from backend.processing.capture import process_capture_core, DuplicateURLError
from backend.ingestion.telegram import notify_via_telegram, generate_title_from_text

async def _ingest_email(subject: str, body: str, attachments: list):
    print(f"Backend [Gmail Watcher]: Processing unread email: '{subject}'")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        pdf_attachment = None
        for att in attachments:
            if att["content_type"] == "application/pdf" or att["filename"].lower().endswith(".pdf"):
                pdf_attachment = att
                break
                
        if pdf_attachment:
            safe_filename = sanitize_filename(pdf_attachment["filename"].rsplit(".", 1)[0])
            result = await process_capture_core(
                url=f"https://local.brain/email-attachment/{safe_filename}",
                title=safe_filename,
                pdf_bytes=pdf_attachment["bytes"]
            )
            safe_title = result.get("title", "Untitled")
            completed_entry = f"- [[{safe_title}]] - Email Attachment: {pdf_attachment['filename']} (Processed on {timestamp})\n"
            with open(COMPLETED_MD_FILE, "a", encoding="utf-8") as f:
                f.write(completed_entry)
            print(f"Backend [Gmail Watcher]: Successfully processed email attachment: [[{safe_title}]]")
            await notify_via_telegram(f"📧✅ Gmail Import: [[{safe_title}]]")
            
        else:
            urls = re.findall(r'(https?://[^\s)\]\n]+)', f"{subject}\n{body}")
            if urls:
                for url in urls:
                    try:
                        notes = body.replace(url, "").strip()
                        result = await process_capture_core(
                            url=url,
                            title=subject or "Email Import",
                            markdown_text=notes
                        )
                        safe_title = result.get("title", "Untitled")
                        completed_entry = f"- [[{safe_title}]] - {url} (Processed from email on {timestamp})\n"
                        with open(COMPLETED_MD_FILE, "a", encoding="utf-8") as f:
                            f.write(completed_entry)
                        print(f"Backend [Gmail Watcher]: Successfully processed url from email: [[{safe_title}]]")
                        await notify_via_telegram(f"📧✅ Gmail Import: [[{safe_title}]]")
                    except DuplicateURLError as e:
                        completed_entry = f"- [[{e.existing_title}]] - {url} (Duplicate from email on {timestamp})\n"
                        with open(COMPLETED_MD_FILE, "a", encoding="utf-8") as f:
                            f.write(completed_entry)
            elif body.strip():
                timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                title_hint = subject if subject.strip() else await generate_title_from_text(body)
                result = await process_capture_core(
                    url=f"https://local.brain/email/{timestamp_str}",
                    title=title_hint,
                    markdown_text=body
                )
                safe_title = result.get("title", "Untitled")
                completed_entry = f"- [[{safe_title}]] - Email Body: {title_hint} (Processed on {timestamp})\n"
                with open(COMPLETED_MD_FILE, "a", encoding="utf-8") as f:
                    f.write(completed_entry)
                print(f"Backend [Gmail Watcher]: Successfully processed email body: [[{safe_title}]]")
                await notify_via_telegram(f"📧✅ Gmail Import: [[{safe_title}]]")

    except DuplicateURLError as e:
        completed_entry = f"- [[{e.existing_title}]] - Email duplicate on {timestamp}\n"
        with open(COMPLETED_MD_FILE, "a", encoding="utf-8") as f:
            f.write(completed_entry)
            
    except Exception as e:
        print(f"Backend [Gmail Watcher]: Error ingesting email '{subject}': {e}")
        failed_entry = f"- Email: '{subject}' - Failed on {timestamp}: {e}\n"
        if body:
            indented_body = "\n".join([f"  {line}" for line in body.splitlines()])
            failed_entry += f"  Body:\n{indented_body}\n"
        with open(FAILED_MD_FILE, "a", encoding="utf-8") as f:
            f.write(failed_entry)
        await notify_via_telegram(f"📧❌ Gmail Import Failed: '{subject}'")

def _process_gmail_emails(gmail_email, gmail_app_password, loop):
    email_user = gmail_email.split('@')[0] if '@' in gmail_email else "stevenmeers"
    BRAIN_TAG = f"{email_user}+brain"
    
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(gmail_email, gmail_app_password)
    
    status, _ = mail.select("CentaurBrain")
    if status != 'OK':
        status, _ = mail.select("INBOX")
        if status != 'OK':
            mail.logout()
            return
    
    status, messages = mail.search(None, f'(UNSEEN TO "{BRAIN_TAG}")')
    if status != 'OK' or not messages[0]:
        mail.logout()
        return
            
    mail_ids = messages[0].split()
    print(f"Backend [Gmail Watcher]: Found {len(mail_ids)} unread email(s) matching '{BRAIN_TAG}'.")
    
    for mail_id in mail_ids:
        try:
            status, data = mail.fetch(mail_id, '(RFC822)')
            if status != 'OK':
                continue
            
            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            headers_to_check = []
            for header_name in ["To", "Delivered-To", "Envelope-To", "X-Original-To", "Cc"]:
                header_val = msg.get(header_name, "")
                if header_val:
                    headers_to_check.append(header_val.lower())
            
            combined_headers = " ".join(headers_to_check)
            if "+brain" not in combined_headers:
                print(f"Backend [Gmail Watcher]: Skipping email (no +brain in headers): {msg.get('Subject', '(no subject)')}")
                continue
            
            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8", errors="ignore")
            
            body = ""
            attachments = []
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    
                    if content_type == "text/plain" and "attachment" not in content_disposition:
                        payload = part.get_payload(decode=True)
                        body += payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
                    elif "attachment" in content_disposition:
                        file_name = part.get_filename()
                        if file_name:
                            attachments.append({
                                "filename": file_name,
                                "content_type": content_type,
                                "bytes": part.get_payload(decode=True)
                            })
            else:
                content_type = msg.get_content_type()
                if content_type == "text/plain":
                    payload = msg.get_payload(decode=True)
                    body = payload.decode(msg.get_content_charset() or "utf-8", errors="ignore")
            
            asyncio.run_coroutine_threadsafe(
                _ingest_email(subject, body, attachments),
                loop
            )
            
            mail.store(mail_id, '+FLAGS', '\\Seen')
            
        except Exception as e:
            print(f"Backend [Gmail Watcher]: Error processing email ID {mail_id}: {e}")
            
    mail.logout()

async def poll_gmail():
    gmail_email = GMAIL_EMAIL
    gmail_app_password = GMAIL_APP_PASSWORD
    if not gmail_email or not gmail_app_password:
        return

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _process_gmail_emails, gmail_email, gmail_app_password, loop)
    except Exception as e:
        print(f"Backend [Gmail Watcher]: Error polling Gmail: {e}")
