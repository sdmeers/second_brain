import re

def sanitize_filename(filename: str) -> str:
    """Removes illegal characters and normalizes whitespace for filenames."""
    filename = re.sub(r'[\\/*?:"<>|]', "", filename)
    filename = re.sub(r'\s+', " ", filename)  # Collapse multiple spaces into one
    return filename.strip()[:100]  # Limit length
