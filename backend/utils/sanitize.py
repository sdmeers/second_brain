import re

# Reserved on Windows; harmless on Linux but a synced vault may land there.
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

DEFAULT_FILENAME = "Untitled"


def sanitize_filename(filename: str, default: str = DEFAULT_FILENAME) -> str:
    """Return a safe single path segment for a vault filename.

    Beyond stripping illegal characters this now guarantees a usable result: path
    separators and traversal segments are removed, and an input that sanitises down to
    nothing (or to a reserved name) falls back to ``default`` rather than producing an
    empty filename that would write to the directory itself.
    """
    if not filename:
        return default

    # Collapse any path structure to its final segment before sanitising
    name = str(filename).replace("\\", "/").split("/")[-1]
    name = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "", name)
    name = re.sub(r'\s+', " ", name).strip()

    # Strip leading dots so we cannot emit "..", ".", or a hidden file
    name = name.lstrip(".").strip()
    name = name[:100].rstrip(" .")

    if not name or name.upper() in _RESERVED_NAMES:
        return default
    return name
