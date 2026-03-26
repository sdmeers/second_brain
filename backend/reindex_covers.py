import os
import re
import httpx
import yaml
from pathlib import Path
from dotenv import load_dotenv

# Re-use logic from main.py and add_book.py
load_dotenv()
OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")
SUMMARIES_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "Summaries")

def fetch_cover(url: str, is_youtube: bool, title: str = "", author: str = "", doc_type: str = "") -> str:
    if is_youtube or doc_type == "video":
        video_id = None
        if "v=" in url:
            video_id = url.split("v=")[1].split("&")[0]
        elif "youtu.be/" in url:
            video_id = url.split("youtu.be/")[1].split("?")[0]
        if video_id:
            return f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
    
    if doc_type == "book":
        try:
            query = f"intitle:{title}"
            if author: query += f"+inauthor:{author}"
            res = httpx.get("https://www.googleapis.com/books/v1/volumes", params={"q": query, "maxResults": 1}, timeout=10.0)
            if res.status_code == 200:
                data = res.json()
                if "items" in data:
                    img = data["items"][0].get("volumeInfo", {}).get("imageLinks", {})
                    return img.get("extraLarge") or img.get("large") or img.get("thumbnail") or ""
        except: pass

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = httpx.get(url, headers=headers, timeout=10.0, follow_redirects=True)
        if res.status_code == 200:
            match = re.search(r'<meta [^>]*property=["'']og:image["''][^>]*content=["''](.*?)["'']', res.text)
            if not match:
                match = re.search(r'<meta [^>]*content=["''](.*?)["''][^>]*property=["'']og:image["'']', res.text)
            if match: return match.group(1)
    except: pass
    return ""

def reindex():
    print(f"Starting re-index of {SUMMARIES_PATH}...")
    files = list(Path(SUMMARIES_PATH).glob("*.md"))
    
    for file_path in files:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        if not content.startswith("---"):
            continue
            
        # Extract YAML
        parts = content.split("---", 2)
        if len(parts) < 3: continue
        
        yaml_str = parts[1]
        body = parts[2]
        
        try:
            data = yaml.safe_load(yaml_str)
        except:
            print(f"Failed to parse YAML for {file_path.name}")
            continue
            
        url = data.get("url", "")
        doc_type = data.get("type", "")
        title = data.get("title", "")
        author = data.get("author", "")
        current_cover = data.get("cover", "")
        
        # Add missing fields
        modified = False
        if "status" not in data:
            data["status"] = "🟡 to-review"
            modified = True
        if "date_captured" not in data:
            data["date_captured"] = data.get("date_processed", "")
            modified = True
        
        # Only update if it's an emoji or empty
        if not current_cover or len(current_cover) < 10:
            print(f"Updating cover for: {file_path.name}...")
            new_cover = fetch_cover(url, "youtube.com" in url or "youtu.be" in url, title, author, doc_type)
            
            if new_cover:
                data["cover"] = new_cover
                modified = True
                print(f"  [SUCCESS] {new_cover[:50]}...")
            else:
                print(f"  [SKIPPED] No cover found.")
        
        if modified:
            new_yaml = yaml.dump(data, sort_keys=False, allow_unicode=True).strip()
            new_content = f"---\n{new_yaml}\n---{body}"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)


if __name__ == "__main__":
    reindex()
