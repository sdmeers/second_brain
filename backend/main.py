import os
import io
import re
import httpx
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
load_dotenv()

# Configuration
OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not OBSIDIAN_VAULT_PATH or not GEMINI_API_KEY:
    raise RuntimeError("CRITICAL: OBSIDIAN_VAULT_PATH or GEMINI_API_KEY missing from .env")

# Ensure Obsidian folders exist
INBOX_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "Inbox")
SOURCES_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "Sources")
os.makedirs(INBOX_PATH, exist_ok=True)
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

def generate_brain_node(title: str, author: str, url: str, content: str, source_file_name: str) -> str:
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
title: "{Title}"
author: "{Author}"
url: "{URL}"
date_processed: "{Date}"
type: "{article | video | paper | book}"
tags: [brain, tag1, tag2]
---
# [[{Title}]]

**Source Material:** [[{Source_File_Name}]]

## tl;dr
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
        f"Title: {title}\n"
        f"Author/Creator: {author}\n"
        f"Source: {url}\n"
        f"Source_File_Name: {source_file_name}\n"
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

@app.post("/process")
async def process_capture(payload: CapturePayload):
    try:
        print(f"Received capture request for: {payload.title}")
        safe_title = sanitize_filename(payload.title)
        
        content_text = payload.markdownText
        is_pdf = payload.url.lower().split('?')[0].endswith('.pdf')
        
        source_filename = ""
        
        # 1. Handle PDF vs Text Source Archiving
        if is_pdf:
            print("Processing as PDF...")
            content_text, pdf_bytes = extract_pdf_text(payload.url)
            source_filename = f"{safe_title}.pdf"
            source_path = os.path.join(SOURCES_PATH, source_filename)
            with open(source_path, "wb") as f:
                f.write(pdf_bytes)
        else:
            print("Processing as Text/Markdown...")
            if not content_text:
                raise HTTPException(status_code=400, detail="No markdown text provided for non-PDF source.")
            
            source_filename = f"{safe_title}_raw.md"
            source_path = os.path.join(SOURCES_PATH, source_filename)
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(f"# {payload.title}\nSource: {payload.url}\n\n{content_text}")
                
        # 2. Call Gemini for Analysis
        print("Calling Gemini to generate Brain Node...")
        brain_node_markdown = generate_brain_node(
            title=payload.title,
            author=payload.authorHint,
            url=payload.url,
            content=content_text,
            source_file_name=source_filename.replace('.md', '').replace('.pdf', '') # Strip extension for wikilink
        )
        
        # 3. Save the Brain Node to Inbox
        node_path = os.path.join(INBOX_PATH, f"{safe_title}.md")
        with open(node_path, "w", encoding="utf-8") as f:
            f.write(brain_node_markdown)
            
        print(f"Successfully processed: {safe_title}")
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
