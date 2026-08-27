import os
import re
import asyncio
import httpx

from backend.config import OBSIDIAN_VAULT_PATH
from backend.vault.paths import is_url_already_ingested, get_atlas_themes
from backend.vault.writer import save_source_material, save_brain_node
from backend.processing.extractor import (
    extract_pdf_text_from_bytes,
    extract_pdf_text,
    extract_youtube_transcript,
    extract_web_text,
    fetch_cover
)
from backend.processing.brain_node import generate_brain_node, generate_light_node, clean_yaml_brackets
from backend.processing.concept_updater import update_concept_page
from backend.utils.sanitize import sanitize_filename
from backend.utils.urls import extract_youtube_id
from backend.utils.logger import log_action

class DuplicateURLError(Exception):
    def __init__(self, message: str, existing_title: str):
        super().__init__(message)
        self.existing_title = existing_title


async def process_capture_core(
    url: str,
    title: str = "Untitled Capture",
    author_hint: str = "",
    markdown_text: str = "",
    file_path: str | None = None,
    deep_extraction: bool = True,
    is_research: bool = False
) -> dict:
    try:
        print(f"\n>>> Backend: Processing Request: {title} (Deep: {deep_extraction})")
        
        # Check for duplicates before executing stage 1 (saves bandwidth and API queries)
        existing_title = is_url_already_ingested(url)
        if existing_title:
            print(f"Backend: URL already ingested: {url} (Title: {existing_title})")
            raise DuplicateURLError(f"This URL has already been ingested as '{existing_title}'.", existing_title)
        
        content_text = markdown_text
        clean_url = url.lower().split('?')[0]
        
        ext = ""
        if file_path:
            _, ext = os.path.splitext(file_path.lower())
            
        is_pdf = ext == ".pdf" or clean_url.endswith('.pdf') or '/pdf/' in clean_url
        is_pptx = ext == ".pptx" or clean_url.endswith('.pptx')
        is_docx = ext in [".docx", ".doc"] or clean_url.endswith('.docx') or clean_url.endswith('.doc')
        # Covers watch?v=, youtu.be, /shorts/, /embed/, /live/ and mobile hosts;
        # the previous substring test missed everything but the first two.
        is_youtube = extract_youtube_id(url) is not None
        
        # Read the file bytes if file_path is provided
        file_bytes = None
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, "rb") as f:
                    file_bytes = f.read()
            except Exception as read_e:
                print(f"Backend: Failed to read file bytes: {read_e}")
        
        # 1. Handle PDF vs PPTX vs DOCX vs Text extraction vs YouTube Fallback
        if is_pdf:
            print("Backend [Stage 1]: Processing as PDF...")
            if file_bytes:
                content_text = extract_pdf_text_from_bytes(file_bytes)
            else:
                content_text, file_bytes = await extract_pdf_text(url)
        elif is_pptx:
            print("Backend [Stage 1]: Processing as PPTX...")
            from backend.processing.extractor import extract_pptx_text_from_bytes
            if file_bytes:
                extracted_text = extract_pptx_text_from_bytes(file_bytes)
                # Combine slide text with the email body or notes if present
                if content_text.strip():
                    content_text = f"{content_text}\n\n=== Slide Contents ===\n\n{extracted_text}"
                else:
                    content_text = extracted_text
            else:
                content_text = f"PowerPoint presentation: {title}"
        elif is_docx:
            print("Backend [Stage 1]: Processing as Word Document (.docx)...")
            from backend.processing.extractor import extract_docx_text, extract_docx_text_from_bytes
            if file_bytes:
                content_text = extract_docx_text_from_bytes(file_bytes)
            else:
                content_text, file_bytes = await extract_docx_text(url)
        elif is_youtube and not content_text:
            print("Backend [Stage 1]: Processing as YouTube (via backend fallback)...")
            content_text = await extract_youtube_transcript(url)
        elif not content_text:
            print(f"Backend [Stage 1]: Scraping Web Content from {url}...")
            content_text = await extract_web_text(url)
        else:
            print(f"Backend [Stage 1]: Processing as Text ({len(content_text) if content_text else 0} chars)...")
                 
        # 2. Call Gemini
        brain_node_markdown = ""
        concepts_to_process = []
        
        if is_research or deep_extraction:
            print("Backend [Stage 2]: Generating Brain Node (Deep Research Knowledge Graph)...")
            atlas_themes = get_atlas_themes()
            brain_node_result = await generate_brain_node(
                title_hint=title,
                author_hint=author_hint,
                url=url,
                content=content_text,
                atlas_themes=atlas_themes
            )
            brain_node_markdown = brain_node_result.summary_markdown.strip()
            concepts_to_process = brain_node_result.concepts
        else:
            print("Backend [Stage 2]: Generating Practical Summary (Lightweight Reference Note)...")
            brain_node_result = await generate_light_node(
                title_hint=title,
                author_hint=author_hint,
                url=url,
                content=content_text
            )
            brain_node_markdown = brain_node_result.summary_markdown.strip()
            concepts_to_process = []

        # 3. Clean Gemini output (Strip markdown backticks if present)
        # Remove ```yaml or ``` at start/end
        brain_node_markdown = re.sub(r'^```[a-z]*\n', '', brain_node_markdown)
        brain_node_markdown = re.sub(r'\n```$', '', brain_node_markdown)
        brain_node_markdown = brain_node_markdown.strip()
        
        # 4. Standardize YAML (Fix brackets)
        brain_node_markdown = clean_yaml_brackets(brain_node_markdown)

        # 5. Fetch Cover Image
        print("Backend [Stage 3]: Fetching Cover Image...")

        cover_url = await fetch_cover(url, is_youtube)
        if cover_url:
            print(f"Backend [Stage 3]: Found cover: {cover_url}")
            # Inject cover into YAML frontmatter if gemini hasn't provided a better one
            if 'cover: "' in brain_node_markdown:
                brain_node_markdown = re.sub(r'cover: ".*?"', f'cover: "{cover_url}"', brain_node_markdown)
            else:
                brain_node_markdown = brain_node_markdown.replace("type:", f"cover: \"{cover_url}\"\ntype:", 1)
        
        # 4. Parse true title
        print("Backend [Stage 4]: Finalizing and Saving...")
        real_title = title
        match = re.search(r'^title:\s*"(.*?)"', brain_node_markdown, re.MULTILINE)
        if match:
            real_title = match.group(1)
            
        # Strip browser notification counts like (1) or (1234) from the start
        real_title = re.sub(r'^\(\d+\)\s*', '', real_title)
        
        # Strip generic "Deep Research Capture" prefix if present
        real_title = re.sub(r'^(📄|🎞️|🏛️|📖)?\s*Deep\s+Research\s+Capture\s*[-:\s]*', r'\1 ', real_title, flags=re.IGNORECASE).strip()
        
        # If title is empty or generic, ensure we don't fall back to "Deep Research Capture"
        if not real_title or real_title.strip() in ["📄", "🎞️", "🏛️", "📖", "Deep Research Capture"]:
            real_title = title if title and title != "Untitled Capture" else "Untitled Article"
            
        safe_title = sanitize_filename(real_title)
        print(f"Backend [Stage 4]: Determined Title: {safe_title}")
        
        # 5. Save Source file via AI Librarian P.A.R.A destination check
        from backend.utils.librarian import determine_para_destination
        if not (is_research or deep_extraction):
            custom_sum_path, custom_src_path = await determine_para_destination(safe_title, content_text, is_pdf=is_pdf)
        else:
            from backend.config import SOURCES_PATH, SUMMARIES_PATH
            custom_src_path = SOURCES_PATH
            custom_sum_path = SUMMARIES_PATH

        ext_to_use = ".pdf" if is_pdf else (".pptx" if is_pptx else (".docx" if is_docx else ".md"))
        source_link_name, _ = save_source_material(
            safe_title, is_pdf or is_pptx or is_docx, url, content_text, file_bytes,
            custom_sources_path=custom_src_path, extension=ext_to_use
        )
                
        # 6. Save Brain Node (clean, concise AI summary note)
        node_path = save_brain_node(
            safe_title, brain_node_markdown, source_link_name,
            custom_summaries_path=custom_sum_path
        )
            
        # 7. Process Concepts (Entity Update Loop)
        if deep_extraction and concepts_to_process:
            print(f"Backend [Stage 5]: Processing {len(concepts_to_process)} concepts with throttling...")
            for concept in concepts_to_process:
                await update_concept_page(concept, brain_node_markdown, safe_title)
                await asyncio.sleep(4)  # Stay under 15 RPM limit
        else:
            print("Backend [Stage 5]: Skipping concept page creation (Lightweight / 0 concepts).")
            
        # Log the ingestion to Librarian Activity Log and system log
        themes = []
        primary_match = re.search(r'theme_primary:\s*"(.*?)"', brain_node_markdown)
        if primary_match:
            themes.append(primary_match.group(1))
            
        related_match = re.search(r'theme_related:\s*\[(.*)\]', brain_node_markdown)
        if related_match:
            related_themes = [t.strip().strip('"').strip("'") for t in related_match.group(1).split(',')]
            themes.extend([t for t in related_themes if t])
            
        log_action("Ingested", f'Source: "{safe_title}"', concepts=themes)
        
        try:
            from backend.utils.activity_log import log_librarian_activity
            loc_str = os.path.dirname(os.path.relpath(node_path, OBSIDIAN_VAULT_PATH)) if node_path else "04_Resources"
            capture_label = "deep_research" if deep_extraction else "reference / url_capture"
            log_librarian_activity(title=safe_title, location=loc_str, source_type=f"{capture_label} capture")
        except Exception as log_e:
            print(f"Backend [Capture]: Failed to update Librarian Activity log: {log_e}")
            
        print(f"<<< Backend: Request Complete: {safe_title}")
        return {"status": "success", "title": safe_title, "node_path": node_path}

        
    except httpx.HTTPStatusError as e:
        print(f"HTTP Error during capture: {e}")
        raise RuntimeError("Failed to download source URL")
    except Exception as e:
        print(f"Error in core capture processing: {e}")
        raise e
