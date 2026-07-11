import os
from backend.config import SOURCES_PATH, SUMMARIES_PATH

def atomic_write(file_path: str, content, is_binary: bool = False):
    """Writes content to a temporary file first, then renames it atomically to prevent corruption."""
    temp_path = f"{file_path}.tmp"
    dir_name = os.path.dirname(file_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    
    mode = "wb" if is_binary else "w"
    encoding = None if is_binary else "utf-8"
    
    try:
        with open(temp_path, mode, encoding=encoding) as f:
            f.write(content)
        os.replace(temp_path, file_path)
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise e

def save_source_material(safe_title: str, is_pdf: bool, url: str, content_text: str, pdf_bytes: bytes = None) -> tuple[str, str]:
    """Saves raw text or PDF source to vault/Sources. Returns (filename, absolute_path)."""
    if is_pdf:
        source_filename = f"{safe_title}.pdf"
        source_path = os.path.join(SOURCES_PATH, source_filename)
        atomic_write(source_path, pdf_bytes, is_binary=True)
        return source_filename, source_path
    else:
        source_filename = f"{safe_title}_raw.md"
        source_path = os.path.join(SOURCES_PATH, source_filename)
        file_content = f"Source: {url}\n\n{content_text}"
        atomic_write(source_path, file_content)
        return f"{safe_title}_raw", source_path

def save_brain_node(safe_title: str, brain_node_markdown: str, source_link_name: str) -> str:
    """Injects the source link and saves the final summary to vault/Summaries. Returns the node path."""
    source_injection = f"\n\n**Source Material:** [[{source_link_name}]]\n\n## tl;dr"
    brain_node_markdown = brain_node_markdown.replace("\n## tl;dr", source_injection, 1)
    
    node_path = os.path.join(SUMMARIES_PATH, f"{safe_title}.md")
    atomic_write(node_path, brain_node_markdown)
    return node_path

