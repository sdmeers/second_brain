import os
import re
import yaml
import asyncio
import time
import json
import hashlib
import sqlite3
import httpx
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Existing relative imports
from backend.logger import log_action
from google import genai
from google.genai import types

from backend.vault.writer import atomic_write
from backend.rag.vectorstore import index_vault
from backend.config import TELEGRAM_QA_BOT_TOKEN


dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=dotenv_path)
VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
from backend.config import (
    LLM_PROVIDER, LLM_MODEL,
    EMBEDDING_PROVIDER, EMBEDDING_MODEL,
    OPENAI_API_KEY, OLLAMA_BASE_URL,
    GEMINI_MODEL
)

if not VAULT_PATH:
    raise RuntimeError("Missing OBSIDIAN_VAULT_PATH in config")

SUMMARIES_DIR = os.path.join(VAULT_PATH, "04_Resources", "Research_Brain", "Summaries")
ATLAS_DIR = os.path.join(VAULT_PATH, "04_Resources", "Research_Brain", "Atlas")
CONCEPTS_DIR = os.path.join(VAULT_PATH, "04_Resources", "Research_Brain", "Concepts")

client = None
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        pass

class SyncMockResponse:
    def __init__(self, text):
        self.text = text

def call_gemini_with_retry(model, contents, config=None, max_retries=5):
    """Synchronous wrapper to call LLM API with exponential backoff, supporting multiple providers."""
    from backend.config import LLM_PROVIDER
    if LLM_PROVIDER != "gemini":
        import openai
        from backend.config import OPENAI_API_KEY, OLLAMA_BASE_URL
        
        client_to_use = None
        if LLM_PROVIDER == "openai":
            client_to_use = openai.OpenAI(api_key=OPENAI_API_KEY)
        elif LLM_PROVIDER == "ollama":
            client_to_use = openai.OpenAI(base_url=f"{OLLAMA_BASE_URL}/v1", api_key="ollama")
        else:
            raise ValueError(f"Unsupported LLM_PROVIDER: {LLM_PROVIDER}")
            
        system_instruction = None
        temperature = 0.1
        response_schema = None
        
        if config:
            system_instruction = getattr(config, "system_instruction", None)
            temperature = getattr(config, "temperature", 0.1)
            response_schema = getattr(config, "response_schema", None)
            
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": str(contents)})
        
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature
        }
        if response_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "schema": response_schema.model_json_schema()
                }
            }
            
        for attempt in range(max_retries):
            try:
                response = client_to_use.chat.completions.create(**kwargs)
                text = response.choices[0].message.content
                return SyncMockResponse(text)
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "503" in error_str or "rate_limit" in error_str.lower():
                    wait_time = (2 ** attempt) + 2
                    print(f"Cleaner [LLM]: Encountered rate limit or server error. Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(wait_time)
                else:
                    raise e
        raise RuntimeError(f"Failed to generate completion after {max_retries} attempts.")

    # Original Gemini implementation
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "503" in error_str:
                wait_time = (2 ** attempt) + 2  # 3s, 4s, 6s, 10s, 18s
                print(f"  [RETRYING] Encountered {error_str[:3]} error. Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Failed after {max_retries} attempts: {error_str}")
            else:
                raise e

MAX_API_CALLS_PER_RUN = 250
STATE_FILE = os.path.join(os.path.dirname(__file__), ".brain_cleaner_state.json")

# Canonical Mapping
MAP = {
    r"#agi\b": "[[Artificial General Intelligence]]",
    r"\[\[AGI\]\]": "[[Artificial General Intelligence]]",
    r"#ai\b": "[[Artificial Intelligence]]",
    r"#artificial_intelligence\b": "[[Artificial Intelligence]]",
    r"#artificial-intelligence\b": "[[Artificial Intelligence]]",
    r"\[\[AI\]\]": "[[Artificial Intelligence]]",
    r"#ethics\b": "[[Ethics]]",
    r"\[\[Ethical Artificial Intelligence\]\]": "[[Ethics]]",
    r"\[\[Ethical AI\]\]": "[[Ethics]]",
    r"#geopolitics\b": "[[Geopolitics]]",
    r"\[\[LLM\]\]": "[[Large Language Model]]",
    r"#cybersecurity\b": "[[Cybersecurity]]"
}

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"processed_groups": [], "refactored_hashes": {}}


def save_state(state):
    temp_file = f"{STATE_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(temp_file, STATE_FILE)
    except Exception as e:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass
        raise e

def calculate_hash(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()

def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r'\(.*?\)', '', t)
    t = t.replace('-', ' ')
    t = re.sub(r'[^\w\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    if t.endswith('s') and not t.endswith('ss'):
        t = t[:-1]
    return t

def clean_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    match = re.search(r'---(.*?)---', content, re.DOTALL)
    if not match: return
    
    yaml_block = match.group(1)
    body = content.split("---", 2)[2]
    
    try:
        data = yaml.safe_load(yaml_block)
    except: return

    old_tags = data.get("tags", [])
    new_tags = ["brain"]
    doc_type = data.get("type", "article")
    new_tags.append(doc_type)
    
    topics_to_add = []
    for t in old_tags:
        if t in ["brain", "article", "video", "paper", "book", "report"]:
            continue
        clean_t = t.replace("_", " ").replace("-", " ").title()
        topics_to_add.append(f"[[{clean_t}]]")

    data["tags"] = new_tags
    new_body = body.strip()
    new_body = re.sub(r'^#\s*\[\[.*?\]\]\s*', '', new_body, flags=re.MULTILINE)
    new_body = re.sub(r'^#\s*.*?\n', '', new_body, count=1)
    
    for pattern, replacement in MAP.items():
        new_body = re.sub(pattern, replacement, new_body, flags=re.IGNORECASE)
    
    if topics_to_add:
        unique_topics = [t for t in set(topics_to_add) if t not in new_body]
        if unique_topics:
            new_body = new_body.strip() + "\n\n**Topics:** " + ", ".join(unique_topics) + "\n"

    new_yaml = yaml.dump(data, sort_keys=False, allow_unicode=True).strip()
    new_content = f"---\n{new_yaml}\n---\n\n{new_body.strip()}"
    
    if new_content.strip() != content.strip():
        atomic_write(file_path, new_content)
        print(f"  [FIXED] {file_path.name}")

def extract_snippets(content: str, link: str, snippet_length: int = 150) -> list[str]:
    snippets = []
    pattern = re.compile(rf'.{{0,{snippet_length}}}\[\[{re.escape(link)}(?:\|.*?)?\]\].{{0,{snippet_length}}}', re.IGNORECASE)
    for match in pattern.finditer(content):
        snippets.append(match.group(0).strip().replace('\n', ' '))
    return snippets

def run_brain_cleaner():
    print(f"🧹 Brain cleaner starting...")
    api_calls = 0
    state = load_state()
    
    # 0. Deduplication (Phase 2)
    print("🔍 Scanning for duplicate concepts...")
    concept_files = list(Path(CONCEPTS_DIR).glob("*.md"))
    groups = {}
    for f in concept_files:
        norm = normalize_title(f.stem)
        if norm not in groups:
            groups[norm] = []
        groups[norm].append(f)
        
    duplicates = {k: v for k, v in groups.items() if len(v) > 1}
    processed_groups = set(state.get("processed_groups", []))
    
    dedup_count = 0
    if duplicates:
        for norm, files in duplicates.items():
            if api_calls >= MAX_API_CALLS_PER_RUN:
                print("⏳ Reached API limit. Stopping early.")
                return
            if norm in processed_groups:
                continue
                
            print(f"  [DEDUPLICATING] Group '{norm}' containing {len(files)} files:")
            canonical_file = min(files, key=lambda x: (1 if '(' in x.stem else 0, len(x.stem)))
            canonical_stem = canonical_file.stem
            
            combined_content = ""
            for f in files:
                print(f"    - {f.name}")
                with open(f, 'r', encoding='utf-8') as file_obj:
                    combined_content += f"\n\n--- Content from {f.name} ---\n\n"
                    combined_content += file_obj.read()
                    
            prompt = f"""You are an expert knowledge manager. Merge the following concept pages into a single, comprehensive canonical page.
You must retain all unique insights, theories, related concepts, and source links (e.g., `[[📄 Source Name]]`). 
Resolve any contradictions gracefully, eliminate repetition, and structure the output with clear Markdown headings. 
Do not output anything outside of the Markdown content.

Here are the concept pages to merge:
{combined_content}
"""
            try:
                response = call_gemini_with_retry(
                    model=GEMINI_MODEL,
                    contents=prompt
                )
                api_calls += 1
                merged_content = response.text.strip()
                
                discarded_stems = [f.stem for f in files if f != canonical_file]
                
                # Check for YAML frontmatter
                match = re.search(r'^---\n(.*?)\n---', merged_content, re.DOTALL)
                if match:
                    yaml_block = match.group(1)
                    try:
                        data = yaml.safe_load(yaml_block) or {}
                    except:
                        data = {}
                    body = merged_content[match.end():].strip()
                else:
                    data = {}
                    body = merged_content
                    
                data['aliases'] = data.get('aliases', []) + discarded_stems
                data['aliases'] = list(set(data['aliases']))
                
                new_yaml = yaml.dump(data, sort_keys=False, allow_unicode=True).strip()
                final_content = f"---\n{new_yaml}\n---\n\n{body}"
                
                atomic_write(canonical_file, final_content)
                    
                # Vault-Wide Link Healing
                all_markdown_files = list(Path(SUMMARIES_DIR).glob("*.md")) + \
                                     list(Path(ATLAS_DIR).glob("*.md")) + \
                                     list(Path(CONCEPTS_DIR).glob("*.md"))
                for md_file in all_markdown_files:
                    with open(md_file, 'r', encoding='utf-8') as read_f:
                        text = read_f.read()
                    new_text = text
                    for old_stem in discarded_stems:
                        pattern = re.compile(rf'\[\[{re.escape(old_stem)}(?:\|(.*?))?\]\]')
                        def repl(m, c_stem=canonical_stem, o_stem=old_stem):
                            alias = m.group(1)
                            if alias:
                                return f"[[{c_stem}|{alias}]]"
                            else:
                                return f"[[{c_stem}|{o_stem}]]"
                        new_text = pattern.sub(repl, new_text)
                    if new_text != text:
                        atomic_write(md_file, new_text)
                            
                for f in files:
                    if f != canonical_file:
                        os.remove(f)
                        
                print(f"  [MERGED] Created canonical '{canonical_stem}.md' and healed links.")
                log_action("Deduplicate", f"Merged {discarded_stems} into {canonical_stem}")
                dedup_count += 1
                
                processed_groups.add(norm)
                state["processed_groups"] = list(processed_groups)
                save_state(state)
                
                time.sleep(4)
            except Exception as e:
                print(f"  [ERROR] Failed to merge group '{norm}': {e}")
    else:
        print("  ✅ No duplicates found.")

    # 1. Clean existing summaries
    print("🧹 Cleaning existing summaries...")
    files = list(Path(SUMMARIES_DIR).glob("*.md"))
    for f in files:
        clean_file(f)
        
    # 2. Orphan Link Detection
    print("📍 Detecting Missing Link Snippets...")
    all_markdown_files = list(Path(SUMMARIES_DIR).glob("*.md")) + list(Path(CONCEPTS_DIR).glob("*.md"))
    
    links_data = {} # link -> {'count': int, 'snippets': list[str]}
    
    for f in all_markdown_files:
        with open(f, 'r', encoding='utf-8') as file:
            content = file.read()
            links = re.findall(r'\[\[(.*?)\]\]', content)
            for raw_link in links:
                topic = raw_link.split('|')[0].strip()
                if not topic or topic.endswith(('.pdf', '.md', '_raw')):
                    continue
                    
                if topic not in links_data:
                    links_data[topic] = {'count': 0, 'snippets': []}
                
                links_data[topic]['count'] += 1
                if len(links_data[topic]['snippets']) < 5:
                    snippets = extract_snippets(content, topic)
                    links_data[topic]['snippets'].extend(snippets)

    # Check which links are missing a file in CONCEPTS_DIR, ATLAS_DIR, or SUMMARIES_DIR
    missing_concepts = {}
    
    for topic, data in links_data.items():
        # Skip source/summary links (links starting with emojis or non-alphanumeric chars)
        if not topic or not topic[0].isalnum():
            continue
            
        safe_topic = re.sub(r'[\\/*?:"<>|]', "", topic).strip()
        concept_path = os.path.join(CONCEPTS_DIR, f"{safe_topic}.md")
        atlas_path = os.path.join(ATLAS_DIR, f"{safe_topic}.md")
        summary_path = os.path.join(SUMMARIES_DIR, f"{safe_topic}.md")
        
        if not any(os.path.exists(p) for p in [concept_path, atlas_path, summary_path]):
            missing_concepts[topic] = data
            
    # 3. Auto-Healing
    # Sort missing concepts by count descending
    sorted_missing = sorted(missing_concepts.items(), key=lambda x: x[1]['count'], reverse=True)
    top_n = min(5, len(sorted_missing))
    
    manifested_count = 0
    if top_n > 0:
        print(f"🏥 Auto-healing top {top_n} missing concepts...")
        for topic, data in sorted_missing[:top_n]:
            if api_calls >= MAX_API_CALLS_PER_RUN:
                print("⏳ Reached API limit. Stopping early.")
                return
            print(f"  [HEALING] [[{topic}]] ({data['count']} mentions)")
            safe_topic = re.sub(r'[\\/*?:"<>|]', "", topic).strip()
            concept_path = os.path.join(CONCEPTS_DIR, f"{safe_topic}.md")
            
            snippets_str = "\n".join([f"- \"{s}\"" for s in data['snippets'][:5]])
            prompt = f"""You are building a 'Second Brain'. 
Generate a concept page for '{topic}' based on its usage in the following context snippets from the user's vault:

{snippets_str}

Please return the content formatted as Markdown. Include a definition, and synthesize how it's being used based on the snippets."""
            
            try:
                response = call_gemini_with_retry(
                    model=GEMINI_MODEL,
                    contents=prompt
                )
                api_calls += 1
                atomic_write(concept_path, response.text.strip())
                manifested_count += 1
                time.sleep(4)
            except Exception as e:
                print(f"  [ERROR] Failed to heal [[{topic}]]: {e}")
                
    # 4. Contradiction/Synthesis Scan
    print("🔍 Scanning for large concepts to refactor...")
    refactored_count = 0
    concept_files = list(Path(CONCEPTS_DIR).glob("*.md"))
    refactored_hashes = state.get("refactored_hashes", {})
    
    for f in concept_files:
        if api_calls >= MAX_API_CALLS_PER_RUN:
            print("⏳ Reached API limit. Stopping early.")
            break
            
        with open(f, 'r', encoding='utf-8') as file:
            content = file.read()
            
        current_hash = calculate_hash(content)
        last_hash = refactored_hashes.get(f.name)
        
        # Only refactor if large AND the content has changed since the last refactor
        if os.path.getsize(f) > 2000 and current_hash != last_hash:
            print(f"  [REFACTORING] {f.name}")
            
            prompt = f"""Refactor and organize this concept page. 

CRITICAL INSTRUCTIONS:
1. PRESERVE NUANCE: Do not 'smooth out' the content. Maintain all specific terminology, unique theories, and internal contradictions discovered across different sources.
2. GRAPH INTEGRITY: Maintain all existing wikilinks [[Topic]] and source links [[📄 Source Name]].
3. STRUCTURE: Improve readability with clear Markdown headings (##, ###).
4. NO DELETIONS: Do not remove information unless it is an exact duplicate of another paragraph in the same file.

Here is the concept page content:
{content}
"""
            try:
                response = call_gemini_with_retry(
                    model=GEMINI_MODEL,
                    contents=prompt
                )
                api_calls += 1
                new_content = response.text.strip()
                
                atomic_write(f, new_content)
                
                # Store the hash of the NEW content so we don't refactor it again until it changes
                refactored_hashes[f.name] = calculate_hash(new_content)
                state["refactored_hashes"] = refactored_hashes
                save_state(state)
                
                refactored_count += 1
                time.sleep(4)
            except Exception as e:
                print(f"  [ERROR] Failed to refactor {f.name}: {e}")
                
    # 5. Orphan Detection
    print("🕸️ Scanning for Orphaned Concepts...")
    all_summaries_and_atlas = list(Path(SUMMARIES_DIR).glob("*.md")) + list(Path(ATLAS_DIR).glob("*.md"))
    active_links = set()

    for f in all_summaries_and_atlas:
        with open(f, 'r', encoding='utf-8') as file:
            content = file.read()
            links = re.findall(r'\[\[(.*?)\]\]', content)
            for raw_link in links:
                topic = raw_link.split('|')[0].strip()
                active_links.add(topic)

    concept_files = list(Path(CONCEPTS_DIR).glob("*.md"))
    orphans = []
    for f in concept_files:
        concept_name = f.stem
        if concept_name not in active_links:
            orphans.append(concept_name)

    if orphans:
        print(f"📍 Found {len(orphans)} orphaned concepts (manually review in 04 Concepts/):")
        for o in sorted(orphans):
            print(f"  - [[{o}]]")
    else:
        print("✅ No orphaned concepts found.")

    print("✨ Vault clean-up, auto-healing, and refactoring complete.")
    log_action("Lint", f"Brain cleaner pass: {dedup_count} deduped, {manifested_count} concepts healed, {refactored_count} refactored, {len(orphans)} orphans detected.")



async def run_sqlite_checkpoint():
    print("🗄️ Checkpointing sqlite databases...")
    for db_name in ["second_brain.db", "brain_vectors.db"]:
        db_path = os.path.join(os.path.dirname(__file__), db_name)
        if os.path.exists(db_path):
            try:
                with sqlite3.connect(db_path) as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as e:
                print(f"Failed to checkpoint {db_name}: {e}")

async def clean_staging_and_inbox():
    print("🗑️ Cleaning staging and inbox...")
    # Staging
    staging_dir = os.path.join(os.path.dirname(__file__), "staging")
    if os.path.exists(staging_dir):
        for f in os.listdir(staging_dir):
            p = os.path.join(staging_dir, f)
            if os.path.isfile(p):
                os.remove(p)
    # Inbox
    inbox_dir = os.path.join(VAULT_PATH, "00_System", "Inbox")
    keep = {"Inbox.md", "Completed.md", "Failed.md"}
    if os.path.exists(inbox_dir):
        for f in os.listdir(inbox_dir):
            if f not in keep:
                p = os.path.join(inbox_dir, f)
                if os.path.isfile(p):
                    os.remove(p)

async def auto_update_index():
    print("🗂️ Updating _INDEX.md...")
    projects_dir = os.path.join(VAULT_PATH, "02_Projects")
    areas_dir = os.path.join(VAULT_PATH, "03_Areas")
    
    projects = [d for d in os.listdir(projects_dir) if os.path.isdir(os.path.join(projects_dir, d)) and not d.startswith(('.', '_'))] if os.path.exists(projects_dir) else []
    areas = [d for d in os.listdir(areas_dir) if os.path.isdir(os.path.join(areas_dir, d)) and not d.startswith(('.', '_'))] if os.path.exists(areas_dir) else []
    
    # Auto-generate Dataview hub note for any new Area folder missing its .md file
    for area_name in areas:
        area_folder = os.path.join(areas_dir, area_name)
        hub_note = os.path.join(area_folder, f"_{area_name}.md")
        if not os.path.exists(hub_note):
            hub_content = f"""---
title: "{area_name} Area Dashboard"
type: area
tags: [area_index]
---
# 📁 {area_name}

> Operational notes and documents for the {area_name} area.

---

## 📁 Area Notes & Documents

```dataview
TABLE file.mtime AS "Last Modified"
FROM "03_Areas/{area_name}"
WHERE file.name != "_{area_name}"
SORT file.mtime DESC
```
"""
            try:
                atomic_write(hub_note, hub_content)
                print(f"Auto-generated Dataview Hub note for new area: _{area_name}.md")
            except Exception as e:
                print(f"Failed to create hub note for area {area_name}: {e}")

    index_file = os.path.join(VAULT_PATH, "00_System", "_INDEX.md")
    if not os.path.exists(index_file):
        return
        
    with open(index_file, "r", encoding="utf-8") as f:
        content = f.read()
        
    proj_text = "## 02_Projects/\n" + "\n".join(f"- `{p}`" for p in sorted(projects)) + "\n"
    area_text = "## 03_Areas/\n" + "\n".join(f"- `{a}`" for a in sorted(areas)) + "\n"
    
    # Replace sections
    content = re.sub(r'## 02_Projects/.*?(?=## |\\Z)', proj_text + "\n", content, flags=re.DOTALL)
    content = re.sub(r'## 03_Areas/.*?(?=## |\\Z)', area_text + "\n", content, flags=re.DOTALL)
    
    atomic_write(index_file, content)

async def cleanup_old_audio():
    print("🎧 Cleaning old raw audio files...")
    cutoff = datetime.now() - timedelta(days=30)
    for root, dirs, files in os.walk(VAULT_PATH):
        for f in files:
            if "_raw.ogg" in f or "_raw." in f:
                p = os.path.join(root, f)
                try:
                    if datetime.fromtimestamp(os.path.getmtime(p)) < cutoff:
                        os.remove(p)
                except Exception:
                    pass

async def generate_daily_digest(target_chat_id: int = None):
    print("🌅 Generating daily digest...")
    from backend.utils.holiday_manager import is_scope_paused
    
    all_paused, all_date = is_scope_paused("all")
    if all_paused and not target_chat_id:
        print(f"🌴 Holiday Mode active (all reviews paused until {all_date}). Skipping daily digest.")
        return None

    pers_paused, pers_date = is_scope_paused("personal")
    prof_paused, prof_date = is_scope_paused("professional")

    today_dt = datetime.now()
    today_str = today_dt.strftime("%Y-%m-%d")
    is_weekend = today_dt.weekday() >= 5
    
    # --- Part 1: GTD Daily Review ---
    captured_file = os.path.join(VAULT_PATH, "01_Tasks", "Captured.md")
    next_file = os.path.join(VAULT_PATH, "01_Tasks", "Next_Actions.md")
    waiting_file = os.path.join(VAULT_PATH, "01_Tasks", "Waiting_For.md")
    
    unclarified_count = 0
    if os.path.exists(captured_file):
        with open(captured_file, "r", encoding="utf-8") as f:
            unclarified_count = sum(1 for line in f if line.strip().startswith("- "))
            
    active_next_actions = []
    active_actions_by_context = {}
    tickler_surfaced = []
    active_projects = set()
    
    if os.path.exists(next_file):
        with open(next_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip().startswith("- [ ]"): continue
                
                if is_weekend and "#professional" in line.lower(): continue
                if prof_paused and "#professional" in line.lower(): continue
                if pers_paused and "#personal" in line.lower(): continue
                
                clean_line = re.sub(r'\(Captured .*?\)', '', line)
                clean_line = re.sub(r'\(Source:.*?\)', '', clean_line).strip("- [ ]\n").strip()

                m_tick = re.search(r'🔔 (\d{4}-\d{2}-\d{2})', line)
                if m_tick:
                    tick_date = m_tick.group(1)
                    if tick_date > today_str: continue # hidden
                    elif tick_date == today_str: tickler_surfaced.append(clean_line)
                
                active_next_actions.append(clean_line)
                
                m_ctx = re.search(r'(@[a-zA-Z0-9_\-]+)', clean_line)
                ctx_name = m_ctx.group(1) if m_ctx else "@other"
                
                if ctx_name not in active_actions_by_context:
                    active_actions_by_context[ctx_name] = []
                active_actions_by_context[ctx_name].append(clean_line)
                
                m_proj = re.search(r'#project/([a-zA-Z0-9_\-]+)', line)
                if m_proj: active_projects.add(m_proj.group(1))

    waiting_items = []
    stale_waitings = []
    if os.path.exists(waiting_file):
        with open(waiting_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip().startswith("- [ ]"): continue
                
                if is_weekend and "#professional" in line.lower(): continue
                if prof_paused and "#professional" in line.lower(): continue
                if pers_paused and "#personal" in line.lower(): continue

                m_tick = re.search(r'🔔 (\d{4}-\d{2}-\d{2})', line)
                if m_tick and m_tick.group(1) > today_str: continue
                
                clean_line = re.sub(r'\(Captured .*?\)', '', line)
                clean_line = re.sub(r'\(Source:.*?\)', '', clean_line).strip("- [ ]\n").strip()
                waiting_items.append(clean_line)
                
                m_cap = re.search(r'\(Captured .*? on (\d{4}-\d{2}-\d{2})\)', line)
                if m_cap:
                    cap_date = datetime.strptime(m_cap.group(1), "%Y-%m-%d")
                    if (today_dt - cap_date).days > 5:
                        stale_waitings.append(clean_line)
                
                m_proj = re.search(r'#project/([a-zA-Z0-9_\-]+)', line)
                if m_proj: active_projects.add(m_proj.group(1))

    orphaned_projects = []
    project_deadlines = []
    proj_dir = os.path.join(VAULT_PATH, "02_Projects")
    for root, dirs, files in os.walk(proj_dir):
        if is_weekend and "Professional" in root: continue
        for f in files:
            if not f.endswith(".md"): continue
            proj_name = f.replace(".md", "")
            if proj_name.replace(" ", "_") not in active_projects:
                orphaned_projects.append(proj_name)

            p_file = os.path.join(root, f)
            try:
                with open(p_file, "r", encoding="utf-8") as pf:
                    p_text = pf.read()
                m_status = re.search(r'status:\s*"?([a-zA-Z0-9_\-]+)"?', p_text)
                if m_status and m_status.group(1).lower() in ["completed", "archived"]:
                    continue
                
                m_due = re.search(r'due_date:\s*"?(\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4})"?', p_text)
                if m_due:
                    due_raw = m_due.group(1)
                    if "-" in due_raw and len(due_raw.split("-")[0]) == 4:
                        due_dt = datetime.strptime(due_raw, "%Y-%m-%d").date()
                    else:
                        due_dt = datetime.strptime(due_raw, "%d-%m-%Y").date()
                    
                    days_left = (due_dt - today_dt.date()).days
                    date_disp = due_dt.strftime("%d-%m-%Y")
                    
                    if days_left <= 14:
                        if days_left < 0:
                            project_deadlines.append(f"⚠️ **{proj_name}** — OVERDUE by {abs(days_left)} day(s) ({date_disp})")
                        elif days_left == 0:
                            project_deadlines.append(f"🚨 **{proj_name}** — DUE TODAY! ({date_disp})")
                        else:
                            project_deadlines.append(f"🚨 **{proj_name}** — Due in {days_left} day(s) ({date_disp})")
            except Exception:
                pass
    
    # --- Part 2: Recent Brain Activity (Last 24h) ---
    yesterday = today_dt - timedelta(days=1)
    recent_activity = []
    for root, dirs, files in os.walk(VAULT_PATH):
        if is_weekend and "Professional" in root: continue
        rel_root = os.path.relpath(root, VAULT_PATH).replace("\\", "/")
        if not (rel_root.startswith("04_Resources") or rel_root.startswith("03_Areas") or rel_root.startswith("02_Projects")):
            continue
            
        for f in files:
            if f.endswith(".md"):
                p = os.path.join(root, f)
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(p))
                    ctime = datetime.fromtimestamp(os.path.getctime(p))
                    if mtime > yesterday or ctime > yesterday:
                        recent_activity.append(f"{rel_root}/{f}")
                except Exception: pass

    # Assemble Digest
    digest_content = f"# Daily Digest - {today_str}\n\n"
    
    digest_content += "## ⚡ Active Next Actions\n"
    if active_actions_by_context:
        for ctx_name, item_list in active_actions_by_context.items():
            digest_content += f"### {ctx_name}\n"
            digest_content += "\n".join(f"- {it}" for it in item_list) + "\n\n"
    else:
        digest_content += "*No active next actions.*\n\n"

    if project_deadlines:
        digest_content += "## 🚨 Upcoming Project Deadlines\n"
        digest_content += "\n".join(f"- {dl}" for dl in project_deadlines) + "\n\n"

    if waiting_items:
        digest_content += "## ⏳ Waiting For Items\n"
        digest_content += "\n".join(f"- {it}" for it in waiting_items) + "\n\n"

    if tickler_surfaced:
        digest_content += "## 🔔 Tickler Items Surfaced Today\n"
        digest_content += "\n".join(f"- {item}" for item in tickler_surfaced) + "\n\n"

    digest_content += "## 📊 Daily Summary\n"
    digest_content += f"- Unclarified Captured Items: {unclarified_count}\n"
    digest_content += f"- Active Next Actions: {len(active_next_actions)}\n"
    digest_content += f"- Waiting For Items: {len(waiting_items)}\n"
    digest_content += f"- Orphaned Projects: {len(orphaned_projects)}\n\n"

    digest_content += "## 📁 Recent Brain Activity (Last 24h)\n"
    if recent_activity:
        digest_content += "\n".join(f"- `{item}`" for item in recent_activity) + "\n\n"
    else:
        digest_content += "*No new vault activity in the last 24 hours.*\n\n"

    # Save Markdown files
    digest_path = os.path.join(VAULT_PATH, "00_System", "Internal", "Digests", f"{today_str}.md")
    os.makedirs(os.path.dirname(digest_path), exist_ok=True)
    atomic_write(digest_path, digest_content)
    
    daily_digest_path = os.path.join(VAULT_PATH, "00_System", "Daily_Digest.md")
    atomic_write(daily_digest_path, digest_content)
    
    from backend.config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_IDS
    if TELEGRAM_BOT_TOKEN and (TELEGRAM_ALLOWED_CHAT_IDS or target_chat_id):
        if target_chat_id:
            chat_id = target_chat_id
        elif isinstance(TELEGRAM_ALLOWED_CHAT_IDS, list):
            chat_id = TELEGRAM_ALLOWED_CHAT_IDS[0] if TELEGRAM_ALLOWED_CHAT_IDS else None
        else:
            chat_id = str(TELEGRAM_ALLOWED_CHAT_IDS).split(",")[0].strip()
            
        summary_lines = [f"🌅 **Daily Review ({today_str})**\n"]
        if project_deadlines:
            summary_lines.append("🚨 **Upcoming Deadlines:**")
            summary_lines.extend(f"• {dl.replace('**', '')}" for dl in project_deadlines)
            summary_lines.append("")

        if tickler_surfaced:
            summary_lines.append(f"🔔 **Surfaced Deferrals ({len(tickler_surfaced)}):**")
            summary_lines.extend(f"• {it}" for it in tickler_surfaced)
            summary_lines.append("")
            
        if active_actions_by_context:
            summary_lines.append(f"⚡ **Next Actions ({len(active_next_actions)}):**")
            for ctx_name, item_list in active_actions_by_context.items():
                summary_lines.append(f"**{ctx_name}**")
                summary_lines.extend(f"• {it}" for it in item_list[:5])
            summary_lines.append("")
            
        if waiting_items:
            summary_lines.append(f"⏳ **Waiting For ({len(waiting_items)}):**")
            summary_lines.extend(f"• {it}" for it in waiting_items[:5])
            summary_lines.append("")

        if unclarified_count > 0:
            summary_lines.append(f"📥 **Unclarified Items:** {unclarified_count} awaiting /clarify")

        summary_text = "\n".join(summary_lines)
        try:
            from backend.ingestion.telegram import send_telegram_message_safe
            async with httpx.AsyncClient() as client:
                await send_telegram_message_safe(client, TELEGRAM_BOT_TOKEN, chat_id, summary_text)
        except Exception as e:
            print(f"Failed to send digest via Telegram: {e}")

    return digest_content

async def generate_sitrep_summary() -> str:
    """Generates or retrieves the latest sitrep / daily digest summary string."""
    digest_path = os.path.join(VAULT_PATH, "00_System", "Daily_Digest.md")
    if os.path.exists(digest_path):
        with open(digest_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        return await generate_daily_digest()

async def generate_weekly_review():
    print("🗓️ Generating Friday 14:00 Weekly Review...")
    from backend.utils.holiday_manager import is_scope_paused
    
    all_paused, all_date = is_scope_paused("all")
    if all_paused:
        print(f"🌴 Holiday Mode active (all reviews paused until {all_date}). Skipping weekly review.")
        return None

    today_dt = datetime.now()
    today_str = today_dt.strftime("%Y-%m-%d")
    seven_days_ago = today_dt - timedelta(days=7)
    
    def get_clean_lines(filepath: str, is_checkbox: bool = True) -> list:
        if not os.path.exists(filepath): return []
        items = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line_s = line.strip()
                match_condition = (line_s.startswith("- [ ]") or line_s.startswith("- [x]")) if is_checkbox else line_s.startswith("- ")
                if match_condition:
                    clean = re.sub(r'\(Source:.*?\)', '', line_s)
                    clean = re.sub(r'\(Captured on.*?\)', '', clean)
                    clean = clean.replace('- [ ]', '').replace('- [x]', '').replace('- ', '').strip()
                    if clean:
                        items.append(clean)
        return items

    captured_items = get_clean_lines(os.path.join(VAULT_PATH, "01_Tasks", "Captured.md"), is_checkbox=False)
    next_items = get_clean_lines(os.path.join(VAULT_PATH, "01_Tasks", "Next_Actions.md"))
    waiting_items = get_clean_lines(os.path.join(VAULT_PATH, "01_Tasks", "Waiting_For.md"))
    someday_items = get_clean_lines(os.path.join(VAULT_PATH, "01_Tasks", "Someday_Maybe.md"))

    # Active project tags search
    active_project_tags = set()
    next_file = os.path.join(VAULT_PATH, "01_Tasks", "Next_Actions.md")
    if os.path.exists(next_file):
        with open(next_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("- [ ]"):
                    m_proj = re.search(r'#project/([a-zA-Z0-9_\-]+)', line)
                    if m_proj: active_project_tags.add(m_proj.group(1).lower())

    # 1. Top-Level Projects Scanning (no deep subfolder recursion)
    proj_dir = os.path.join(VAULT_PATH, "02_Projects")
    top_level_projects = []
    
    for category in ["Personal", "Professional"]:
        cat_dir = os.path.join(proj_dir, category)
        if os.path.exists(cat_dir):
            for entry in sorted(os.listdir(cat_dir)):
                if entry.startswith(".") or entry.startswith("_"): continue
                entry_path = os.path.join(cat_dir, entry)
                if os.path.isdir(entry_path) or entry.endswith(".md"):
                    p_name = entry.replace(".md", "")
                    top_level_projects.append((p_name, category, entry_path))

    orphaned_projects = []
    project_roadmaps = {}

    for p_name, category, p_path in top_level_projects:
        p_tag_clean = p_name.strip().replace(" ", "_").lower()
        if p_tag_clean not in active_project_tags and p_name.lower() not in active_project_tags:
            orphaned_projects.append(f"{p_name} ({category})")
            
        # Extract roadmap steps from master note
        note_md = p_path if p_path.endswith(".md") else os.path.join(p_path, f"{p_name}.md")
        if os.path.exists(note_md):
            with open(note_md, "r", encoding="utf-8") as f:
                c = f.read()
            m_steps = re.search(r'## Project Steps\n(.*?)(?=\n## |\Z)', c, re.DOTALL)
            if m_steps:
                r_steps = [s.strip("- \n") for s in m_steps.group(1).strip().splitlines() if s.strip()]
                if r_steps:
                    project_roadmaps[p_name] = r_steps

    # Stale Delegations (>5 days)
    stale_waitings = []
    waiting_file = os.path.join(VAULT_PATH, "01_Tasks", "Waiting_For.md")
    if os.path.exists(waiting_file):
        with open(waiting_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("- [ ]"):
                    m_cap = re.search(r'\(Captured .*? on (\d{4}-\d{2}-\d{2})\)', line)
                    if m_cap:
                        cap_date = datetime.strptime(m_cap.group(1), "%Y-%m-%d")
                        if (today_dt - cap_date).days > 5:
                            stale_waitings.append(line.strip())

    # 2. Part 2: Ingested & Vault Activity (Last 7 Days)
    recent_activity_by_folder = {}
    for root, dirs, files in os.walk(VAULT_PATH):
        rel_root = os.path.relpath(root, VAULT_PATH).replace("\\", "/")
        if not (rel_root.startswith("02_Projects") or rel_root.startswith("03_Areas") or rel_root.startswith("04_Resources")):
            continue
            
        for f in files:
            if f.endswith(".md") or f.endswith(".pdf") or f.endswith(".pptx"):
                p = os.path.join(root, f)
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(p))
                    if mtime > seven_days_ago:
                        folder_key = rel_root.split("/")[0] + "/" + (rel_root.split("/")[1] if len(rel_root.split("/")) > 1 else "")
                        if folder_key not in recent_activity_by_folder:
                            recent_activity_by_folder[folder_key] = []
                        recent_activity_by_folder[folder_key].append(f)
                except Exception: pass

    # Assemble Markdown Review Document
    review_md = f"# 🗓️ Weekly Review & System Status — {today_str}\n\n"
    
    review_md += "## Part 1: All Action Items Across System\n\n"
    
    review_md += f"### 📥 Captured Items ({len(captured_items)})\n"
    if captured_items:
        review_md += "\n".join(f"- {i}" for i in captured_items) + "\n\n"
    else:
        review_md += "- *None (Inbox zero!)*\n\n"

    review_md += f"### ⚡ Next Actions ({len(next_items)})\n"
    if next_items:
        review_md += "\n".join(f"- {i}" for i in next_items) + "\n\n"
    else:
        review_md += "- *None*\n\n"

    review_md += f"### ⏳ Waiting For ({len(waiting_items)})\n"
    if waiting_items:
        review_md += "\n".join(f"- {i}" for i in waiting_items) + "\n\n"
    else:
        review_md += "- *None*\n\n"

    review_md += f"### 💡 Someday / Maybe ({len(someday_items)})\n"
    if someday_items:
        review_md += "\n".join(f"- {i}" for i in someday_items) + "\n\n"
    else:
        review_md += "- *None*\n\n"

    review_md += f"### 📌 Project Roadmaps ({len(project_roadmaps)} Active Projects)\n"
    if project_roadmaps:
        for p_name, r_steps in project_roadmaps.items():
            review_md += f"**{p_name}:**\n"
            review_md += "\n".join(f"  - {s}" for s in r_steps) + "\n"
        review_md += "\n"
    else:
        review_md += "- *No roadmap steps defined.*\n\n"

    review_md += "## Part 2: Vault Activity & Ingested Content (Last 7 Days)\n\n"
    if recent_activity_by_folder:
        for folder, file_list in recent_activity_by_folder.items():
            review_md += f"### 📁 `{folder}`\n"
            review_md += "\n".join(f"- {fl}" for fl in file_list) + "\n\n"
    else:
        review_md += "- *No new content ingested in the last 7 days.*\n\n"

    review_md += "## Part 3: Review Alerts & Action Items\n\n"
    if stale_waitings:
        review_md += f"### ⚠️ Stale Delegations (>5 Days)\n" + "\n".join(f"- {w}" for w in stale_waitings) + "\n\n"
    else:
        review_md += "### ⏳ Stale Delegations: *None*\n\n"

    if orphaned_projects:
        review_md += f"### ⚠️ Orphaned Projects (0 Active Actions: {len(orphaned_projects)})\n" + "\n".join(f"- {p}" for p in orphaned_projects) + "\n\n"
    else:
        review_md += "### 📁 Orphaned Projects: *None (All projects have active next actions!)*\n\n"

    # Save to vault
    wr_path = os.path.join(VAULT_PATH, "00_System", "Weekly_Review.md")
    atomic_write(wr_path, review_md)

    # Save dated copy in Digests folder
    wr_archive = os.path.join(VAULT_PATH, "00_System", "Internal", "Digests", f"Weekly_Review_{today_str}.md")
    os.makedirs(os.path.dirname(wr_archive), exist_ok=True)
    atomic_write(wr_archive, review_md)

    # Telegram Notification (Full Part 1 + Part 2 Summary + Pointer)
    from backend.config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_IDS
    if TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_IDS:
        if isinstance(TELEGRAM_ALLOWED_CHAT_IDS, list):
            chat_id = TELEGRAM_ALLOWED_CHAT_IDS[0]
        else:
            chat_id = str(TELEGRAM_ALLOWED_CHAT_IDS).split(",")[0].strip()
            
        async with httpx.AsyncClient() as client:
            # Message 1: Part 1 — All Action Items Across System
            msg1 = f"🗓️ <b>Weekly Review & System Status</b> — {today_str}\n\n"
            msg1 += "<b>Part 1: All Action Items Across System</b>\n\n"
            
            msg1 += f"📥 <b>Captured ({len(captured_items)}):</b>\n"
            msg1 += ("\n".join(f"• {i}" for i in captured_items) if captured_items else "• <i>None (Inbox zero!)</i>") + "\n\n"

            msg1 += f"⚡ <b>Next Actions ({len(next_items)}):</b>\n"
            msg1 += ("\n".join(f"• {i}" for i in next_items) if next_items else "• <i>None</i>") + "\n\n"

            msg1 += f"⏳ <b>Waiting For ({len(waiting_items)}):</b>\n"
            msg1 += ("\n".join(f"• {i}" for i in waiting_items) if waiting_items else "• <i>None</i>") + "\n\n"

            msg1 += f"💡 <b>Someday / Maybe ({len(someday_items)}):</b>\n"
            msg1 += ("\n".join(f"• {i}" for i in someday_items) if someday_items else "• <i>None</i>") + "\n\n"

            msg1 += f"📌 <b>Project Roadmaps:</b>\n"
            if project_roadmaps:
                for p_name, r_steps in project_roadmaps.items():
                    msg1 += f"<b>{p_name}:</b>\n" + "\n".join(f"  • {s}" for s in r_steps) + "\n"
            else:
                msg1 += "• <i>No roadmap steps defined.</i>\n"

            # Telegram character limit safety check
            if len(msg1) > 4000:
                msg1 = msg1[:3950] + "\n\n<i>... [Part 1 truncated for Telegram 4k limit]</i>"

            try:
                await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": msg1, "parse_mode": "HTML"})
            except Exception as e:
                print(f"Failed to send Part 1 weekly review message: {e}")

            # Message 2: Part 2 Activity Summary & Pointer
            total_activity_files = sum(len(v) for v in recent_activity_by_folder.values())
            msg2 = "📊 <b>Part 2: 7-Day Vault Activity Summary</b>\n"
            if recent_activity_by_folder:
                for folder, file_list in recent_activity_by_folder.items():
                    msg2 += f"• <b>{folder}:</b> {len(file_list)} file(s)\n"
                msg2 += f"<i>Total: {total_activity_files} files modified/ingested in last 7 days.</i>\n\n"
            else:
                msg2 += "• <i>No files ingested in last 7 days.</i>\n\n"

            msg2 += "⚠️ <b>Review Alerts:</b>\n"
            msg2 += f"• <b>Stale Delegations (>5d):</b> {len(stale_waitings)}\n"
            msg2 += f"• <b>Orphaned Projects:</b> {len(orphaned_projects)}\n\n"

            msg2 += "📄 <b>Full Report Pointer:</b>\n"
            msg2 += f"Full report saved to: <code>00_System/Weekly_Review.md</code>\n\n"
            msg2 += "<i>Run /reflect in Telegram to start your interactive guided review!</i>"

            try:
                await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": msg2, "parse_mode": "HTML"})
            except Exception as e:
                print(f"Failed to send Part 2 weekly review message: {e}")

async def rolling_retention():
    print("🧹 Rolling retention for old digests...")
    digests_dir = os.path.join(VAULT_PATH, "00_System", "Internal", "Digests")
    if not os.path.exists(digests_dir): return
    cutoff = datetime.now() - timedelta(days=14)
    for f in os.listdir(digests_dir):
        if f.endswith(".md"):
            p = os.path.join(digests_dir, f)
            if datetime.fromtimestamp(os.path.getmtime(p)) < cutoff:
                os.remove(p)

async def main():
    import sys
    if "--weekly-review" in sys.argv:
        await generate_weekly_review()
        return

    print("🚀 Starting Phase 11 Brain Cleaner Tasks...")
    await clean_staging_and_inbox()
    await run_sqlite_checkpoint()
    try: await index_vault()
    except Exception as e: print(f"Failed to index vault: {e}")
    await auto_update_index()
    await cleanup_old_audio()
    await generate_daily_digest()
    await rolling_retention()
    run_brain_cleaner()
    print("✅ All Brain Cleaner Tasks Completed.")

if __name__ == "__main__":
    asyncio.run(main())

