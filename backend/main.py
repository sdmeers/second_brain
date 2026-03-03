import functions_framework
import json
import os
import io
import httpx
import fitz  # PyMuPDF
from bs4 import BeautifulSoup
from notion_client import Client
from google import genai
from google.genai import types
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Initialize Vertex AI via the new google-genai SDK
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "vibecook-prod-sdm")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")

client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION
)

# Initialize Notion Client
NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

if not NOTION_API_KEY:
    print("CRITICAL: NOTION_API_KEY is missing from environment!")
else:
    # Masked print to verify token is loaded
    print(f"Notion Client initialized with token: {NOTION_API_KEY[:7]}...{NOTION_API_KEY[-4:]}")

notion = Client(auth=NOTION_API_KEY)

def get_cors_headers(request):
    if request.method == 'OPTIONS':
        return {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
            'Access-Control-Max-Age': '3600'
        }
    return {'Access-Control-Allow-Origin': '*'}

def get_existing_categories():
    """Fetches existing Multi-select options from the Notion Database property 'Categories'."""
    try:
        db = notion.databases.retrieve(database_id=NOTION_DATABASE_ID)
        properties = db.get("properties", {})
        
        # In Notion SDK 3.0.0+, synced databases (Data Sources) might store properties differently
        if not properties and "data_sources" in db and db["data_sources"]:
            ds_id = db["data_sources"][0]["id"]
            ds = notion.data_sources.retrieve(data_source_id=ds_id)
            properties = ds.get("properties", {})

        categories_prop = properties.get("Categories", {})
        options = categories_prop.get("multi_select", {}).get("options", [])
        return [opt["name"] for opt in options]
    except Exception as e:
        print(f"Error fetching categories: {e}")
        return []

def fetch_and_extract_content(url):
    """Downloads content from a URL and extracts text if it's a PDF or HTML."""
    try:
        print(f"Fetching URL for backend extraction: {url}")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = httpx.get(url, follow_redirects=True, timeout=30.0, headers=headers)
        response.raise_for_status()
        
        content_type = response.headers.get("Content-Type", "").lower()
        
        if "application/pdf" in content_type or url.lower().split('?')[0].endswith(".pdf"):
            print("Processing as PDF...")
            pdf_stream = io.BytesIO(response.content)
            doc = fitz.open(stream=pdf_stream, filetype="pdf")
            
            text = ""
            max_pages = min(15, len(doc))
            for i in range(max_pages):
                text += doc[i].get_text()
            return text.strip()
        else:
            print(f"Processing as HTML (Content-Type: {content_type})...")
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Remove scripts, styles, and other non-content elements
            for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
                element.decompose()
            
            # Extract text
            text = soup.get_text(separator=' ', strip=True)
            # Limit length to avoid prompt bloat
            return text[:50000]
    except Exception as e:
        print(f"Error fetching/extracting content: {e}")
        return None

def markdown_to_notion_blocks(markdown_text):
    """Converts a Markdown string into Notion-compatible blocks."""
    blocks = []
    lines = markdown_text.split("\n")
    for line in lines:
        line = line.strip()
        if not line: continue
        
        if line.startswith("### "):
            blocks.append({
                "object": "block", "type": "heading_3",
                "heading_3": {"rich_text": [{"type": "text", "text": {"content": line[4:]}}]}
            })
        elif line.startswith("## "):
            blocks.append({
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]}
            })
        elif line.startswith("- ") or line.startswith("* "):
            blocks.append({
                "object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]}
            })
        else:
            # Simple text fallback (bolding/italics are handled as plain text here for simplicity)
            blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": line.replace("**", "").replace("_", "")}}]}
            })
    return blocks

import re

def call_vertex_ai(user_input, existing_categories, is_book=False, title_hint="", author_hint=""):
    """Constructs prompt and calls Gemini 2.0 Flash to summarize content."""
    # Ensure existing_categories uses double quotes in the prompt
    categories_str = json.dumps(existing_categories)
    
    system_instruction = (
        "You are 'Centaur Notes', an expert AI researcher and summarizer. "
        "Your goal is to categorize and summarize content with high precision."
        "\n\nEXTRACTION GUIDELINES:"
        "\n1. Title: Strictly extract the actual title from the source. The provided 'TITLE HINT' is the official page title; use it as the primary source for the Title unless it is clearly generic (like 'Substack')."
        "\n2. Author(s): Identify the primary author(s). Use the provided 'AUTHOR HINT' as a primary clue."
        "\n3. Type: Classify the content as one of: Paper, News, Blog, Report, Video, Podcast, Book."
        "   - 'Paper' means an academic paper or peer-reviewed journal article."
        "   - 'Video' is usually a YouTube video but could be any video source."
        "   - 'Podcast' is for audio shows."
        "\n4. Keywords: Provide 3-5 descriptive keywords that capture the specific topics discussed."
        "\n\nCATEGORIZATION GUIDELINES:"
        f"\n1. Use these 'Existing Categories' if they are highly relevant: {categories_str}."
        "\n2. If the content's primary theme is NOT covered by existing categories (e.g., 'Leadership', 'History'), create 1-2 NEW high-level categories."
        "\n3. Avoid redundancy: Do NOT create new categories that are synonyms or narrow subsets of existing ones."
        "\n4. Accuracy: Never assign a category that is not a core theme of the content. Do not force-fit unrelated content into existing categories."
        "\n\nSUMMARY GUIDELINES:"
        "\n- Provide a detailed Markdown summary (300-600 words)."
        "\n- Use ### for subheadings."
        "\n- Elaborate on nuances and key arguments beyond the top 3 points."
        "\n\nCRITICAL: Output ONLY valid JSON using double quotes for all property names and string values."
    )
    
    prompt = (
        f"Analyze this {'book title' if is_book else 'content'}:\n"
        f"TITLE HINT: {title_hint}\n"
        f"AUTHOR HINT: {author_hint}\n"
        f"CONTENT: {user_input}"
    )

    response_schema = {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING"},
            "authors": {
                "type": "ARRAY",
                "items": {"type": "STRING"}
            },
            "type": {
                "type": "STRING",
                "enum": ["Paper", "News", "Blog", "Report", "Video", "Podcast", "Book"]
            },
            "keywords": {
                "type": "ARRAY",
                "items": {"type": "STRING"}
            },
            "top_3_points": {
                "type": "ARRAY",
                "items": {"type": "STRING"}
            },
            "summary": {"type": "STRING"},
            "selected_categories": {
                "type": "ARRAY",
                "items": {"type": "STRING"}
            },
        },
        "required": ["title", "authors", "type", "keywords", "top_3_points", "summary", "selected_categories"]
    }

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=response_schema
        )
    )
    
    raw_text = response.text.strip()
    
    def repair_json(text):
        # 1. Remove markdown code blocks
        text = re.sub(r'^```(?:json)?\n', '', text, flags=re.MULTILINE)
        text = re.sub(r'\n```$', '', text, flags=re.MULTILINE)
        
        # 2. Try parsing directly
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
            
        # 3. Handle single quotes issue (common LLM failure)
        # This is a basic repair: replace ' with " but be careful about apostrophes
        # We only replace single quotes that look like property delimiters
        text_repaired = re.sub(r"\'(\w+)\'\s*:", r'"\1":', text) # Property names
        text_repaired = re.sub(r":\s*\'(.*?)\'([,\s}])", r': "\1"\2', text_repaired) # String values
        
        try:
            return json.loads(text_repaired)
        except json.JSONDecodeError as e:
            print(f"Failed to repair JSON. Raw text: {text[:500]}")
            raise e

    try:
        return repair_json(raw_text)
    except Exception as e:
        print(f"AI JSON Parse Error: {e}")
        raise e

@functions_framework.http
def centaur_api(request):
    headers = get_cors_headers(request)
    if request.method == 'OPTIONS': return ('', 204, headers)

    request_json = request.get_json(silent=True)
    if not request_json: return (json.dumps({"error": "No payload"}), 400, headers)

    source = request_json.get("source")
    existing_categories = get_existing_categories()

    try:
        if source == "extension":
            url = request_json.get("url")
            markdown_text = request_json.get("markdownText")
            title_hint = request_json.get("title", "")
            author_hint = request_json.get("authorHint", "")
            
            # If no text provided, try to fetch and extract from URL on backend
            if not markdown_text or markdown_text.strip() == "":
                markdown_text = fetch_and_extract_content(url)
                if not markdown_text:
                    return (json.dumps({"error": "Failed to extract text from this URL. Only articles and PDFs are supported."}), 400, headers)

            ai_data = call_vertex_ai(markdown_text, existing_categories, title_hint=title_hint, author_hint=author_hint)
            
            new_page = notion.pages.create(
                parent={"database_id": NOTION_DATABASE_ID},
                properties={
                    "Name": {"title": [{"text": {"content": ai_data.get("title", "Untitled")}}]},
                    "URL": {"url": url},
                    "Authors": {"rich_text": [{"text": {"content": ", ".join(ai_data.get("authors", []))}}]},
                    "Type": {"select": {"name": ai_data.get("type", "News")}},
                    "Keywords": {"multi_select": [{"name": k[:100]} for k in ai_data.get("keywords", [])]},
                    "Categories": {"multi_select": [{"name": c} for c in ai_data.get("selected_categories", [])]},
                    "Top 3 Points": {"rich_text": [{"text": {"content": "\n".join([f"• {p}" for p in ai_data.get("top_3_points", [])])}}]},
                    "Date Added": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}}
                },
                children=markdown_to_notion_blocks(ai_data.get("summary", ""))
            )
            return (json.dumps({"status": "success", "id": new_page["id"]}), 200, headers)

        elif source == "notion_button":
            page_id = request_json.get("page_id")
            page = notion.pages.retrieve(page_id=page_id)
            title = page["properties"]["Name"]["title"][0]["plain_text"]
            
            ai_data = call_vertex_ai(title, existing_categories, is_book=True, title_hint=title)
            
            notion.pages.update(
                page_id=page_id,
                properties={
                    "Authors": {"rich_text": [{"text": {"content": ", ".join(ai_data.get("authors", []))}}]},
                    "Type": {"select": {"name": ai_data.get("type", "Book")}},
                    "Keywords": {"multi_select": [{"name": k[:100]} for k in ai_data.get("keywords", [])]},
                    "Categories": {"multi_select": [{"name": c} for c in ai_data.get("selected_categories", [])]},
                    "Top 3 Points": {"rich_text": [{"text": {"content": "\n".join([f"• {p}" for p in ai_data.get("top_3_points", [])])}}]},
                    "Date Added": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}}
                }
            )
            notion.blocks.children.append(
                block_id=page_id,
                children=markdown_to_notion_blocks(ai_data.get("summary", ""))
            )
            return (json.dumps({"status": "success"}), 200, headers)

        return (json.dumps({"error": "Invalid source"}), 400, headers)

    except Exception as e:
        print(f"Centaur Error: {e}")
        return (json.dumps({"error": str(e)}), 500, headers)
