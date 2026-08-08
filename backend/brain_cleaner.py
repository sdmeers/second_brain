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
    
    index_file = os.path.join(VAULT_PATH, "00_System", "_INDEX.md")
    if not os.path.exists(index_file):
        return
        
    with open(index_file, "r", encoding="utf-8") as f:
        content = f.read()
        
    proj_text = "## 02_Projects/\n" + "\n".join(f"- `{p}`" for p in sorted(projects)) + "\n"
    area_text = "## 03_Areas/\n" + "\n".join(f"- `{a}`" for p in sorted(areas) for a in [p]) + "\n" # formatting trick to get loop variable
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

async def generate_daily_digest():
    print("🌅 Generating daily digest...")
    
    # --- Part 1: Tasks Summary ---
    tasks_file = os.path.join(VAULT_PATH, "01_Tasks", "Next_Actions.md")
    overdue_tasks = []
    active_tasks = []
    fourteen_days_ago = datetime.now() - timedelta(days=14)
    if os.path.exists(tasks_file):
        with open(tasks_file, "r", encoding="utf-8") as f:
            for line in f:
                if "- [ ]" in line:
                    m = re.search(r'\(Captured from .*? on (\d{4}-\d{2}-\d{2})\)', line)
                    if m:
                        try:
                            cap_date = datetime.strptime(m.group(1), "%Y-%m-%d")
                            if cap_date < fourteen_days_ago:
                                overdue_tasks.append(line.strip())
                            else:
                                active_tasks.append(line.strip())
                        except ValueError:
                            active_tasks.append(line.strip())
                    else:
                        active_tasks.append(line.strip())

    # --- Part 2 & 3: Recent Activity and Recent Ideas (Last 7 Days) ---
    seven_days_ago = datetime.now() - timedelta(days=7)
    recent_activity = {}
    recent_activity_notes = []
    recent_ideas = []

    for root, dirs, files in os.walk(VAULT_PATH):
        rel_root = os.path.relpath(root, VAULT_PATH).replace("\\", "/")
        if rel_root.startswith("00_System") or rel_root.startswith("01_Tasks") or rel_root.startswith("."):
            continue

        for f in files:
            if f.endswith(".md") and f not in ["_INDEX.md", "Librarian_Activity.md"]:
                p = os.path.join(root, f)
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(p))
                    ctime = datetime.fromtimestamp(os.path.getctime(p))
                    if mtime > seven_days_ago or ctime > seven_days_ago:
                        # Part 2
                        if rel_root not in recent_activity:
                            recent_activity[rel_root] = []
                        recent_activity[rel_root].append(f)
                        
                        with open(p, "r", encoding="utf-8") as file_obj:
                            content = file_obj.read()
                            recent_activity_notes.append({"name": f, "content": content})

                        # Part 3
                        is_idea = False
                        if "02_Projects/Ideas" in rel_root:
                            is_idea = True
                        elif "#type/idea" in content:
                            is_idea = True
                            
                        if is_idea:
                            recent_ideas.append(f)
                except Exception:
                    pass

    # Assemble Digest (Parts 1, 2, 3 only)
    digest_content = f"# Daily Digest - {datetime.now().strftime('%Y-%m-%d')}\n\n"
    
    digest_content += "## Part 1: Tasks Summary\n"
    if overdue_tasks:
        digest_content += "### Overdue/Stagnant Tasks (>14 days)\n" + "\n".join(overdue_tasks) + "\n\n"
    if active_tasks:
        digest_content += f"### Active Backlog Tasks ({len(active_tasks)} tasks)\n" + "\n".join(active_tasks[:10])
        if len(active_tasks) > 10:
             digest_content += f"\n... and {len(active_tasks) - 10} more.\n"
    digest_content += "\n\n"
    
    digest_content += "## Part 2: Recent Activity (Last 7 Days)\n"
    if recent_activity:
        for folder, files in recent_activity.items():
            digest_content += f"### {folder}\n"
            for f in files:
                digest_content += f"- {f}\n"
    else:
        digest_content += "No recent activity recorded in the last 7 days.\n"
    digest_content += "\n"
    
    digest_content += "## Part 3: Recent Ideas\n"
    if recent_ideas:
        for idea in recent_ideas:
            digest_content += f"- {idea}\n"
    else:
        digest_content += "No recent ideas captured.\n"
    digest_content += "\n"

    # --- Part 4: AI Synthesis & Suggestions (Commented out - preserved for future adaptation) ---
    # vss_matches = []
    # try:
    #     from backend.rag.embeddings import get_embeddings_batch
    #     from backend.rag.vectorstore import search_similar_chunks
    #     texts = [note['content'][:2000] for note in recent_activity_notes if note['content'].strip()]
    #     if texts:
    #         embs = await get_embeddings_batch(texts)
    #         for emb in embs:
    #             if emb:
    #                 results = await search_similar_chunks(emb, top_k=5)
    #                 for r in results:
    #                     path = r['file_path']
    #                     if "04_Resources/" in path.replace("\\", "/"):
    #                         vss_matches.append(path)
    # except Exception as e:
    #     print(f"VSS error: {e}")
    #
    # prompt = f"""Analyze recent activity and ideas..."""
    # try:
    #     resp = call_gemini_with_retry(GEMINI_MODEL, prompt)
    #     ai_synthesis = resp.text.strip()
    #     digest_content += "## Part 4: AI Synthesis & Suggestions\n" + ai_synthesis + "\n"
    # except Exception as e:
    #     pass

    date_str = datetime.now().strftime("%Y-%m-%d")
    digest_path = os.path.join(VAULT_PATH, "00_System", "Internal", "Digests", f"{date_str}.md")
    os.makedirs(os.path.dirname(digest_path), exist_ok=True)
    atomic_write(digest_path, digest_content)
    
    daily_digest_path = os.path.join(VAULT_PATH, "00_System", "Daily_Digest.md")
    atomic_write(daily_digest_path, digest_content)

    return digest_content


async def generate_sitrep_summary() -> str:
    """Generates a real-time SITREP digest and formats a 3-part summary for Telegram."""
    digest_content = await generate_daily_digest()
    
    prompt = """Create a concise, punchy Telegram SITREP summary of this digest following EXACTLY this plain text structure (without markdown * or ** asterisks, use standard hyphens - for bullets):

📊 SITREP / Summary:

Part 1: Tasks
[Concise summary of active and stagnant/overdue tasks]

Part 2: Recent Activity
[Summary list of folders and notes worked on in the last 7 days]

Part 3: Recent Ideas
[List of recently captured ideas]

Here is the digest content to summarize:
""" + digest_content

    try:
        resp = call_gemini_with_retry(GEMINI_MODEL, prompt)
        summary = resp.text.strip().replace("*", "")
        return summary
    except Exception as e:
        return f"📊 SITREP / Summary:\n\n{digest_content}"

async def rolling_retention():
    print("🧹 Rolling retention for old digests...")
    digests_dir = os.path.join(VAULT_PATH, "00_System", "Internal", "Digests")
    if not os.path.exists(digests_dir):
        return
    cutoff = datetime.now() - timedelta(days=14)
    for f in os.listdir(digests_dir):
        if f.endswith(".md"):
            p = os.path.join(digests_dir, f)
            if datetime.fromtimestamp(os.path.getmtime(p)) < cutoff:
                os.remove(p)

async def main():
    print("🚀 Starting Phase 11 Brain Cleaner Tasks...")
    await clean_staging_and_inbox()
    await run_sqlite_checkpoint()
    try:
        await index_vault()
    except Exception as e:
        print(f"Failed to index vault: {e}")
        
    await auto_update_index()
    await cleanup_old_audio()
    await generate_daily_digest()
    await rolling_retention()
    
    # Run sync vault maintenance
    run_brain_cleaner()
    
    print("✅ All Brain Cleaner Tasks Completed.")

if __name__ == "__main__":
    asyncio.run(main())
