import io
import re
import asyncio
from functools import partial
import httpx
import fitz  # PyMuPDF
import yt_dlp
from bs4 import BeautifulSoup
from google.genai import types
from backend.config import GEMINI_MODEL
from backend.utils.gemini import call_gemini_with_retry

def extract_pdf_text(url: str) -> tuple[str, bytes]:
    """Downloads PDF and extracts text."""
    headers = {"User-Agent": "Mozilla/5.0"}
    response = httpx.get(url, follow_redirects=True, timeout=30.0, headers=headers)
    response.raise_for_status()
    
    pdf_bytes = response.content
    pdf_stream = io.BytesIO(pdf_bytes)
    doc = fitz.open(stream=pdf_stream, filetype="pdf")
    
    text = ""
    for page in doc:
        text += page.get_text()
        
    return text.strip(), pdf_bytes

def extract_pdf_text_from_bytes(pdf_bytes: bytes) -> str:
    pdf_stream = io.BytesIO(pdf_bytes)
    doc = fitz.open(stream=pdf_stream, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text()
    return text.strip()

async def extract_youtube_transcript(url: str) -> str:
    """Uses yt-dlp to extract the transcript from a YouTube video."""
    print(f"Backend [Transcript]: Starting for {url}")
    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 10,
    }

    try:
        print("Backend [Transcript]: Extracting video info (yt-dlp)...")
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, partial(ydl.extract_info, url, download=False))
        print("Backend [Transcript]: Info extraction complete.")
        
        all_subs = info.get('subtitles', {})
        all_auto_subs = info.get('automatic_captions', {})
        target_url = None
        
        candidates = []
        # Gather all English candidates from manual and auto
        for sub_type, sub_dict in [("manual", all_subs), ("auto", all_auto_subs)]:
            for lang_code, formats in sub_dict.items():
                if lang_code.startswith('en'):
                    for f in formats:
                        if f.get('ext') == 'json3':
                            candidates.append({
                                "type": sub_type,
                                "code": lang_code,
                                "url": f.get('url')
                            })
        
        def score_lang(c):
            score = 100 if c['type'] == 'manual' else 0
            if c['code'] in ['en', 'en-orig']: score += 50
            elif c['code'].startswith('en-'): score += 25
            return score

        if candidates:
            best = max(candidates, key=score_lang)
            target_url = best['url']
            print(f"Backend [Transcript]: Selected {best['type']} track ({best['code']})")
        
        if not target_url:
            print("Backend [Transcript]: No English JSON3 transcript URL found.")
            raise ValueError("No English transcript found for this video.")
        
        print(f"Backend [Transcript]: Fetching transcript content from YouTube API...")
        async with httpx.AsyncClient() as client:
            res = await client.get(target_url, timeout=30.0)
            res.raise_for_status()
            data = res.json()
            
        print("Backend [Transcript]: Parsing JSON3 format...")
        text_parts = []
        for event in data.get('events', []):
            if 'segs' in event:
                for seg in event['segs']:
                    text_parts.append(seg.get('utf8', ''))
        
        transcript = " ".join(text_parts).replace("\n", " ").strip()
        result = re.sub(r'\s+', ' ', transcript)
        print(f"Backend [Transcript]: Success! Extracted {len(result)} characters.")
        return result
                
    except Exception as e:
        print(f"Backend [Transcript]: ERROR: {e}")
        raise ValueError(f"Failed to extract transcript: {str(e)}")

def extract_web_text(url: str) -> str:
    """Fetches a URL and extracts readable text using BeautifulSoup."""
    headers = {"User-Agent": "Mozilla/5.0"}
    response = httpx.get(url, follow_redirects=True, timeout=30.0, headers=headers)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    for script in soup(["script", "style"]):
        script.extract()
        
    text = soup.get_text()
    
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = '\n'.join(chunk for chunk in chunks if chunk)
    
    return text.strip()

def fetch_cover(url: str, is_youtube: bool) -> str:
    """Attempts to find a suitable cover image URL."""
    if is_youtube:
        video_id = None
        if "v=" in url:
            video_id = url.split("v=")[1].split("&")[0]
        elif "youtu.be/" in url:
            video_id = url.split("youtu.be/")[1].split("?")[0]
        if video_id:
            return f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
    
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        with httpx.Client(follow_redirects=True, timeout=10.0, headers=headers) as c:
            r = c.get(url)
            if r.status_code == 200:
                match = re.search(r'<meta [^>]*property=["\']og:image["\'][^>]*content=["\'](.*?)["\']', r.text)
                if not match:
                    match = re.search(r'<meta [^>]*content=["\'](.*?)["\'][^>]*property=["\']og:image["\']', r.text)
                if match:
                    return match.group(1)
    except Exception as e:
        print(f"Cover Fetch Error: {e}")
    
    return ""

async def analyze_image_via_gemini(file_bytes: bytes, caption: str) -> str:
    prompt = "You are an assistant for a personal second brain. Analyze this image. If it contains text (like a document, page, or whiteboard), extract and transcribe all readable text accurately. If it is a picture, describe it in detail. Format your response as clean Markdown notes."
    if caption:
        prompt += f"\nContext/Notes provided by user: {caption}"
    
    response = await call_gemini_with_retry(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=file_bytes, mime_type="image/jpeg"),
            prompt
        ]
    )
    return response.text.strip()

def extract_pptx_text(file_path: str) -> str:
    """Extracts text from slides and speaker notes in a PowerPoint presentation."""
    from pptx import Presentation
    try:
        prs = Presentation(file_path)
        text_runs = []
        for slide_idx, slide in enumerate(prs.slides, 1):
            text_runs.append(f"--- Slide {slide_idx} ---")
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    text_runs.append(shape.text.strip())
            
            if slide.notes_slide and slide.notes_slide.notes_text_frame:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    text_runs.append(f"[Speaker Notes]: {notes}")
                    
        return "\n\n".join(text_runs).strip()
    except Exception as e:
        print(f"PPTX Text Extraction Error: {e}")
        return f"Error extracting text from PowerPoint presentation: {e}"

def extract_pptx_text_from_bytes(pptx_bytes: bytes) -> str:
    """Extracts text from PowerPoint presentation bytes."""
    import io
    from pptx import Presentation
    try:
        pptx_stream = io.BytesIO(pptx_bytes)
        prs = Presentation(pptx_stream)
        text_runs = []
        for slide_idx, slide in enumerate(prs.slides, 1):
            text_runs.append(f"--- Slide {slide_idx} ---")
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    text_runs.append(shape.text.strip())
            
            if slide.notes_slide and slide.notes_slide.notes_text_frame:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    text_runs.append(f"[Speaker Notes]: {notes}")
                    
        return "\n\n".join(text_runs).strip()
    except Exception as e:
        print(f"PPTX Bytes Text Extraction Error: {e}")
        return f"Error extracting text from PowerPoint presentation bytes: {e}"

