"""Turning a voice recording into a captured item.

Shared by the Telegram bot (voice notes) and the GTD web app (the mic button), so both
produce the same thing: the audio kept in the inbox as ``<timestamp>_voice.<ext>`` and
its transcript as a line in ``Captured.md`` linking back to it.

Transcription goes through Gemini, as Telegram voice notes always have. Telegram sends
OGG; browsers record WebM/Opus (Chrome, Android) or MP4/AAC (Safari, iPhone), neither of
which Gemini lists as supported, so those are decoded to WAV first.
"""

import asyncio
import io
import os
import wave
from datetime import datetime

from backend.config import INBOX_PATH
from backend.vault import clarify_ops

# Formats Gemini accepts as they are -> the mime type to declare.
_NATIVE = {
    "audio/ogg": "audio/ogg",
    "audio/opus": "audio/ogg",
    "audio/mpeg": "audio/mpeg",
    "audio/mp3": "audio/mpeg",
    "audio/wav": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/flac": "audio/flac",
}
_EXTENSIONS = {
    "audio/ogg": "ogg", "audio/opus": "ogg", "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/flac": "flac",
    "audio/webm": "webm", "video/webm": "webm",
    "audio/mp4": "m4a", "video/mp4": "m4a", "audio/x-m4a": "m4a", "audio/m4a": "m4a", "audio/aac": "aac",
}


def base_mime(mime_type: str | None) -> str:
    """``audio/webm;codecs=opus`` -> ``audio/webm``."""
    return (mime_type or "").split(";")[0].strip().lower()


def extension_for(mime_type: str | None) -> str | None:
    """File extension for a recording, or None if it isn't an audio type we handle."""
    return _EXTENSIONS.get(base_mime(mime_type))


def to_wav(file_bytes: bytes) -> bytes:
    """Decode any audio container PyAV understands to 16 kHz mono WAV."""
    import numpy as np
    from faster_whisper.audio import decode_audio

    samples = decode_audio(io.BytesIO(file_bytes), sampling_rate=16000)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    return out.getvalue()


async def transcribe(file_bytes: bytes, mime_type: str | None) -> str:
    """Transcript of a recording, converting it first if Gemini wouldn't take it."""
    from backend.processing.transcriber import transcribe_audio_via_gemini

    mime = base_mime(mime_type)
    if mime in _NATIVE:
        return await transcribe_audio_via_gemini(file_bytes, _NATIVE[mime])
    wav = await asyncio.to_thread(to_wav, file_bytes)
    return await transcribe_audio_via_gemini(wav, "audio/wav")


def save_voice_capture(file_bytes: bytes, ext: str, transcript: str, via: str,
                       when: datetime | None = None) -> str:
    """Keep the recording in the inbox and add its transcript to ``Captured.md``.

    Returns the captured line. The transcript lives inline in that line; the audio is
    only there to listen back to, and is archived when the item is clarified.
    """
    when = when or datetime.now()
    os.makedirs(INBOX_PATH, exist_ok=True)
    filename = f"{when.strftime('%Y-%m-%d_%H-%M-%S')}_voice.{ext}"
    n = 1
    while os.path.exists(os.path.join(INBOX_PATH, filename)):
        n += 1
        filename = f"{when.strftime('%Y-%m-%d_%H-%M-%S')}-{n}_voice.{ext}"
    with open(os.path.join(INBOX_PATH, filename), "wb") as f:
        f.write(file_bytes)
    return clarify_ops.add_capture(transcript.replace("\n", " "), when, via=via, source=filename)
