import os
import re
import yaml
import asyncio
import time
import json
import hashlib
from pathlib import Path
from dotenv import load_dotenv
from logger import log_action
from google import genai
from google.genai import types

dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=dotenv_path)
VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not VAULT_PATH or not GEMINI_API_KEY:
    raise RuntimeError("Missing OBSIDIAN_VAULT_PATH or GEMINI_API_KEY in .env")

SUMMARIES_DIR = os.path.join(VAULT_PATH, "02 Summaries")
ATLAS_DIR = os.path.join(VAULT_PATH, "03 Atlas")
CONCEPTS_DIR = os.path.join(VAULT_PATH, "04 Concepts")

client = genai.Client(api_key=GEMINI_API_KEY)

def call_gemini_with_retry(model, contents, config=None, max_retries=5):
    """Synchronous wrapper to call Gemini API with exponential backoff for 429 and 503 errors."""
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
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)

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
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
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
                    model="gemini-3.1-flash-lite-preview",
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
                
                with open(canonical_file, 'w', encoding='utf-8') as out_f:
                    out_f.write(final_content)
                    
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
                        with open(md_file, 'w', encoding='utf-8') as write_f:
                            write_f.write(new_text)
                            
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
    source_emojis = ("🎞️", "🏛️", "📄", "📖")
    
    for topic, data in links_data.items():
        # Skip if the link starts with a source emoji
        if topic.startswith(source_emojis):
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
                    model="gemini-3.1-flash-lite-preview",
                    contents=prompt
                )
                api_calls += 1
                with open(concept_path, 'w', encoding='utf-8') as f:
                    f.write(response.text.strip())
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
                    model="gemini-3.1-flash-lite-preview",
                    contents=prompt
                )
                api_calls += 1
                new_content = response.text.strip()
                
                with open(f, 'w', encoding='utf-8') as file:
                    file.write(new_content)
                
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

if __name__ == "__main__":
    run_brain_cleaner()