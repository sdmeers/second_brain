import os
import tempfile

from backend.config import SOURCES_PATH, SUMMARIES_PATH

def atomic_write(file_path: str, content, is_binary: bool = False):
    """Write via a unique temp file in the same directory, fsync, then atomically rename.

    Three changes over the previous version:
      - the temp file gets a unique name, so two concurrent writes to the same target
        cannot clobber each other's partial content;
      - the data is fsync'd before the rename, so a crash or power loss cannot leave a
        renamed-but-empty file;
      - the temp file is created with a leading dot and a .tmp suffix so Syncthing and
        Obsidian both ignore it while it exists.
    """
    dir_name = os.path.dirname(file_path) or "."
    os.makedirs(dir_name, exist_ok=True)

    mode = "wb" if is_binary else "w"
    encoding = None if is_binary else "utf-8"
    prefix = f".{os.path.basename(file_path)}."

    # Preserve the target's permissions. mkstemp creates 0600 and os.replace keeps the
    # temp file's mode, so without this every file the system writes silently loses its
    # group/other read bits -- which breaks Samba shares and other-user access.
    try:
        target_mode = os.stat(file_path).st_mode & 0o7777
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        target_mode = 0o666 & ~umask

    fd, temp_path = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=dir_name)
    try:
        with os.fdopen(fd, mode, encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_path, target_mode)
        os.replace(temp_path, file_path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise

def save_source_material(safe_title: str, is_binary: bool, url: str, content_text: str, file_bytes: bytes = None, custom_sources_path: str = None, extension: str = None) -> tuple[str, str]:
    """Saves raw text or binary source to vault/Sources. Returns (filename, absolute_path)."""
    target_path = custom_sources_path or SOURCES_PATH
    ext = extension or (".pdf" if is_binary else ".md")
    if is_binary:
        source_filename = f"{safe_title}{ext}"
        source_path = os.path.join(target_path, source_filename)
        atomic_write(source_path, file_bytes, is_binary=True)
        return source_filename, source_path
    else:
        source_filename = f"{safe_title}_raw.md"
        source_path = os.path.join(target_path, source_filename)
        file_content = f"Source: {url}\n\n{content_text}"
        atomic_write(source_path, file_content)
        return f"{safe_title}_raw", source_path

def save_brain_node(safe_title: str, brain_node_markdown: str, source_link_name: str, custom_summaries_path: str = None) -> str:
    """Injects the source link and saves the final summary to vault/Summaries. Returns the node path."""
    source_injection = f"\n\n**Source Material:** [[{source_link_name}]]\n\n## tl;dr"
    brain_node_markdown = brain_node_markdown.replace("\n## tl;dr", source_injection, 1)
    
    target_path = custom_summaries_path or SUMMARIES_PATH
    node_path = os.path.join(target_path, f"{safe_title}.md")
    atomic_write(node_path, brain_node_markdown)
    return node_path

