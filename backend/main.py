import os
import io
import re
import json
import httpx
import yt_dlp
import fitz  # PyMuPDF
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
from logger import log_action

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
SUMMARIES_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "02 Summaries")
SOURCES_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "01 Sources")
ATLAS_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "03 Atlas")
CONCEPTS_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "04 Concepts")
os.makedirs(SUMMARIES_PATH, exist_ok=True)
os.makedirs(SOURCES_PATH, exist_ok=True)
os.makedirs(ATLAS_PATH, exist_ok=True)
os.makedirs(CONCEPTS_PATH, exist_ok=True)

import asyncio

# Initialize Gemini Client
client = genai.Client(api_key=GEMINI_API_KEY)

async def call_gemini_with_retry(model: str, contents: str, config: types.GenerateContentConfig = None, max_retries: int = 5):
    """Wrapper to call Gemini API with exponential backoff for 429 and 503 errors."""
    for attempt in range(max_retries):
        try:
            return await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "503" in error_str:
                wait_time = (2 ** attempt) + 2  # 3s, 4s, 6s, 10s, 18s
                print(f"Backend [Gemini]: Encountered {error_str[:3]} error. Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                await asyncio.sleep(wait_time)
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Failed after {max_retries} attempts: {error_str}")
            else:
                raise e

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

class OntologyExtraction(BaseModel):
    summary_markdown: str = Field(description="The complete markdown formatted summary including YAML frontmatter.")
    concepts: list[str] = Field(description="List of all wikilinks extracted in the Core Concepts section (e.g. ['[[Concept 1]]', '[[Concept 2]]']). MUST NOT BE EMPTY.")

def sanitize_filename(filename: str) -> str:
    """Removes illegal characters and normalizes whitespace for filenames."""
    filename = re.sub(r'[\\/*?:"<>|]', "", filename)
    filename = re.sub(r'\s+', " ", filename)  # Collapse multiple spaces into one
    return filename.strip()[:100]  # Limit length

def get_atlas_themes() -> list[str]:
    """Reads the 03 Atlas folder and returns a list of themes as wikilinks."""
    try:
        if not os.path.exists(ATLAS_PATH):
            return []
        files = os.listdir(ATLAS_PATH)
        return [f"[[{f[:-3]}]]" for f in files if f.endswith(".md")]
    except Exception as e:
        print(f"Error reading Atlas Themes: {e}")
        return []

def get_existing_concepts() -> list[str]:
    """Reads the 04 Concepts folder and returns a list of concepts as wikilinks."""
    try:
        if not os.path.exists(CONCEPTS_PATH):
            return []
        files = os.listdir(CONCEPTS_PATH)
        return [f"[[{f[:-3]}]]" for f in files if f.endswith(".md")]
    except Exception as e:
        print(f"Error reading Existing Concepts: {e}")
        return []

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

async def generate_brain_node(title_hint: str, author_hint: str, url: str, content: str, atlas_themes: list[str]) -> OntologyExtraction:
    """Calls Gemini to generate the Obsidian-formatted ontology node and a list of concepts."""
    print(f"Backend [Gemini]: Preparing prompt for content ({len(content)} chars)...")
    
    themes_str = "\n".join([f"   - {t}" for t in atlas_themes]) if atlas_themes else "   - (No established themes yet, you can invent some if needed)"
    
    existing_concepts = get_existing_concepts()
    concepts_str = ", ".join(existing_concepts) if existing_concepts else "(No established concepts yet)"
    
    system_instruction = f"""You are an expert knowledge architect building a 'Second Brain' in Obsidian.
Your goal is to analyze the provided text and extract a structured ontology based on a 'Map of Content' (MOC) strategy.

CRITICAL RULES:
1. THEMES vs CONCEPTS:
    - THEMES: These are high-level Map of Content (MOC) categories from the user's established Atlas. You MUST identify ONE 'theme_primary' and zero or more 'theme_related' entries.
    - Use double brackets [[Topic]] ONLY for themes and concepts.
    - established ATLAS THEMES:
{themes_str}

2. CONCEPTS: Extract highly specific, non-trivial concepts, frameworks, and entities as wikilinks (e.g., [[Time Horizon]], [[Responsible Scaling Policy]], [[Collective Action Problem]]).
   - DO NOT extract generic, everyday business, tech, or academic words as new concepts (e.g., avoid creating concept pages for [[External Review]], [[Internal Accountability]], [[Industry-wide Safety]]).
   - EXCEPTION FOR THEMES: If a broad term perfectly matches one of your established ATLAS THEMES (like [[Innovation]] or [[Technology]]), you MUST categorize it under THEMES (theme_primary or theme_related), NOT as a new CONCEPT.
   - Target roughly 5 to 14 highly impactful concepts per document. Quality and specificity are far more important than quantity. Do not over-saturate with generic ideas.
   - NEVER include the title of the document itself or other referenced documents (e.g. things starting with 🎞️, 🏛️, 📄, 📖) in the 'concepts' array. Those are source nodes, not concept nodes.

3. FORMATTING & NAMING GUARDRAILS FOR CONCEPTS:
   - Here is a list of existing concepts in the vault: {concepts_str}
   - If a concept you identify is highly similar, synonymous, or an acronym of an existing concept, DO NOT create a new name. You MUST use the exact existing concept name from the list for your wikilinks.
   - When generating truly new concept titles, adhere strictly to these rules:
     a) Always use singular nouns (e.g., 'Agent', not 'Agents').
     b) Always use the full term, omitting parenthetical acronyms in titles (e.g., 'Minimum Viable Product', never 'Minimum Viable Product (MVP)').
     c) Remove hyphens from compound concepts unless grammatically strictly required (e.g., 'Hyperwar', never 'Hyper-war').

4. DETAILED SUMMARIES: Write comprehensive, highly detailed summaries that preserve nuance and specific arguments, rather than over-simplified high-level overviews. Err on the side of providing more detail.

5. TAGS: These are granular metadata tags for states and types (e.g., #article, #book). DO NOT use tags for topics.

6. EMOJI PREFIXES: You must prefix the title with an emoji based on the content type:
   - article: 📄
   - video: 🎞️
   - paper: 🏛️
   - book: 📖

7. OUTPUT FORMAT TEMPLATE for summary_markdown:
---
title: "{{Emoji}} {{Extract Title}}"
author: "{{Extract the Author}}"
url: "{{URL}}"
date_processed: "{{Date}}"
date_captured: "{{Date}}"
status: "🟡 to-review"
theme_primary: "[[Theme Name]]"
theme_related: ["[[Theme 1]]", "[[Theme 2]]"]
type: "{{article | video | paper | book}}"
cover: "{{Emoji}}"
tags: [brain]
---
# [[{{Emoji}} {{Extract Title}}]]

## tl;dr
...
{{A comprehensive and nuanced summary of the core message or contribution.}}

## Core Concepts
* **[[Concept 1]]**: {{Brief context}}
* **[[Concept 2]]**: {{Brief context}}

## Key Takeaways
* {{Point 1}}
* {{Point 2}}

## 🗺️ Context & MOC
- **[[Theme Primary]]**: {{One sentence on how this connects to the primary theme.}}
- **[[Theme Related 1]]**, **[[Theme Related 2]]**: {{How it intersects with other themes.}}

## Emergent Themes & Connections
{{Analyze how this intersects with its themes and implications for the future.}}

8. JSON SCHEMA POPULATION: You must return a JSON object.
   - The `summary_markdown` field must contain the full markdown text.
   - The `concepts` field MUST be an array of strings containing the exact wikilinks you extracted in the Core Concepts section (e.g., ["[[Time Horizon]]", "[[Responsible Scaling Policy]]"]). If you do not populate this array, the concept pages will not be created.
"""
    
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
        response = await call_gemini_with_retry(
            model="gemini-3.1-flash-lite-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.3,
                response_mime_type="application/json",
                response_schema=OntologyExtraction,
            )
        )
        print("Backend [Gemini]: API call successful.")
        
        try:
            data = response.text
            parsed = OntologyExtraction.model_validate_json(data)
            
            # Ensure markdown starts with ---
            if not parsed.summary_markdown.strip().startswith("---"):
                parsed.summary_markdown = "---\n" + parsed.summary_markdown.strip()
                
            return parsed
        except Exception as parse_e:
            print(f"Backend [Gemini]: JSON Parsing Error: {parse_e}")
            print(f"Raw Output: {response.text}")
            raise
    except Exception as e:
        print(f"Backend [Gemini]: ERROR: {e}")
        raise

async def update_concept_page(concept_name: str, new_summary: str, source_title: str):
    """Updates an existing concept page or creates a new one, prioritizing Atlas themes."""
    clean_concept = sanitize_filename(concept_name.replace('[[', '').replace(']]', ''))
    
    atlas_path = os.path.join(ATLAS_PATH, f"{clean_concept}.md")
    concept_path = os.path.join(CONCEPTS_PATH, f"{clean_concept}.md")
    
    # Prioritize Atlas over Concepts
    target_path = atlas_path if os.path.exists(atlas_path) else concept_path
    is_atlas = target_path == atlas_path
    
    if os.path.exists(target_path):
        print(f"Backend [Concept]: Updating existing {'atlas' if is_atlas else 'concept'} [[{clean_concept}]]")
        with open(target_path, "r", encoding="utf-8") as f:
            existing_content = f.read()
            
        preserve_msg = "\n\nCRITICAL: This is an Atlas Map of Content. DO NOT modify the '# Map of Content' section or any Dataview queries. Only update the definition and discussion sections above it." if is_atlas else ""
            
        prompt = f"""Here is the existing {'atlas theme' if is_atlas else 'concept'} page for '{clean_concept}':
{existing_content}

Here is a new source summary that references it:
{new_summary}

Please update the page to integrate any new information, note contradictions, and add a backlink to the new source ([[{source_title}]]). Return the complete updated Markdown.{preserve_msg}
"""
        try:
            response = await call_gemini_with_retry(
                model="gemini-3.1-flash-lite-preview",
                contents=prompt,
            )
            updated_content = response.text.strip()
            # Safety check: if is_atlas, ensure we didn't lose the dataview
            if is_atlas and "```dataview" not in updated_content and "```dataview" in existing_content:
                print(f"Backend [Concept]: WARNING - Gemini stripped dataview from [[{clean_concept}]]. Attempting to restore.")
                # Simple restoration: append the old dataview section if it's missing
                if "# Map of Content" in existing_content:
                    moc_part = existing_content.split("# Map of Content")[-1]
                    if "# Map of Content" not in updated_content:
                        updated_content += "\n\n# Map of Content" + moc_part
            
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(updated_content)
            log_action("Concept Updated", f'Updated [[{clean_concept}]] with context from "{source_title}"')
        except Exception as e:
            print(f"Backend [Concept]: Failed to update [[{clean_concept}]]: {e}")
    else:
        print(f"Backend [Concept]: Creating new concept [[{clean_concept}]]")
        prompt = f"""Generate a baseline definition and concept page for '{clean_concept}' based on its usage in this new source summary:
{new_summary}

Please include a backlink to the source: [[{source_title}]]. Return the Markdown.
"""
        try:
            response = await call_gemini_with_retry(
                model="gemini-3.1-flash-lite-preview",
                contents=prompt,
            )
            new_content = response.text.strip()
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as e:
            print(f"Backend [Concept]: Failed to create concept [[{clean_concept}]]: {e}")

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
                match = re.search(r'<meta [^>]*property=["\']og:image["\'][^>]*content=["\'](.*?)["\']', r.text)
                if not match:
                    match = re.search(r'<meta [^>]*content=["\'](.*?)["\'][^>]*property=["\']og:image["\']', r.text)
                if match:
                    return match.group(1)
    except Exception as e:
        print(f"Cover Fetch Error: {e}")
    
    return ""

from bs4 import BeautifulSoup

def extract_web_text(url: str) -> str:
    """Fetches a URL and extracts readable text using BeautifulSoup."""
    headers = {"User-Agent": "Mozilla/5.0"}
    response = httpx.get(url, follow_redirects=True, timeout=30.0, headers=headers)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    # Remove script and style elements
    for script in soup(["script", "style"]):
        script.extract()
        
    text = soup.get_text()
    
    # Break into lines and remove leading and trailing whitespace
    lines = (line.strip() for line in text.splitlines())
    # Break multi-headlines into a line each
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    # Drop blank lines
    text = '\n'.join(chunk for chunk in chunks if chunk)
    
    return text.strip()

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
        elif not content_text:
            print(f"Backend [Stage 1]: Scraping Web Content from {payload.url}...")
            content_text = extract_web_text(payload.url)
        else:
            print(f"Backend [Stage 1]: Processing as Text ({len(content_text) if content_text else 0} chars)...")
                
        # 2. Call Gemini
        print("Backend [Stage 2]: Generating Brain Node...")
        atlas_themes = get_atlas_themes()
        brain_node_result = await generate_brain_node(
            title_hint=payload.title,
            author_hint=payload.authorHint,
            url=payload.url,
            content=content_text,
            atlas_themes=atlas_themes
        )

        # 3. Clean Gemini output (Strip markdown backticks if present)
        brain_node_markdown = brain_node_result.summary_markdown.strip()
        # Remove ```yaml or ``` at start/end
        brain_node_markdown = re.sub(r'^```[a-z]*\n', '', brain_node_markdown)
        brain_node_markdown = re.sub(r'\n```$', '', brain_node_markdown)
        brain_node_markdown = brain_node_markdown.strip()
        
        # 4. Standardize YAML (Fix brackets)
        def clean_yaml_brackets(content: str) -> str:
            """Surgically fixes theme_primary: [[Topic]] and theme_related: [[Topic1], [Topic2]] issues."""
            def fix_list(match):
                val = match.group(1)
                items = re.findall(r'\[+([^\[\]]+)\]+', val)
                clean_items = []
                for item in items:
                    parts = [p.strip().strip('"').strip("'") for p in item.split(',')]
                    clean_items.extend([p for p in parts if p])
                formatted = ", ".join([f'"[[{t}]]"' for t in clean_items])
                return f'theme_related: [{formatted}]'

            def fix_primary(match):
                val = match.group(1)
                items = re.findall(r'\[+([^\[\]]+)\]+', val)
                if items:
                    t = items[0].strip().strip('"').strip("'")
                    return f'theme_primary: "[[{t}]]"'
                return match.group(0)

            content = re.sub(r'theme_related:\s*(.*)', fix_list, content)
            content = re.sub(r'theme_primary:\s*(.*)', fix_primary, content)
            return content
            
        brain_node_markdown = clean_yaml_brackets(brain_node_markdown)

        # 5. Fetch Cover Image
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
            
        # Strip browser notification counts like (1) or (1234) from the start
        real_title = re.sub(r'^\(\d+\)\s*', '', real_title)
            
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
                f.write(f"Source: {payload.url}\n\n{content_text}")
                
        # 6. Save Brain Node
        source_injection = f"\n\n**Source Material:** [[{source_link_name}]]\n\n## tl;dr"
        brain_node_markdown = brain_node_markdown.replace("\n## tl;dr", source_injection, 1)
        
        node_path = os.path.join(SUMMARIES_PATH, f"{safe_title}.md")
        with open(node_path, "w", encoding="utf-8") as f:
            f.write(brain_node_markdown)
            
        # 7. Process Concepts (Entity Update Loop)
        if brain_node_result.concepts:
            print(f"Backend [Stage 5]: Processing {len(brain_node_result.concepts)} concepts with throttling...")
            for concept in brain_node_result.concepts:
                await update_concept_page(concept, brain_node_markdown, safe_title)
                await asyncio.sleep(4)  # Stay under 15 RPM limit
        else:
            print(f"Backend [Stage 5]: WARNING - 0 concepts were extracted by Gemini.")
            
        # Log the ingestion
        themes = []
        primary_match = re.search(r'theme_primary:\s*"(.*?)"', brain_node_markdown)
        if primary_match:
            themes.append(primary_match.group(1))
            
        related_match = re.search(r'theme_related:\s*\[(.*?)\]', brain_node_markdown)
        if related_match:
            related_themes = [t.strip().strip('"').strip("'") for t in related_match.group(1).split(',')]
            themes.extend([t for t in related_themes if t])
            
        log_action("Ingested", f'Source: "{safe_title}"', concepts=themes)
            
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
