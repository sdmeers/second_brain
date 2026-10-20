import re
from datetime import datetime
from pydantic import BaseModel, Field
from google.genai import types
from backend.config import GEMINI_MODEL
from backend.vault.paths import get_existing_concepts
from backend.utils.gemini import get_or_create_context_cache, call_gemini_with_retry
from backend.prompts import load_prompt

class OntologyExtraction(BaseModel):
    summary_markdown: str = Field(description="The complete markdown formatted summary including YAML frontmatter.")
    concepts: list[str] = Field(description="List of all wikilinks extracted in the Core Concepts section (e.g. ['[[Concept 1]]', '[[Concept 2]]']). MUST NOT BE EMPTY.")

def clean_yaml_brackets(content: str) -> str:
    """Surgically fixes theme_primary: [[Topic]] and theme_related: [[Topic1], [Topic2]] issues."""
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

async def generate_brain_node(title_hint: str, author_hint: str, url: str, content: str, atlas_themes: list[str]) -> OntologyExtraction:
    """Calls Gemini to generate the Obsidian-formatted ontology node and a list of concepts."""
    print(f"Backend [Gemini]: Preparing prompt for content ({len(content)} chars)...")
    
    themes_str = "\n".join([f"   - {t}" for t in atlas_themes]) if atlas_themes else "   - (No established themes yet, you can invent some if needed)"
    
    existing_concepts = get_existing_concepts()
    concepts_str = ", ".join(existing_concepts) if existing_concepts else "(No established concepts yet)"
    
    system_instruction = load_prompt("brain_node", themes=themes_str, concepts=concepts_str)
    
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    prompt = (
        f"Title Hint: {title_hint}\n"
        f"Author Hint: {author_hint}\n"
        f"Source: {url}\n"
        f"Date: {date_str}\n"
        f"Text to analyze: {content[:100000]}"
    )
    
    cache_name = await get_or_create_context_cache(GEMINI_MODEL, system_instruction)
    
    try:
        print(f"Backend [Gemini]: Calling Gemini API (model: {GEMINI_MODEL})...")
        
        if cache_name:
            config = types.GenerateContentConfig(
                cached_content=cache_name,
                temperature=0.3,
                response_mime_type="application/json",
                response_schema=OntologyExtraction,
            )
        else:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.3,
                response_mime_type="application/json",
                response_schema=OntologyExtraction,
            )
            
        response = await call_gemini_with_retry(
            model=GEMINI_MODEL,
            contents=prompt,
            config=config
        )
        print("Backend [Gemini]: API call successful.")
        
        try:
            data = response.text
            parsed = OntologyExtraction.model_validate_json(data)
            
            if not parsed.summary_markdown.strip().startswith("---"):
                parsed.summary_markdown = "---\n" + parsed.summary_markdown.strip()
                
            return parsed
        except Exception as parse_e:
            print(f"Backend [Gemini]: JSON Parsing Error: {parse_e}")
            print(f"Raw Output: {response.text}")
            raise
    except Exception as e:
        print(f"Backend [Gemini]: ERROR: {e}")
        raise

class LightNodeExtraction(BaseModel):
    summary_markdown: str = Field(description="The complete markdown formatted summary including YAML frontmatter. Must contain actual newline characters (\\n) separating each line. DO NOT output a single line.")

async def generate_light_node(title_hint: str, author_hint: str, url: str, content: str) -> LightNodeExtraction:
    """Calls Gemini to generate a lightweight summary of standard URL captures and bookmarks (without concepts/MOC details)."""
    print(f"Backend [Gemini-Light]: Preparing prompt for content ({len(content)} chars)...")
    
    system_instruction = load_prompt("light_node")

    date_str = datetime.now().strftime("%Y-%m-%d")
    
    prompt = (
        f"Title Hint: {title_hint}\n"
        f"Author Hint: {author_hint}\n"
        f"Source: {url}\n"
        f"Date: {date_str}\n"
        f"Text to analyze: {content[:30000]}"
    )
    
    try:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=LightNodeExtraction,
        )
        
        response = await call_gemini_with_retry(
            model=GEMINI_MODEL,
            contents=prompt,
            config=config
        )
        print("Backend [Gemini-Light]: API call successful.")
        
        data = response.text
        parsed = LightNodeExtraction.model_validate_json(data)
        
        if not parsed.summary_markdown.strip().startswith("---"):
            parsed.summary_markdown = "---\n" + parsed.summary_markdown.strip()
            
        return parsed
    except Exception as e:
        print(f"Backend [Gemini-Light]: ERROR: {e}")
        raise

