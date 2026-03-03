import os
import requests
import json
import argparse
from datetime import datetime
from notion_client import Client
from dotenv import load_dotenv

load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
# For local dev/test, point to your local backend
API_URL = os.getenv("BACKEND_URL", "http://localhost:8080/")

notion = Client(auth=NOTION_API_KEY)

def add_book_and_summarize(book_title, author=None):
    print(f"Creating Notion entry for: {book_title} {'by ' + author if author else ''}")
    
    # 1. Prepare properties
    properties = {
        "Name": {"title": [{"text": {"content": book_title}}]},
        "Date Added": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}}
    }
    
    # If author is provided, pre-fill it in Notion so the backend can use it
    if author:
        properties["Authors"] = {"rich_text": [{"text": {"content": author}}]}
    
    # Create the entry in Notion
    new_page = notion.pages.create(
        parent={"database_id": NOTION_DATABASE_ID},
        properties=properties
    )
    
    page_id = new_page["id"]
    print(f"Page created (ID: {page_id}). Sending to AI...")

    # 2. Trigger your backend with source="notion_button"
    payload = {
        "source": "notion_button",
        "page_id": page_id
    }
    
    try:
        response = requests.post(API_URL, json=payload, timeout=90)
        response.raise_for_status()
        print(f"Success! Book summarized and Notion updated.")
    except Exception as e:
        print(f"Error during AI analysis: {e}")
        print(f"Page ID {page_id} still exists, but you'll need to update it manually or retry.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add a book/chapter to Centaur Notes and summarize it.")
    parser.add_argument("title", help="The title of the book or chapter.")
    parser.add_argument("--author", help="Optional author to improve AI accuracy.")
    args = parser.parse_args()
    
    add_book_and_summarize(args.title, args.author)
