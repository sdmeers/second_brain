import os
import argparse
import re
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# Local imports
from main import call_gemini_with_retry, update_concept_page, get_atlas_themes, OntologyExtraction, sanitize_filename
from logger import log_action

dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=dotenv_path)

# Configuration
OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not OBSIDIAN_VAULT_PATH or not GEMINI_API_KEY:
    raise RuntimeError("CRITICAL: OBSIDIAN_VAULT_PATH or GEMINI_API_KEY missing from .env")

SUMMARIES_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "02 Summaries")
os.makedirs(SUMMARIES_PATH, exist_ok=True)

client = genai.Client(api_key=GEMINI_API_KEY)

async def generate_book_node(title: str, author: str, atlas_themes: list[str]) -> OntologyExtraction:
    print(f"Researching book: '{title}' by {author or 'Unknown Author'}...")
    
    themes_str = "\n".join([f"   - {t}" for t in atlas_themes]) if atlas_themes else "   - (No established themes yet, you can invent some if needed)"
    
    system_instruction = f"""You are an expert knowledge architect building a 'Second Brain' in Obsidian. 
Your goal is to summarize a book based on your internal knowledge and research, extracting a structured ontology.

You MUST use the provided Google Search tool to verify the book's core arguments, specific terminology, and key frameworks.
Do not hallucinate facts; if you cannot find specific details, stick to the general themes you can verify.

CRITICAL RULES:
1. THEMES vs CONCEPTS:
    - THEMES: These are high-level Map of Content (MOC) categories from the user's established Atlas. You MUST identify ONE 'theme_primary' and zero or more 'theme_related' entries.
    - Use double brackets [[Topic]] ONLY for themes and concepts.
    - established ATLAS THEMES:
{themes_str}

2. CONCEPTS: Extract highly specific, non-trivial concepts, frameworks, and entities as wikilinks (e.g., [[Time Horizon]], [[Responsible Scaling Policy]], [[Collective Action Problem]]).
   - DO NOT extract generic, everyday business, tech, or academic words as new concepts.
   - EXCEPTION FOR THEMES: If a broad term perfectly matches one of your established ATLAS THEMES, you MUST categorize it under THEMES, NOT as a new CONCEPT.
   - Target roughly 5 to 14 highly impactful concepts per book. Quality and specificity are far more important than quantity.

3. DETAILED SUMMARIES: Write comprehensive, highly detailed summaries that preserve nuance and specific arguments. Err on the side of providing more detail.

4. TAGS: These are granular metadata tags for states and types. DO NOT use tags for topics.

5. OUTPUT FORMAT TEMPLATE for summary_markdown:
---
title: "📖 {{Official Book Title}}"
author: "{{Author}}"
url: "{{Link to a major retailer or official page}}"
date_processed: "{{Date}}"
date_captured: "{{Date}}"
status: "🟡 to-review"
theme_primary: "[[Theme Name]]"
theme_related: ["[[Theme 1]]", "[[Theme 2]]"]
type: "book"
cover: "📖"
tags: [brain, book]
---
# [[📖 {{Official Book Title}}]]

## tl;dr
...
{{A comprehensive and nuanced summary of the core message or contribution.}}

## Core Concepts
* **[[Concept 1]]**: {{Definition/context}}
* **[[Concept 2]]**: {{Definition/context}}

## Key Takeaways
* {{Point 1}}
* {{Point 2}}

## 🗺️ Context & MOC
- **[[Theme Primary]]**: {{One sentence on how this connects to the primary theme.}}
- **[[Theme Related 1]]**, **[[Theme Related 2]]**: {{How it intersects with other themes.}}

## Emergent Themes & Connections
{{Analyze how this intersects with its themes and implications for the future.}}
"""
    
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    prompt = (
        f"Please provide a comprehensive summary and ontology for the following book:\n"
        f"Title Hint: {title}\n"
        f"Author Hint: {author}\n"
        f"Date: {date_str}"
    )
    
    try:
        # Priority: Use gemini-2.5-flash with Google Search (has search quota)
        print("  [Attempt 1] Using gemini-2.5-flash with Search Tool...")
        # Note: We do not use `call_gemini_with_retry` here because it uses a different client interface with Tools
        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.3,
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "503" in str(e):
            print(f"  [FALLBACK] Search Quota Hit or Rate Limited ({str(e)[:3]}). Retrying without search using gemini-3.1-flash-lite-preview...")
            
            fallback_instruction = system_instruction + """
6. JSON SCHEMA POPULATION: You must return a JSON object.
   - The `summary_markdown` field must contain the full markdown text.
   - The `concepts` field MUST be an array of strings containing the exact wikilinks you extracted in the Core Concepts section (e.g., ["[[Time Horizon]]", "[[Responsible Scaling Policy]]"]). If you do not populate this array, the concept pages will not be created.
"""
            # Fallback: Use gemini-3.1-flash-lite-preview via the robust retry mechanism
            response = await call_gemini_with_retry(
                model="gemini-3.1-flash-lite-preview",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=fallback_instruction,
                    temperature=0.3,
                    response_mime_type="application/json",
                    response_schema=OntologyExtraction,
                )
            )
        else:
            raise e
            
    try:
        data = response.text
        
        # Remove markdown code blocks if the model wrapped the JSON in them
        clean_data = re.sub(r'^```[a-z]*\n', '', data)
        clean_data = re.sub(r'\n```$', '', clean_data)
        clean_data = clean_data.strip()
        
        # If the response isn't pure JSON (e.g. from attempt 1), try to extract it manually or mock the Pydantic object
        if "summary_markdown" not in clean_data:
            import json
            
            # If the model ignored the schema instruction entirely (e.g. due to search tools), we construct the object manually
            if not clean_data.startswith("{"):
                # Extract concepts from the markdown body
                extracted_concepts = re.findall(r'\*\*\s*(\[\[.*?\]\])\s*\*\*', clean_data)
                parsed = OntologyExtraction(summary_markdown=clean_data, concepts=extracted_concepts)
            else:
                 parsed = OntologyExtraction.model_validate_json(clean_data)
        else:
            parsed = OntologyExtraction.model_validate_json(clean_data)
        
        if not parsed.summary_markdown.strip().startswith("---"):
            parsed.summary_markdown = "---\n" + parsed.summary_markdown.strip()
            
        return parsed
    except Exception as parse_e:
        print(f"Backend [Gemini]: JSON Parsing Error: {parse_e}")
        print(f"Raw Output: {response.text}")
        raise

def fetch_book_cover(title: str, author: str) -> str:
    """Queries Google Books API for a cover image URL."""
    try:
        import httpx
        query = f"intitle:{title}"
        if author:
            query += f"+inauthor:{author}"
        
        url = "https://www.googleapis.com/books/v1/volumes"
        params = {"q": query, "maxResults": 1}
        
        with httpx.Client(timeout=10.0) as client_http:
            response = client_http.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                if "items" in data:
                    volume_info = data["items"][0].get("volumeInfo", {})
                    image_links = volume_info.get("imageLinks", {})
                    # Prefer high res if available
                    return image_links.get("extraLarge") or image_links.get("large") or image_links.get("thumbnail") or ""
    except Exception as e:
        print(f"Book Cover Fetch Error: {e}")
    return ""

async def add_book(title: str, author: str):
    safe_title = sanitize_filename(title)
    
    atlas_themes = get_atlas_themes()
    brain_node_result = await generate_book_node(title, author, atlas_themes)
    markdown_content = brain_node_result.summary_markdown.strip()
    
    # Standardize YAML (Fix brackets)
    def clean_yaml_brackets(content: str) -> str:
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
        
    markdown_content = clean_yaml_brackets(markdown_content)
    
    # Fetch real cover
    cover_url = fetch_book_cover(title, author)
    if cover_url:
        print(f"Found book cover: {cover_url}")
        if 'cover: "' in markdown_content:
            markdown_content = re.sub(r'cover: ".*?"', f'cover: "{cover_url}"', markdown_content)
        else:
            markdown_content = markdown_content.replace("type: \"book\"", f"cover: \"{cover_url}\"\ntype: \"book\"", 1)

    # Re-extract the true safe title from the generated Markdown
    match = re.search(r'^title:\s*"(.*?)"', markdown_content, re.MULTILINE)
    if match:
        extracted_title = match.group(1)
        safe_title = sanitize_filename(extracted_title)

    node_path = os.path.join(SUMMARIES_PATH, f"{safe_title}.md")
    with open(node_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
        
    # Process Concepts (Entity Update Loop)
    if brain_node_result.concepts:
        print(f"\n[Stage 2]: Processing {len(brain_node_result.concepts)} concepts...")
        for concept in brain_node_result.concepts:
            await update_concept_page(concept, markdown_content, safe_title)
    else:
        print(f"\n[Stage 2]: WARNING - 0 concepts were extracted by Gemini.")
        
    # Log the ingestion
    themes = []
    primary_match = re.search(r'theme_primary:\s*"(.*?)"', markdown_content)
    if primary_match:
        themes.append(primary_match.group(1))
        
    related_match = re.search(r'theme_related:\s*\[(.*?)\]', markdown_content)
    if related_match:
        related_themes = [t.strip().strip('"').strip("'") for t in related_match.group(1).split(',')]
        themes.extend([t for t in related_themes if t])
        
    log_action("Book Ingested", f'Added book: "{safe_title}"', concepts=themes)
        
    print(f"\nSuccess! Book summary saved to: {node_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add a book to Centaur Brain and summarize it.")
    parser.add_argument("title", help="The title of the book.")
    parser.add_argument("--author", default="", help="Optional author to improve AI accuracy.")
    args = parser.parse_args()
    
    asyncio.run(add_book(args.title, args.author))