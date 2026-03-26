import os
import argparse
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# Configuration
OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not OBSIDIAN_VAULT_PATH or not GEMINI_API_KEY:
    raise RuntimeError("CRITICAL: OBSIDIAN_VAULT_PATH or GEMINI_API_KEY missing from .env")

SUMMARIES_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "Summaries")
os.makedirs(SUMMARIES_PATH, exist_ok=True)

client = genai.Client(api_key=GEMINI_API_KEY)

def generate_book_node(title: str, author: str) -> str:
    print(f"Researching book: '{title}' by {author or 'Unknown Author'}...")
    
    system_instruction = """You are an expert knowledge architect building a 'Second Brain' in Obsidian. 
Your goal is to summarize a book based on your internal knowledge and research, extracting a structured ontology.

You MUST use the provided Google Search tool to verify the book's core arguments, specific terminology, and key frameworks.
Do not hallucinate facts; if you cannot find specific details, stick to the general themes you can verify.

You must format your response entirely in valid Markdown, starting with a YAML frontmatter block.
CRITICAL RULES:
1. TOPICS AS WIKILINKS: Do NOT put topics, concepts, or themes in the 'tags' array. Tags are strictly for [brain, book].
2. Use double brackets [[Topic]] ONLY for concepts.
3. CANONICAL NAMING: You MUST aggressively standardize to these specific names to prevent graph duplication:
   - Use [[Artificial Intelligence]] (Never AI, artificial-intelligence, or #ai)
   - Use [[Artificial General Intelligence]] (Never AGI)
   - Use [[Ethics]] (Never Ethical AI)
   - Use [[Machine Learning]]
   - Use [[Large Language Model]] (Never LLM)
   - Use [[Geopolitics]]
   - Use [[Cybersecurity]]
4. ALWAYS use Title Case and Singular Nouns for wikilinks.
5. In the YAML frontmatter, 'tags' should ONLY contain ['brain', 'book'].

OUTPUT FORMAT TEMPLATE:
```yaml
---
title: "{Official Book Title}"
author: "{Author}"
url: "{Link to a major retailer or official page}"
date_processed: "{Date}"
date_captured: "{Date}"
status: "🆕 new"
type: "book"
cover: "{Image URL}"
tags: [brain, book]
---

## tl;dr
...
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
        f"Please provide a comprehensive summary and ontology for the following book:\n"
        f"Title Hint: {title}\n"
        f"Author Hint: {author}\n"
        f"Date: {date_str}"
    )
    
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.3,
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
    )
    
    # Strip markdown block formatting if Gemini includes it
    output = response.text.strip()
    if not output.startswith("---"):
        output = "---\n" + output
        
    return output

def fetch_book_cover(title: str, author: str) -> str:
    """Queries Google Books API for a cover image URL."""
    try:
        import httpx
        query = f"intitle:{title}"
        if author:
            query += f"+inauthor:{author}"
        
        url = "https://www.googleapis.com/books/v1/volumes"
        params = {"q": query, "maxResults": 1}
        
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, params=params)
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

def add_book(title: str, author: str):
    import re
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title)
    safe_title = re.sub(r'\s+', " ", safe_title).strip()[:100]
    
    markdown_content = generate_book_node(title, author)
    
    # Fetch real cover
    cover_url = fetch_book_cover(title, author)
    if cover_url:
        print(f"Found book cover: {cover_url}")
        if 'cover: "' in markdown_content:
            markdown_content = re.sub(r'cover: ".*?"', f'cover: "{cover_url}"', markdown_content)
        else:
            markdown_content = markdown_content.replace("type: \"book\"", f"cover: \"{cover_url}\"\ntype: \"book\"", 1)

    node_path = os.path.join(SUMMARIES_PATH, f"{safe_title}.md")
    with open(node_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
        
    print(f"\nSuccess! Book summary saved to: {node_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add a book to Centaur Brain and summarize it.")
    parser.add_argument("title", help="The title of the book.")
    parser.add_argument("--author", default="", help="Optional author to improve AI accuracy.")
    args = parser.parse_args()
    
    add_book(args.title, args.author)