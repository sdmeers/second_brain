import os
import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr
import asyncio
from datetime import datetime
from backend.config import GMAIL_EMAIL, GMAIL_APP_PASSWORD, GMAIL_ALLOWED_SENDERS
from backend.utils.sanitize import sanitize_filename
from backend.ingestion.telegram import notify_via_telegram

from backend.ingestion.queue import add_to_queue

def sender_allowed(msg, allowed: list[str]) -> tuple[bool, str]:
    """Whether a +brain message comes from an address on the allow-list.

    Returns ``(ok, sender)``. An empty list accepts everything. A From line is easy to
    forge, so a message Gmail itself marked as failing DMARC is refused even when the
    address matches. Only Gmail's own verdict counts: a sender can add a fake header but
    cannot remove Google's.
    """
    sender = parseaddr(str(msg.get("From", "")))[1].strip().lower()
    if not allowed:
        return True, sender
    if sender not in allowed:
        return False, sender
    for verdict in msg.get_all("Authentication-Results", []):
        verdict = str(verdict).lower()
        if "mx.google.com" in verdict and "dmarc=fail" in verdict:
            return False, sender
    return True, sender


async def _ingest_email(subject: str, body: str, attachments: list):
    print(f"Backend [Gmail Watcher]: Queuing email: '{subject}'")
    
    # Stage attachments outside the vault. They used to be written into INBOX_PATH, which
    # the drop-folder scanner also watches, so each one was ingested a second time as a
    # file_drop. The queue worker deletes staged files once processed.
    from backend.config import STAGING_PATH
    os.makedirs(STAGING_PATH, exist_ok=True)

    try:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        saved_attachments = []
        for index, att in enumerate(attachments):
            filename = sanitize_filename(att["filename"])
            unique_filename = f"email_{timestamp}_{index}_{filename}"
            file_path = os.path.join(STAGING_PATH, unique_filename)

            with open(file_path, "wb") as f:
                f.write(att["bytes"])

            saved_attachments.append({
                "filename": filename,
                "content_type": att["content_type"],
                "file_path": file_path
            })

        # One queue item per attachment (only the first was ever processed before; the
        # rest were picked up by accident via the inbox scanner). Bodies with no
        # attachment are queued as text.
        for att in saved_attachments or [None]:
            payload = {"subject": subject}
            if att:
                # The body rides in the payload: the worker treats content_text on a
                # document item as already-extracted text and would skip the PDF.
                payload.update(filename=att["filename"], content_type=att["content_type"], body=body)
            add_to_queue(
                source_type="email",
                raw_payload=payload,
                content_text="" if att else body,
                file_path=att["file_path"] if att else None
            )
        
        print(f"Backend [Gmail Watcher]: Successfully queued email '{subject}'")
        await notify_via_telegram(f"📧 Inbound Email Queued: '{subject}'")
        
    except Exception as e:
        print(f"Backend [Gmail Watcher]: Error queuing email '{subject}': {e}")
        await notify_via_telegram(f"📧❌ Gmail Queueing Failed: '{subject}' ({e})")

def _process_gmail_emails(gmail_email, gmail_app_password, loop):
    email_user = gmail_email.split('@')[0] if '@' in gmail_email else "user"
    BRAIN_TAG = f"{email_user}+brain"
    
    mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=15.0)
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
            
            ok, sender = sender_allowed(msg, GMAIL_ALLOWED_SENDERS)
            if not ok:
                # Marked read so it is not fetched again every poll; nothing is ingested.
                print("Backend [Gmail Watcher]: Ignored a +brain email from a sender not on the allow-list.")
                mail.store(mail_id, '+FLAGS', '\\Seen')
                asyncio.run_coroutine_threadsafe(notify_via_telegram(
                    f"📧🚫 Ignored an email to +brain from {sender or 'an unknown sender'}: not on the "
                    "allow-list (GMAIL_ALLOWED_SENDERS in backend/.env)."), loop)
                continue

            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8", errors="ignore")
            
            body = ""
            html_body = ""
            attachments = []
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    
                    if content_type == "text/plain" and "attachment" not in content_disposition:
                        payload = part.get_payload(decode=True)
                        body += payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
                    elif content_type == "text/html" and "attachment" not in content_disposition:
                        payload = part.get_payload(decode=True)
                        html_body += payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
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
                elif content_type == "text/html":
                    payload = msg.get_payload(decode=True)
                    html_body = payload.decode(msg.get_content_charset() or "utf-8", errors="ignore")
            
            # Use HTML fallback if plain text body is empty
            if not body.strip() and html_body.strip():
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html_body, "html.parser")
                    # Clean scripts and styles
                    for script in soup(["script", "style"]):
                        script.decompose()
                    body = soup.get_text(separator="\n").strip()
                except Exception as e:
                    print(f"Backend [Gmail Watcher]: Error converting HTML to text: {e}")
                    body = html_body
            
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
        await asyncio.wait_for(
            loop.run_in_executor(None, _process_gmail_emails, gmail_email, gmail_app_password, loop),
            timeout=20.0
        )
    except TimeoutError:
        print("Backend [Gmail Watcher]: Gmail polling timed out after 20s.")
    except Exception as e:
        print(f"Backend [Gmail Watcher]: Error polling Gmail: {e}")
