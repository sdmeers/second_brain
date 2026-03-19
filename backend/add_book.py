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

INBOX_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "Inbox")
os.makedirs(INBOX_PATH, exist_ok=True)

client = genai.Client(api_key=GEMINI_API_KEY)

def generate_book_node(title: str, author: str) -> str:
    print(f"Researching book: '{title}' by {author or 'Unknown Author'}...")
    
    system_instruction = """You are an expert knowledge architect building a 'Second Brain' in Obsidian. 
Your goal is to summarize a book based on your internal knowledge and research, extracting a structured ontology.

You MUST use the provided Google Search tool to verify the book's core arguments, specific terminology, and key frameworks.
Do not hallucinate facts; if you cannot find specific details, stick to the general themes you can verify.

You must format your response entirely in valid Markdown, starting with a YAML frontmatter block.

CRITICAL RULES:
1. You must wrap key concepts, technologies, theories, or recurring themes in double brackets to create bi-directional links (e.g., [[Agentic Workflows]]).
2. Be concise but highly analytical. Extract the meaning, frameworks, and implications.
3. In the YAML frontmatter, provide an array of lowercase tags.

OUTPUT FORMAT TEMPLATE:
```yaml
---
title: "{Official Book Title}"
author: "{Author}"
url: "{Link to a major retailer or official page}"
date_processed: "{Date}"
type: "book"
tags: [brain, book, tag1, tag2]
---
# [[{Official Book Title}]]

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
        f"Please provide a comprehensive summary and ontology for the following book:\n"
        f"Title Hint: {title}\n"
        f"Author Hint: {author}\n"
        f"Date: {date_str}"
    )
    
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.3,
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
    )
    
    # Strip markdown block formatting if Gemini includes it
    output = response.text.strip()
    if output.startswith("```yaml"):
        output = output[3:].strip()
    if output.startswith("```markdown"):
        output = output[11:].strip()
    if output.endswith("```"):
        output = output[:-3].strip()
        
    return output

def add_book(title: str, author: str):
    import re
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()[:100]
    
    markdown_content = generate_book_node(title, author)
    
    node_path = os.path.join(INBOX_PATH, f"{safe_title}.md")
    with open(node_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
        
    print(f"\nSuccess! Book summary saved to: {node_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add a book to Centaur Brain and summarize it.")
    parser.add_argument("title", help="The title of the book.")
    parser.add_argument("--author", default="", help="Optional author to improve AI accuracy.")
    args = parser.parse_args()
    
    add_book(args.title, args.author)