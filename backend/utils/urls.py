"""Canonical URL normalisation, used for duplicate-capture detection.

The previous approach discarded the entire query string (`url.split("?")[0]`), which is
wrong for any site that identifies content by query parameter. On YouTube the video ID
lives in `?v=`, so every `youtube.com/watch?v=...` URL collapsed to
`https://www.youtube.com/watch` and each new video was rejected as a duplicate of
whichever one happened to be listed first.

`normalize_url` instead removes only parameters that cannot change *which* document is
being referenced -- tracking tags, share IDs, playback offsets -- and keeps the rest.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Parameters that never identify a different document: analytics, share attribution,
# playback offsets, and playlist/position context.
TRACKING_PARAMS = {
    # Analytics / attribution
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "yclid", "igshid", "mc_cid", "mc_eid",
    "_ga", "_gl", "ref", "ref_src", "ref_url", "source", "spm", "scid",
    # YouTube-specific share/context noise
    "si", "feature", "app", "ab_channel", "pp", "themeRefresh",
    # Playback position and list context (same video either way)
    "t", "start", "end", "time_continue", "index", "list", "start_radio", "rv",
}

YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com"}
YOUTUBE_SHORT_HOSTS = {"youtu.be"}

# YouTube IDs are 11 chars of [A-Za-z0-9_-]
YOUTUBE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def extract_youtube_id(url: str) -> str | None:
    """Return the 11-character video ID for any YouTube URL form, else None.

    Handles watch?v=, youtu.be/, /shorts/, /embed/, /live/ and /v/.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None

    host = _strip_www(parts.netloc.lower())
    path = parts.path

    if host in YOUTUBE_SHORT_HOSTS:
        candidate = path.lstrip("/").split("/")[0]
        return candidate if YOUTUBE_ID_RE.match(candidate) else None

    if host in YOUTUBE_HOSTS:
        if path == "/watch" or path == "/watch/":
            for key, value in parse_qsl(parts.query, keep_blank_values=False):
                if key == "v" and YOUTUBE_ID_RE.match(value):
                    return value
            return None
        for prefix in ("/shorts/", "/embed/", "/live/", "/v/"):
            if path.startswith(prefix):
                candidate = path[len(prefix):].split("/")[0]
                return candidate if YOUTUBE_ID_RE.match(candidate) else None

    return None


def normalize_url(url: str) -> str:
    """Return a canonical comparison key for a URL.

    - YouTube URLs in any form collapse to `https://youtube.com/watch?v=<id>`.
    - Otherwise: lowercase scheme/host, drop `www.` and the fragment, remove tracking
      parameters, and sort the parameters that remain so ordering does not matter.
    - Non-HTTP strings (and anything unparseable) are returned lowercased and stripped.
    """
    if not url:
        return ""

    raw = url.strip()

    video_id = extract_youtube_id(raw)
    if video_id:
        return f"https://youtube.com/watch?v={video_id}"

    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.lower()

    if parts.scheme.lower() not in ("http", "https"):
        return raw.lower().rstrip("/")

    host = _strip_www(parts.netloc.lower())
    path = parts.path.rstrip("/") or "/"

    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(sorted(kept))

    return urlunsplit(("https", host, path, query, ""))


def urls_match(a: str, b: str) -> bool:
    """Whether two URLs refer to the same document."""
    return bool(a) and bool(b) and normalize_url(a) == normalize_url(b)
