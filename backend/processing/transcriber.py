from google.genai import types
from backend.config import GEMINI_MODEL
from backend.utils.gemini import call_gemini_with_retry

async def transcribe_audio_via_gemini(file_bytes: bytes, mime_type: str) -> str:
    prompt = "Transcribe this audio file accurately. If it is a personal voice memo or dictation, preserve all details, clean up filler words (um, ah), and format it nicely in Markdown with bullet points or paragraphs where appropriate. Output only the transcription."
    response = await call_gemini_with_retry(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
            prompt
        ]
    )
    return response.text.strip()
