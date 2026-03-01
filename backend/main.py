import functions_framework
import json
import os
import io
import httpx
import fitz  # PyMuPDF
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
    """Downloads content from a URL and extracts text if it's a PDF."""
    try:
        print(f"Fetching URL for backend extraction: {url}")
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
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
            print(f"URL is not a PDF (Content-Type: {content_type}). Extraction failed.")
            return None
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

def call_vertex_ai(user_input, existing_categories, is_book=False):
    """Constructs prompt and calls Gemini 2.0 Flash to summarize content."""
    system_instruction = (
        "You are 'Centaur Notes', an expert AI summarising texts about AI, Defence, Tech, and Geopolitics. "
        "Output a JSON object: {title, top_3_points: [], summary: 'Markdown string', selected_categories: []}. "
        f"Prioritize these 'Existing Categories': {existing_categories}. "
        "Summary should be 200-400 words with rich Markdown (### for subheadings)."
    )
    
    prompt = f"Analyze this {'book title' if is_book else 'content'}: {user_input}"

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json"
        )
    )
    return json.loads(response.text)

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
            
            # If no text provided, try to fetch and extract from URL on backend
            if not markdown_text or markdown_text.strip() == "":
                markdown_text = fetch_and_extract_content(url)
                if not markdown_text:
                    return (json.dumps({"error": "Failed to extract text from this URL. Only articles and PDFs are supported."}), 400, headers)

            ai_data = call_vertex_ai(markdown_text, existing_categories)
            
            new_page = notion.pages.create(
                parent={"database_id": NOTION_DATABASE_ID},
                properties={
                    "Name": {"title": [{"text": {"content": ai_data.get("title", "Untitled")}}]},
                    "URL": {"url": url},
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
            
            ai_data = call_vertex_ai(title, existing_categories, is_book=True)
            
            notion.pages.update(
                page_id=page_id,
                properties={
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
