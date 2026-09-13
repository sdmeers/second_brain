import os
from dotenv import load_dotenv

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=dotenv_path)

OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.1-flash-lite")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "gemini").lower()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-2")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

GEMINI_MODEL = LLM_MODEL # Keeping for backwards compatibility
WIKILINK_SIMILARITY_THRESHOLD = float(os.getenv('WIKILINK_SIMILARITY_THRESHOLD', '1.2'))

# Embedding throughput controls. The Gemini embedding quota is counted in texts per minute
# per project, so pacing uses the same unit. Default sits under the observed paid-tier
# ceiling of 3,000; drop it well below if the project ever falls back to the free tier.
EMBEDDING_TEXTS_PER_MINUTE = int(os.getenv("EMBEDDING_TEXTS_PER_MINUTE", "2500"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "100"))

if LLM_PROVIDER == "gemini" or EMBEDDING_PROVIDER == "gemini":
    if not GEMINI_API_KEY:
        print(f"CRITICAL: GEMINI_API_KEY not found in {dotenv_path}")
        raise RuntimeError(f"CRITICAL: GEMINI_API_KEY missing from {dotenv_path}")

if LLM_PROVIDER == "openai" or EMBEDDING_PROVIDER == "openai":
    if not OPENAI_API_KEY:
        print(f"CRITICAL: OPENAI_API_KEY not found in {dotenv_path}")
        raise RuntimeError(f"CRITICAL: OPENAI_API_KEY missing from {dotenv_path}")

if not OBSIDIAN_VAULT_PATH:
    raise RuntimeError("CRITICAL: OBSIDIAN_VAULT_PATH missing from .env")

# Ensure Obsidian folders exist (using P.A.R.A. folders)
RESEARCH_BRAIN_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "04_Resources", "Research_Brain")
SUMMARIES_PATH = os.path.join(RESEARCH_BRAIN_PATH, "Summaries")
SOURCES_PATH = os.path.join(RESEARCH_BRAIN_PATH, "Sources")
ATLAS_PATH = os.path.join(RESEARCH_BRAIN_PATH, "Atlas")
CONCEPTS_PATH = os.path.join(RESEARCH_BRAIN_PATH, "Concepts")

# General Reference Resources (Non-Centaur)
ARTICLES_AND_NEWS_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "04_Resources", "Articles_and_News")
ARTICLES_SOURCES_PATH = os.path.join(ARTICLES_AND_NEWS_PATH, "Sources")
BOOKS_AND_MEDIA_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "04_Resources", "Books_and_Media")
MANUALS_AND_EQUIPMENT_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "04_Resources", "Manuals_and_Equipment")
MANUALS_SOURCES_PATH = os.path.join(MANUALS_AND_EQUIPMENT_PATH, "Sources")
CHEATSHEETS_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "04_Resources", "Cheatsheets_and_Guides")

REFERENCE_NOTES_PATH = ARTICLES_AND_NEWS_PATH # Backwards compatibility
REFERENCE_SOURCES_PATH = ARTICLES_SOURCES_PATH

# New P.A.R.A paths
INBOX_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "00_System", "Inbox")
INBOX_ARCHIVE_PATH = os.path.join(INBOX_PATH, "Archive")

IMPORT_PATH = INBOX_PATH
INBOX_MD_FILE = os.path.join(IMPORT_PATH, "Inbox.md")
COMPLETED_MD_FILE = os.path.join(IMPORT_PATH, "Completed.md")
FAILED_MD_FILE = os.path.join(IMPORT_PATH, "Failed.md")

# Task paths
TASKS_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "01_Tasks")
TASKS_MD_FILE = os.path.join(TASKS_PATH, "Next_Actions.md")
COMPLETED_TASKS_MD_FILE = os.path.join(TASKS_PATH, "z_Completed.md")
WAITING_FOR_MD_FILE = os.path.join(TASKS_PATH, "Waiting_For.md")
SOMEDAY_MAYBE_MD_FILE = os.path.join(TASKS_PATH, "Someday_Maybe.md")
CAPTURED_MD_FILE = os.path.join(TASKS_PATH, "Captured.md")

# System internal paths
INTERNAL_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "00_System", "Internal")

def ensure_vault_layout():
    """Create the vault directories and seed files the system expects.

    Called explicitly at startup rather than run as an import side effect, so that
    importing backend.config (which every script does) no longer writes to disk.
    """
    for path in (SUMMARIES_PATH, SOURCES_PATH, ATLAS_PATH, CONCEPTS_PATH,
                 REFERENCE_NOTES_PATH, REFERENCE_SOURCES_PATH, INTERNAL_PATH,
                 TASKS_PATH, IMPORT_PATH):
        os.makedirs(path, exist_ok=True)

    if ENABLE_OBSIDIAN_INBOX_POLLING:
        for path in (INBOX_PATH, INBOX_ARCHIVE_PATH):
            os.makedirs(path, exist_ok=True)

    for filepath in (INBOX_MD_FILE, COMPLETED_MD_FILE, FAILED_MD_FILE):
        if not os.path.exists(filepath):
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write("")
            except OSError as e:
                print(f"Backend [Startup]: Error creating initialization file {filepath}: {e}")

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ALLOWED_CHAT_IDS = []

# Jira Configuration
JIRA_URL = os.getenv("JIRA_URL", "")
JIRA_USER_EMAIL = os.getenv("JIRA_USER_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_DEFAULT_PROJECT_KEY = os.getenv("JIRA_DEFAULT_PROJECT_KEY", "")
allowed_chat_ids_str = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS")
if allowed_chat_ids_str:
    TELEGRAM_ALLOWED_CHAT_IDS = [int(x.strip()) for x in allowed_chat_ids_str.split(",") if x.strip()]

# Gmail Configuration
GMAIL_EMAIL = os.getenv("GMAIL_EMAIL")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# Polling Toggles
ENABLE_OBSIDIAN_INBOX_POLLING = os.getenv("ENABLE_OBSIDIAN_INBOX_POLLING", "false").lower() == "true"
ENABLE_TELEGRAM_BOT = os.getenv("ENABLE_TELEGRAM_BOT", "false").lower() == "true"
ENABLE_GMAIL_POLLING = os.getenv("ENABLE_GMAIL_POLLING", "false").lower() == "true"

PORT = int(os.getenv("PORT", 8080))

# Optional shared secret for the HTTP API (/process, /query). When set, callers must
# send `Authorization: Bearer <token>`. Unset leaves the API open, as before.
API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "")
