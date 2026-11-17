"""Text from the file types the household feed reads, extracted locally (no model, no network).

One function per file type, chosen by extension. Adding a type means adding a reader
here and its extension in ``household_scan.READABLE`` (the laptop side). Every reader
returns plain text, or "" when nothing could be read; none raises.
"""

import csv
import email
import email.policy
import html
import io
import os
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

MAX_SHEET_ROWS = 400


@dataclass
class Read:
    text: str
    # Two copies of the same email (an .eml and an Outlook .msg, say) share this key.
    same_as: str = ""


def html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup)).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def _document(name: str, data: bytes) -> str:
    from backend.processing.extractor import extract_docx_text_from_bytes, extract_pdf_text_from_bytes
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf":
        text = extract_pdf_text_from_bytes(data)
    elif ext == ".docx":
        text = extract_docx_text_from_bytes(data)
    else:
        return ""
    return "" if text.startswith("Error extracting") else text.strip()


def _email_key(subject: str, sent: str) -> str:
    """Subject plus the sending day and time, so an .eml and an .msg of one email match."""
    from email.utils import parsedate_to_datetime
    try:
        sent = parsedate_to_datetime(sent).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, IndexError):
        sent = ""
    subject = re.sub(r"\s+", " ", subject or "").strip().lower()
    return f"email:{subject}|{sent}" if subject and sent else ""


def _email_text(sender: str, to: str, sent: str, subject: str, body: str, attachments: list[tuple[str, str]]) -> str:
    lines = [f"From: {sender}", f"To: {to}", f"Date: {sent}", f"Subject: {subject}", "", body.strip()]
    for name, text in attachments:
        lines += ["", f"--- Attachment: {name} ---", text or "(not read)"]
    return "\n".join(lines).strip()


def read_eml(data: bytes) -> Read:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        body = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            body = html_to_text(body)
    attachments = []
    for part in msg.iter_attachments():
        name = part.get_filename() or "unnamed"
        try:
            attachments.append((name, _document(name, part.get_payload(decode=True) or b"")))
        except Exception:
            attachments.append((name, ""))
    subject, sent = str(msg.get("subject", "")), str(msg.get("date", ""))
    return Read(_email_text(str(msg.get("from", "")), str(msg.get("to", "")), sent, subject, body, attachments),
                _email_key(subject, sent))


def read_msg(data: bytes) -> Read:
    """An Outlook .msg file: an OLE container of MAPI properties."""
    import olefile
    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        def prop(tag: str, root: str = "") -> str:
            for kind, codec in (("001F", "utf-16-le"), ("001E", "cp1252")):
                stream = f"{root}__substg1.0_{tag}{kind}"
                if ole.exists(stream):
                    return ole.openstream(stream).read().decode(codec, errors="replace").rstrip("\x00")
            return ""

        headers = email.message_from_string(prop("007D"), policy=email.policy.default) if prop("007D") else None
        sent = str(headers.get("date", "")) if headers else ""
        body = prop("1000")
        if not body.strip() and ole.exists("__substg1.0_10130102"):
            body = html_to_text(ole.openstream("__substg1.0_10130102").read().decode("utf-8", errors="replace"))
        attachments = []
        roots = sorted({e[0] for e in ole.listdir() if e and e[0].startswith("__attach_version1.0_")})
        for root in roots:
            name = prop("3707", root + "/") or prop("3704", root + "/") or "unnamed"
            stream = f"{root}/__substg1.0_37010102"
            text = ""
            if ole.exists(stream):
                try:
                    text = _document(name, ole.openstream(stream).read())
                except Exception:
                    text = ""
            attachments.append((name, text))
        subject = prop("0037")
        sender = prop("0C1A") + (f" <{prop('5D01') or prop('0C1F')}>" if prop("5D01") or prop("0C1F") else "")
    return Read(_email_text(sender.strip(), prop("0E04"), sent, subject, body, attachments), _email_key(subject, sent))


def _decode_text(data: bytes) -> str:
    for codec in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_url(data: bytes) -> Read:
    """A Windows .url shortcut: just the address it points at."""
    m = re.search(r"(?im)^URL=(.+)$", _decode_text(data))
    return Read(f"Link: {m.group(1).strip()}" if m else "")


def _rows_text(rows) -> str:
    out = []
    for n, row in enumerate(rows):
        if n >= MAX_SHEET_ROWS:
            out.append(f"(further rows not read; the sheet is cut at {MAX_SHEET_ROWS})")
            break
        cells = [c.strip() for c in row]
        if any(cells):
            out.append(" | ".join(cells))
    return "\n".join(out)


def read_csv(data: bytes) -> Read:
    return Read(_rows_text(csv.reader(io.StringIO(_decode_text(data)))))


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(data: bytes) -> Read:
    """Cell values (as last calculated) of every sheet, from the workbook's XML."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels")):
                rels[rel.get("Id")] = rel.get("Target", "").lstrip("/").removeprefix("xl/")
        parts = []
        for sheet in ElementTree.fromstring(z.read("xl/workbook.xml")).iter(f"{{{_NS['m']}}}sheet"):
            target = "xl/" + rels.get(sheet.get(f"{{{_NS['r']}}}id"), "")
            if target not in names:
                continue
            rows = []
            for row in ElementTree.fromstring(z.read(target)).iter(f"{{{_NS['m']}}}row"):
                cells: dict[int, str] = {}
                for c in row.findall("m:c", _NS):
                    kind, v = c.get("t"), c.find("m:v", _NS)
                    if kind == "inlineStr":
                        value = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
                    elif v is None or v.text is None:
                        continue
                    elif kind == "s":
                        value = shared[int(v.text)] if int(v.text) < len(shared) else ""
                    else:
                        value = v.text
                    cells[_col(c.get("r", "A"))] = value
                if cells:
                    rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
            body = _rows_text(rows)
            if body:
                parts.append(f"Sheet: {sheet.get('name', '')}\n{body}")
    return Read("\n\n".join(parts))


def read_file(path: str) -> Read:
    """The text of one staged file, by its extension. Empty when it can't be read."""
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as f:
            data = f.read()
        if ext in (".pdf", ".docx"):
            return Read(_document(path, data))
        if ext == ".eml":
            return read_eml(data)
        if ext == ".msg":
            return read_msg(data)
        if ext in (".txt", ".md"):
            return Read(_decode_text(data).strip())
        if ext == ".url":
            return read_url(data)
        if ext == ".csv":
            return read_csv(data)
        if ext == ".xlsx":
            return read_xlsx(data)
    except Exception as e:
        print(f"Backend [Household]: could not read a {ext} file ({type(e).__name__}).")
    return Read("")


def readable_enough(path: str, text: str) -> bool:
    """A PDF or Word file with almost no text is a scan; other types may be legitimately short."""
    if os.path.splitext(path)[1].lower() in (".pdf", ".docx"):
        return len(text) >= 200
    return bool(text.strip())
