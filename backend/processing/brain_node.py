import re
from datetime import datetime
from pydantic import BaseModel, Field
from google.genai import types
from backend.config import GEMINI_MODEL
from backend.vault.paths import get_existing_concepts
from backend.utils.gemini import get_or_create_context_cache, call_gemini_with_retry

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
    
    system_instruction = f"""You are an expert knowledge architect building a 'Second Brain' in Obsidian.
Your goal is to analyze the provided text and extract a structured ontology based on a 'Map of Content' (MOC) strategy.

CRITICAL RULES:
1. THEMES vs CONCEPTS:
    - THEMES: These are high-level Map of Content (MOC) categories from the user's established Atlas. You MUST identify ONE 'theme_primary' and zero or more 'theme_related' entries.
    - Use double brackets [[Topic]] ONLY for themes and concepts.
    - established ATLAS THEMES:
{themes_str}

2. CONCEPTS: Extract highly specific, non-trivial concepts, frameworks, and entities as wikilinks (e.g., [[Time Horizon]], [[Responsible Scaling Policy]], [[Collective Action Problem]]).
   - DO NOT extract generic, everyday business, tech, or academic words as new concepts (e.g., avoid creating concept pages for [[External Review]], [[Internal Accountability]], [[Industry-wide Safety]]).
   - EXCEPTION FOR THEMES: If a broad term perfectly matches one of your established ATLAS THEMES (like [[Innovation]] or [[Technology]]), you MUST categorize it under THEMES (theme_primary or theme_related), NOT as a new CONCEPT.
   - Target roughly 5 to 14 highly impactful concepts per document. Quality and specificity are far more important than quantity. Do not over-saturate with generic ideas.
   - NEVER include the title of the document itself or other referenced documents (e.g. things starting with 🎞️, 🏛️, 📄, 📖) in the 'concepts' array. Those are source nodes, not concept nodes.

3. FORMATTING & NAMING GUARDRAILS FOR CONCEPTS:
   - Here is a list of existing concepts in the vault: {concepts_str}
   - If a concept you identify is highly similar, synonymous, or an acronym of an existing concept, DO NOT create a new name. You MUST use the exact existing concept name from the list for your wikilinks.
   - When generating truly new concept titles, adhere strictly to these rules:
     a) Always use singular nouns (e.g., 'Agent', not 'Agents').
     b) Always use the full term, omitting parenthetical acronyms in titles (e.g., 'Minimum Viable Product', never 'Minimum Viable Product (MVP)').
     c) Remove hyphens from compound concepts unless grammatically strictly required (e.g., 'Hyperwar', never 'Hyper-war').

4. DETAILED SUMMARIES: Write comprehensive, highly detailed summaries that preserve nuance and specific arguments, rather than over-simplified high-level overviews. Err on the side of providing more detail.

5. TAGS: These are granular metadata tags for states and types (e.g., #article, #book). DO NOT use tags for topics.

6. EMOJI PREFIXES: You must prefix the title with an emoji based on the content type:
   - article: 📄
   - video: 🎞️
   - paper: 🏛️
   - book: 📖

7. OUTPUT FORMAT TEMPLATE for summary_markdown:
---
title: "{{Emoji}} {{Extract Title}}"
author: "{{Extract the Author}}"
url: "{{URL}}"
date_processed: "{{Date}}"
date_captured: "{{Date}}"
status: "🟡 to-review"
theme_primary: "[[Theme Name]]"
theme_related: ["[[Theme 1]]", "[[Theme 2]]"]
type: "{{article | video | paper | book}}"
cover: "{{Emoji}}"
tags: [brain]
---
# [[{{Emoji}} {{Extract Title}}]]

## tl;dr
...
{{A comprehensive and nuanced summary of the core message or contribution.}}

## Core Concepts
* **[[Concept 1]]**: {{Brief context}}
* **[[Concept 2]]**: {{Brief context}}

## Key Takeaways
* {{Point 1}}
* {{Point 2}}

## 🗺️ Context & MOC
- **[[Theme Primary]]**: {{One sentence on how this connects to the primary theme.}}
- **[[Theme Related 1]]**, **[[Theme Related 2]]**: {{How it intersects with other themes.}}

## Emergent Themes & Connections
{{Analyze how this intersects with its themes and implications for the future.}}

8. JSON SCHEMA POPULATION: You must return a JSON object.
   - The `summary_markdown` field must contain the full markdown text.
   - The `concepts` field MUST be an array of strings containing the exact wikilinks you extracted in the Core Concepts section (e.g., ["[[Time Horizon]]", "[[Responsible Scaling Policy]]"]). If you do not populate this array, the concept pages will not be created.
"""
    
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
