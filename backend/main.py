import os
import io
import re
import json
import httpx
import yt_dlp
import fitz  # PyMuPDF
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=dotenv_path)

# Configuration
OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print(f"CRITICAL: GEMINI_API_KEY not found in {dotenv_path}")
    raise RuntimeError(f"CRITICAL: GEMINI_API_KEY missing from {dotenv_path}")

if not OBSIDIAN_VAULT_PATH:
    raise RuntimeError("CRITICAL: OBSIDIAN_VAULT_PATH missing from .env")

# Ensure Obsidian folders exist
SUMMARIES_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "Summaries")
SOURCES_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "Sources")
os.makedirs(SUMMARIES_PATH, exist_ok=True)
os.makedirs(SOURCES_PATH, exist_ok=True)

# Initialize Gemini Client
client = genai.Client(api_key=GEMINI_API_KEY)

# Initialize FastAPI App
app = FastAPI(title="Centaur Brain API")

# Configure CORS for Chrome Extension
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust this in production if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CapturePayload(BaseModel):
    source: str
    url: str
    title: Optional[str] = "Untitled Capture"
    authorHint: Optional[str] = ""
    markdownText: Optional[str] = ""

def sanitize_filename(filename: str) -> str:
    """Removes illegal characters and normalizes whitespace for filenames."""
    filename = re.sub(r'[\\/*?:"<>|]', "", filename)
    filename = re.sub(r'\s+', " ", filename)  # Collapse multiple spaces into one
    return filename.strip()[:100]  # Limit length

def extract_pdf_text(url: str) -> tuple[str, bytes]:
    """Downloads PDF and extracts text."""
    headers = {"User-Agent": "Mozilla/5.0"}
    response = httpx.get(url, follow_redirects=True, timeout=30.0, headers=headers)
    response.raise_for_status()
    
    pdf_bytes = response.content
    pdf_stream = io.BytesIO(pdf_bytes)
    doc = fitz.open(stream=pdf_stream, filetype="pdf")
    
    text = ""
    max_pages = min(20, len(doc)) # Extract up to 20 pages to save tokens
    for i in range(max_pages):
        text += doc[i].get_text()
        
    return text.strip(), pdf_bytes

def generate_brain_node(title_hint: str, author_hint: str, url: str, content: str) -> str:
    """Calls Gemini to generate the Obsidian-formatted ontology node."""
    system_instruction = """You are an expert knowledge architect building a 'Second Brain' in Obsidian. 
Your goal is to analyze the provided text and extract a structured ontology.

You must format your response entirely in valid Markdown, starting with a YAML frontmatter block.

CRITICAL RULES:
1. You must wrap key concepts, technologies, theories, or recurring themes in double brackets to create bi-directional links.
2. WIKILINK FORMATTING: You MUST aggressively standardize your wikilinks to prevent graph duplication.
   - ALWAYS use Title Case (e.g., [[Artificial Intelligence]], not [[artificial intelligence]]).
   - ALWAYS use singular nouns where possible (e.g., [[Autonomous Weapon]], not [[Autonomous Weapons]]).
   - ALWAYS spell out acronyms fully (e.g., [[Artificial General Intelligence]], not [[AGI]] or [[Artificial General Intelligence (AGI)]]).
3. Be concise but highly analytical. Do not just summarize; extract the meaning and implications.
4. If quoting directly from the text, use Markdown blockquotes (>).
5. Do not output the raw text again. You are only generating the analysis/summary node.
6. In the YAML frontmatter, provide an array of lowercase tags.
OUTPUT FORMAT TEMPLATE:
```yaml
---
title: "{Extract Title with Emoji Prefix}"
author: "{Extract the Author}"
url: "{URL}"
date_processed: "{Date}"
date_captured: "{Date}"
status: "🆕 new"
type: "{article | video | paper | book}"
cover: "{An icon/emoji representing the type, e.g., 📺, 📄, 📖, 🧪}"
tags: [brain, tag1, tag2]
---
# [[{Extract Title with Emoji Prefix}]]

## tl;dr
...
{A concise 2-sentence summary of the core message or contribution.}

## Core Concepts
* **[[Concept 1]]**: {Definition/context}
* **[[Concept 2]]**: {Definition/context}

## Key Takeaways
* {Point 1}
* {Point 2}

## Emergent Themes & Connections
{Where does this fit into the broader landscape? What are the implications?}
```"""
    
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    prompt = (
        f"Title Hint: {title_hint}\n"
        f"Author Hint: {author_hint}\n"
        f"Source: {url}\n"
        f"Date: {date_str}\n"
        f"Text to analyze: {content[:100000]}" # Limit context window just in case
    )
    
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.3
        )
    )
    
    # Strip markdown block formatting if Gemini includes it
    output = response.text.strip()
    if output.startswith("```yaml"):
        output = output[7:].strip()
    elif output.startswith("yaml\n---"):
        output = output[5:].strip()
    if output.startswith("```markdown"):
        output = output[11:].strip()
    if output.endswith("```"):
        output = output[:-3].strip()
        
    return output

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
        # Run yt-dlp in a thread pool since it's synchronous
        import asyncio
        from functools import partial
        
        print("Backend [Transcript]: Extracting video info (yt-dlp)...")
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, partial(ydl.extract_info, url, download=False))
        print("Backend [Transcript]: Info extraction complete.")
        
        all_subs = info.get('subtitles', {})
        all_auto_subs = info.get('automatic_captions', {})
        target_url = None
        
        # Language selection strategy: 
        # 1. Prefer manual 'en'
        # 2. Prefer manual 'en-...'
        # 3. Prefer auto 'en-orig' or 'en'
        # 4. Fallback to any 'en' prefix
        
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
            # Manual is better than auto
            score = 100 if c['type'] == 'manual' else 0
            # Exact/Standard codes are better
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

async def generate_brain_node(title_hint: str, author_hint: str, url: str, content: str) -> str:
    """Calls Gemini to generate the Obsidian-formatted ontology node."""
    print(f"Backend [Gemini]: Preparing prompt for content ({len(content)} chars)...")
    system_instruction = """You are an expert knowledge architect building a 'Second Brain' in Obsidian. 
Your goal is to analyze the provided text and extract a structured ontology.

You must format your response entirely in valid Markdown, starting with a YAML frontmatter block.

CRITICAL RULES:
1. You must wrap key concepts, technologies, theories, or recurring themes in double brackets to create bi-directional links.
2. WIKILINK FORMATTING: You MUST aggressively standardize your wikilinks to prevent graph duplication.
   - ALWAYS use Title Case (e.g., [[Artificial Intelligence]], not [[artificial intelligence]]).
   - ALWAYS use singular nouns where possible (e.g., [[Autonomous Weapon]], not [[Autonomous Weapons]]).
   - ALWAYS spell out acronyms fully (e.g., [[Artificial General Intelligence]], not [[Artificial General Intelligence (AGI)]]).
3. Be concise but highly analytical. Do not just summarize; extract the meaning and implications.
4. If quoting directly from the text, use Markdown blockquotes (>).
5. Do not output the raw text again. You are only generating the analysis/summary node.
6. In the YAML frontmatter, provide an array of lowercase tags.
OUTPUT FORMAT TEMPLATE:
```yaml
---
title: "{Extract Title with Emoji Prefix}"
author: "{Extract the Author}"
url: "{URL}"
date_processed: "{Date}"
date_captured: "{Date}"
status: "🆕 new"
type: "{article | video | paper | book}"
cover: "{An icon/emoji representing the type, e.g., 📺, 📄, 📖, 🧪}"
tags: [brain, tag1, tag2]
---
# [[{Extract Title with Emoji Prefix}]]

## tl;dr
...
{A concise 2-sentence summary of the core message or contribution.}

## Core Concepts
* **[[Concept 1]]**: {Definition/context}
* **[[Concept 2]]**: {Definition/context}

## Key Takeaways
* {Point 1}
* {Point 2}

## Emergent Themes & Connections
{Where does this fit into the broader landscape? What are the implications?}
```"""
    
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    prompt = (
        f"Title Hint: {title_hint}\n"
        f"Author Hint: {author_hint}\n"
        f"Source: {url}\n"
        f"Date: {date_str}\n"
        f"Text to analyze: {content[:100000]}" # Limit context window just in case
    )
    
    try:
        print(f"Backend [Gemini]: Calling Gemini API (model: gemini-3.1-flash-lite-preview)...")
        # Use the async client (aio)
        response = await client.aio.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.3
            )
        )
        print("Backend [Gemini]: API call successful.")
        
        # Strip markdown block formatting if Gemini includes it
        output = response.text.strip()
        if output.startswith("```yaml"):
            output = output[7:].strip()
        elif output.startswith("yaml\n---"):
            output = output[5:].strip()
        if output.startswith("```markdown"):
            output = output[11:].strip()
        if output.endswith("```"):
            output = output[:-3].strip()
            
        return output
    except Exception as e:
        print(f"Backend [Gemini]: ERROR: {e}")
        raise

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
    
    # Simple Open Graph fallback for articles
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        with httpx.Client(follow_redirects=True, timeout=10.0, headers=headers) as c:
            r = c.get(url)
            if r.status_code == 200:
                # Look for og:image meta tag
                match = re.search(r'<meta [^>]*property=["'']og:image["''][^>]*content=["''](.*?)["'']', r.text)
                if not match:
                    match = re.search(r'<meta [^>]*content=["''](.*?)["''][^>]*property=["'']og:image["'']', r.text)
                if match:
                    return match.group(1)
    except Exception as e:
        print(f"Cover Fetch Error: {e}")
    
    return ""

@app.post("/process")
async def process_capture(payload: CapturePayload):
    try:
        print(f"\n>>> Backend: Processing Request: {payload.title}")
        
        content_text = payload.markdownText
        clean_url = payload.url.lower().split('?')[0]
        is_pdf = clean_url.endswith('.pdf') or '/pdf/' in clean_url
        is_youtube = "youtube.com/watch" in payload.url or "youtu.be/" in payload.url
        
        pdf_bytes = None
        
        # 1. Handle PDF vs Text extraction vs YouTube Fallback
        if is_pdf:
            print("Backend [Stage 1]: Processing as PDF...")
            content_text, pdf_bytes = extract_pdf_text(payload.url)
        elif is_youtube and not content_text:
            print("Backend [Stage 1]: Processing as YouTube (via backend fallback)...")
            content_text = await extract_youtube_transcript(payload.url)
        else:
            print(f"Backend [Stage 1]: Processing as Text ({len(content_text) if content_text else 0} chars)...")
            if not content_text:
                raise HTTPException(status_code=400, detail="No markdown text provided.")
                
        # 2. Call Gemini
        print("Backend [Stage 2]: Generating Brain Node...")
        brain_node_markdown = await generate_brain_node(
            title_hint=payload.title,
            author_hint=payload.authorHint,
            url=payload.url,
            content=content_text
        )
        
        # 3. Fetch Cover Image
        print("Backend [Stage 3]: Fetching Cover Image...")
        cover_url = fetch_cover(payload.url, is_youtube)
        if cover_url:
            print(f"Backend [Stage 3]: Found cover: {cover_url}")
            # Inject cover into YAML frontmatter if gemini hasn't provided a better one
            if 'cover: "' in brain_node_markdown:
                brain_node_markdown = re.sub(r'cover: ".*?"', f'cover: "{cover_url}"', brain_node_markdown)
            else:
                brain_node_markdown = brain_node_markdown.replace("type:", f"cover: \"{cover_url}\"\ntype:", 1)
        
        # 4. Parse true title
        print("Backend [Stage 4]: Finalizing and Saving...")
        real_title = payload.title
        match = re.search(r'^title:\s*"(.*?)"', brain_node_markdown, re.MULTILINE)
        if match:
            real_title = match.group(1)
            
        safe_title = sanitize_filename(real_title)
        print(f"Backend [Stage 4]: Determined Title: {safe_title}")
        
        # 5. Save Source file
        source_link_name = ""
        if is_pdf:
            source_filename = f"{safe_title}.pdf"
            source_link_name = source_filename
            source_path = os.path.join(SOURCES_PATH, source_filename)
            with open(source_path, "wb") as f:
                f.write(pdf_bytes)
        else:
            source_filename = f"{safe_title}_raw.md"
            source_link_name = f"{safe_title}_raw"
            source_path = os.path.join(SOURCES_PATH, source_filename)
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(f"# {real_title}\nSource: {payload.url}\n\n{content_text}")
                
        # 6. Save Brain Node
        source_injection = f"\n\n**Source Material:** [[{source_link_name}]]\n\n## tl;dr"
        brain_node_markdown = brain_node_markdown.replace("\n## tl;dr", source_injection, 1)
        
        node_path = os.path.join(SUMMARIES_PATH, f"{safe_title}.md")
        with open(node_path, "w", encoding="utf-8") as f:
            f.write(brain_node_markdown)
            
        print(f"<<< Backend: Request Complete: {safe_title}")
        return {"status": "success", "title": safe_title, "node_path": node_path}
        
    except httpx.HTTPStatusError as e:
        print(f"HTTP Error: {e}")
        raise HTTPException(status_code=400, detail="Failed to download source URL")
    except Exception as e:
        print(f"Internal Server Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Run the server natively
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=True)
