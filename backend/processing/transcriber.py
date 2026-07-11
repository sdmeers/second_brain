import os
import asyncio
from google.genai import types
from backend.config import GEMINI_MODEL
from backend.utils.gemini import call_gemini_with_retry

# Global model cache to avoid loading from disk on every transcription
_whisper_model = None
_model_initialized = False

def get_whisper_model():
    """Lazily initializes and caches the faster-whisper model on GPU (CUDA) or CPU fallback."""
    global _whisper_model, _model_initialized
    if _model_initialized:
        return _whisper_model
        
    from faster_whisper import WhisperModel
    
    # Cascade through GPU compute types to find one supported by the hardware (e.g. float32/int8 for GTX 1070 Pascal)
    for compute_type in ["float16", "float32", "int8"]:
        try:
            print(f"RAG [Transcriber]: Trying to initialize local Whisper ('large-v3') on CUDA ({compute_type})...")
            _whisper_model = WhisperModel("large-v3", device="cuda", compute_type=compute_type)
            print(f"RAG [Transcriber]: Local Whisper model initialized on CUDA ({compute_type}) successfully.")
            _model_initialized = True
            return _whisper_model
        except Exception as e:
            print(f"RAG [Transcriber]: CUDA ({compute_type}) initialization failed: {e}")
            
    # CPU Fallback
    try:
        print("RAG [Transcriber]: GPU initialization failed completely. Trying CPU fallback with int8...")
        _whisper_model = WhisperModel("large-v3", device="cpu", compute_type="int8")
        print("RAG [Transcriber]: Local Whisper model initialized on CPU successfully.")
    except Exception as cpu_e:
        print(f"RAG [Transcriber]: Local CPU initialization failed ({cpu_e}). Whisper is disabled.")
        _whisper_model = None
            
    _model_initialized = True
    return _whisper_model


async def transcribe_audio_via_gemini(file_bytes: bytes, mime_type: str) -> str:
    """Fallback transcription calling the Gemini API directly."""
    print("RAG [Transcriber]: Calling Gemini API for audio transcription...")
    prompt = "Transcribe this audio file accurately. If it is a personal voice memo or dictation, preserve all details, clean up filler words (um, ah), and format it nicely. Output only the transcription."
    response = await call_gemini_with_retry(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
            prompt
        ]
    )
    return response.text.strip()

async def transcribe_audio_local(file_path: str) -> str:
    """Transcribes an audio file locally using faster-whisper, falling back to Gemini if unavailable."""
    model = get_whisper_model()
    
    if model is None:
        print("RAG [Transcriber]: Local Whisper model not available. Falling back to Gemini API...")
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            ext = file_path.rsplit(".", 1)[-1].lower()
            mime_type = "audio/ogg" if ext == "ogg" else "audio/mpeg"
            return await transcribe_audio_via_gemini(file_bytes, mime_type)
        except Exception as e:
            print(f"RAG [Transcriber]: Gemini fallback failed: {e}")
            raise e
            
    print(f"RAG [Transcriber]: Running local Whisper transcription on {os.path.basename(file_path)}...")
    
    # WhisperModel.transcribe is synchronous and block-heavy. We offload it to a thread
    # using asyncio.to_thread to keep the event loop responsive.
    def run_inference():
        # beam_size=5 is standard for good accuracy
        segments, info = model.transcribe(file_path, beam_size=5)
        text_segments = [seg.text for seg in segments]
        return " ".join(text_segments).strip()
        
    try:
        transcription = await asyncio.to_thread(run_inference)
        print(f"RAG [Transcriber]: Local transcription finished ({len(transcription)} chars).")
        return transcription
    except Exception as e:
        print(f"RAG [Transcriber]: Local transcription failed: {e}. Trying Gemini fallback...")
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        ext = file_path.rsplit(".", 1)[-1].lower()
        mime_type = "audio/ogg" if ext == "ogg" else "audio/mpeg"
        return await transcribe_audio_via_gemini(file_bytes, mime_type)
