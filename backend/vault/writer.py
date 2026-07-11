import os
from backend.config import SOURCES_PATH, SUMMARIES_PATH

def save_source_material(safe_title: str, is_pdf: bool, url: str, content_text: str, pdf_bytes: bytes = None) -> tuple[str, str]:
    """Saves raw text or PDF source to vault/Sources. Returns (filename, absolute_path)."""
    if is_pdf:
        source_filename = f"{safe_title}.pdf"
        source_path = os.path.join(SOURCES_PATH, source_filename)
        with open(source_path, "wb") as f:
            f.write(pdf_bytes)
        return source_filename, source_path
    else:
        source_filename = f"{safe_title}_raw.md"
        source_path = os.path.join(SOURCES_PATH, source_filename)
        with open(source_path, "w", encoding="utf-8") as f:
            f.write(f"Source: {url}\n\n{content_text}")
        return f"{safe_title}_raw", source_path

def save_brain_node(safe_title: str, brain_node_markdown: str, source_link_name: str) -> str:
    """Injects the source link and saves the final summary to vault/Summaries. Returns the node path."""
    source_injection = f"\n\n**Source Material:** [[{source_link_name}]]\n\n## tl;dr"
    brain_node_markdown = brain_node_markdown.replace("\n## tl;dr", source_injection, 1)
    
    node_path = os.path.join(SUMMARIES_PATH, f"{safe_title}.md")
    with open(node_path, "w", encoding="utf-8") as f:
        f.write(brain_node_markdown)
    return node_path
