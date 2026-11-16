"""HTTP API for the web app's Journal page.

Mounted inside the actions router (``/api/actions/journal``), so every route here sits
behind ``require_api_token`` like the rest of the app's API.

``GET  /api/actions/journal``               days with a journal, newest first, plus today's status
``GET  /api/actions/journal/day/{date}``     one day's entries, parsed from the note
``GET  /api/actions/journal/audio/{name}``   a stored recording
``POST /api/actions/journal``               record an entry (a recording or typed text)

The journal itself is ``feeds.journal.record_entry``, the same function Telegram uses.
"""

from datetime import date, datetime

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend import config
from backend.feeds import journal
from backend.processing import voice_capture

router = APIRouter(prefix="/journal", tags=["journal"])

MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 20_000
_in_progress: set[str] = set()

_AUDIO_TYPES = {"ogg": "audio/ogg", "webm": "audio/webm", "m4a": "audio/mp4", "mp3": "audio/mpeg",
                "wav": "audio/wav", "flac": "audio/flac", "aac": "audio/aac"}


@router.get("")
def journal_index():
    return {"days": journal.list_days(), "today": journal.today_status()}


@router.get("/day/{day}")
def journal_day(day: str):
    try:
        parsed = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=422, detail="Date must be YYYY-MM-DD") from None
    result = journal.read_day(parsed)
    if result is None:
        raise HTTPException(status_code=404, detail="No journal for that day")
    return result


@router.get("/audio/{name}")
def journal_audio(name: str):
    path = journal.audio_file(name)
    if path is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    ext = name.rsplit(".", 1)[-1]
    return FileResponse(path, media_type=_AUDIO_TYPES.get(ext, "application/octet-stream"),
                        headers={"Cache-Control": "private, no-store"})


@router.post("")
async def journal_record(upload_id: str = Form(min_length=1, max_length=64),
                         captured_at: str | None = Form(default=None),
                         text: str | None = Form(default=None),
                         duration_seconds: float | None = Form(default=None),
                         file: UploadFile | None = None):
    """Save a journal entry from the app: a recording, or typed text.

    ``upload_id`` makes a resend harmless: ``record_entry`` saves each ID once, so a phone
    that drops the connection mid-transcription and retries doesn't save the entry twice.
    """
    if not config.ENABLE_JOURNAL_FEED:
        raise HTTPException(status_code=403, detail="The journal feed is switched off")
    if upload_id in _in_progress:
        raise HTTPException(status_code=409, detail="This entry is still being saved")
    when = None
    if captured_at:
        try:
            when = datetime.strptime(captured_at, "%Y-%m-%d %H:%M")
        except ValueError:
            raise HTTPException(status_code=422, detail="captured_at must be YYYY-MM-DD HH:MM") from None

    audio, mime = None, None
    if file is not None:
        if not voice_capture.extension_for(file.content_type):
            raise HTTPException(status_code=415, detail=f"Unsupported audio type: {file.content_type}")
        audio, mime = await file.read(), file.content_type
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Recording too large")
        if not audio:
            raise HTTPException(status_code=422, detail="Empty recording")
    elif not (text or "").strip():
        raise HTTPException(status_code=422, detail="Send a recording or some text")
    elif len(text) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="Entry too long")
    if duration_seconds is not None and not 0 < duration_seconds < 4 * 3600:
        duration_seconds = None

    _in_progress.add(upload_id)
    try:
        result = await journal.record_entry(
            text=None if audio else text, audio=audio, mime_type=mime, duration_seconds=duration_seconds,
            when=when, via="GTD app", entry_id=f"app:{upload_id}")
    except voice_capture.UnreadableAudio as e:
        # Will never succeed, so say so: the app drops the recording instead of retrying.
        print(f"Backend [Journal API]: unreadable recording: {e}")
        raise HTTPException(status_code=422, detail="That recording couldn't be read") from None
    except Exception as e:
        # Worth retrying (transcriber hiccup): the app keeps the entry queued.
        print(f"Backend [Journal API]: entry not saved ({type(e).__name__}).")
        raise HTTPException(status_code=502, detail="The entry couldn't be saved yet") from None
    finally:
        _in_progress.discard(upload_id)
    return {"status": result.status, "message": result.message(), "journal_date": result.journal_date,
            "commitments": len(result.commitments), "today": journal.today_status()}
