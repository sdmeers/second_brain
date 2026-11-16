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

# Transient attachments from Telegram and Gmail, outside the vault. The queue worker deletes
# a staged file once its item is processed. Gmail previously wrote into INBOX_PATH, which the
# drop-folder scanner also watches, so every emailed attachment was ingested twice.
STAGING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "staging")
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

# Copies of task files taken before the sync-conflict healer merges them. Deliberately
# outside the vault so Syncthing never propagates (or conflicts on) the backups.
TASK_BACKUP_PATH = os.getenv(
    "TASK_BACKUP_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "task_backups"))
TASK_BACKUP_RETENTION_DAYS = int(os.getenv("TASK_BACKUP_RETENTION_DAYS", "30"))
# The dashboard leaves a task file alone for this long after another device edits it, so
# its reformatting write can't race the edit and create a Syncthing conflict.
TASK_SYNC_QUIET_SECONDS = float(os.getenv("TASK_SYNC_QUIET_SECONDS", "30"))
# Server-side writes to 01_Tasks/ are committed to the vault's git once they settle.
TASK_COMMIT_DELAY_SECONDS = float(os.getenv("TASK_COMMIT_DELAY_SECONDS", "60"))

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
# Addresses allowed to send to the +brain address, comma-separated. Empty accepts anyone,
# as before; set it so a stranger who learns the address cannot put content in the vault.
GMAIL_ALLOWED_SENDERS = [a.strip().lower() for a in os.getenv("GMAIL_ALLOWED_SENDERS", "").split(",") if a.strip()]

# Polling Toggles
ENABLE_OBSIDIAN_INBOX_POLLING = os.getenv("ENABLE_OBSIDIAN_INBOX_POLLING", "false").lower() == "true"
ENABLE_TELEGRAM_BOT = os.getenv("ENABLE_TELEGRAM_BOT", "false").lower() == "true"
ENABLE_GMAIL_POLLING = os.getenv("ENABLE_GMAIL_POLLING", "false").lower() == "true"

PORT = int(os.getenv("PORT", 8080))

# Optional shared secret for the HTTP API (/process, /query). When set, callers must
# send `Authorization: Bearer <token>`. Unset leaves the API open, as before.
API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "")

# --- Context-ingestion feeds (backend/feeds/) ---------------------------------------
# Ingestion state and the cost ledger live outside the vault so Syncthing never sees them.
FEEDS_DB_PATH = os.getenv(
    "FEEDS_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "feeds.db"))
# Raw exports sit under the inbox, which is excluded from Syncthing and from the RAG index.
FEEDS_RAW_PATH = os.path.join(INBOX_PATH, "Raw")
# Hard monthly cap on model spend by the feeds, in pence. Existing features are not capped.
FEEDS_MONTHLY_BUDGET_PENCE = float(os.getenv("FEEDS_MONTHLY_BUDGET_PENCE", "300"))
# Held back from model calls so the small cost of indexing each new note always fits under
# the cap. Model calls stop at budget minus reserve; note writes may use the reserve.
FEEDS_INDEXING_RESERVE_PENCE = float(os.getenv("FEEDS_INDEXING_RESERVE_PENCE", "10"))
# A single run estimated above this must be approved before it starts. Backfills always ask.
FEEDS_APPROVAL_THRESHOLD_PENCE = float(os.getenv("FEEDS_APPROVAL_THRESHOLD_PENCE", "25"))
# Provider prices are in USD; set a little high so the ledger over- rather than under-counts.
FEEDS_USD_TO_GBP = float(os.getenv("FEEDS_USD_TO_GBP", "0.80"))
# Lets chat read the open task lists and stores note type/date in the index. Off by default.
ENABLE_FEED_RETRIEVAL = os.getenv("ENABLE_FEED_RETRIEVAL", "false").lower() == "true"
# Daily voice journal (backend/feeds/journal.py). Off by default.
ENABLE_JOURNAL_FEED = os.getenv("ENABLE_JOURNAL_FEED", "false").lower() == "true"
JOURNAL_DIR = "03_Areas/Journal"   # vault-relative; notes go in a sub-folder per year
# Journal recordings older than this many months are deleted (the transcripts stay).
# 0, the default, keeps them forever.
JOURNAL_AUDIO_KEEP_MONTHS = int(os.getenv("JOURNAL_AUDIO_KEEP_MONTHS", "0") or 0)

# Personal calendar (backend/feeds/calendar.py). One or more secret iCal addresses,
# comma-separated. Treat them as secrets: never log them.
ENABLE_CALENDAR_FEED = os.getenv("ENABLE_CALENDAR_FEED", "false").lower() == "true"
GCAL_ICAL_URLS = [u.strip().strip('"') for u in os.getenv("GCAL_ICAL_URL", "").split(",") if u.strip()]
CALENDAR_LOOKAHEAD_DAYS = int(os.getenv("CALENDAR_LOOKAHEAD_DAYS", "7"))

# AI conversation summaries (backend/feeds/chats.py). Exports dropped in CHAT_EXPORTS_PATH.
ENABLE_CHATS_FEED = os.getenv("ENABLE_CHATS_FEED", "false").lower() == "true"
CHAT_EXPORTS_PATH = os.getenv("CHAT_EXPORTS_PATH", os.path.expanduser("~/exports"))
CHAT_MIN_CHARS = int(os.getenv("CHAT_MIN_CHARS", "600"))          # shorter conversations are skipped
CHAT_MAX_CHARS = int(os.getenv("CHAT_MAX_CHARS", "60000"))        # longer ones are cut in the middle
CHAT_ACTION_WINDOW_DAYS = int(os.getenv("CHAT_ACTION_WINDOW_DAYS", "14"))
# "single": all notes under 04_Resources/AI_Conversations/<year>/.
# "by_area": each note under the area or resource folder the model picks, in AI_Conversations/.
CHAT_NOTES_FILING = os.getenv("CHAT_NOTES_FILING", "single").lower()
CHAT_NOTES_DIR = "04_Resources/AI_Conversations"
CHAT_EXCLUSIONS_FILE = os.path.join(INTERNAL_PATH, "Chat_Exclusions.md")
# A backfill stops while this much of the month's budget is still left for the other feeds.
FEEDS_BACKFILL_HEADROOM_PENCE = float(os.getenv("FEEDS_BACKFILL_HEADROOM_PENCE", "50"))

# Personal email digest (backend/feeds/mail.py). Read-only Gmail access over OAuth.
ENABLE_EMAIL_FEED = os.getenv("ENABLE_EMAIL_FEED", "false").lower() == "true"
_STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
GOOGLE_CLIENT_FILE = os.getenv("GOOGLE_CLIENT_FILE", os.path.join(_STATE_DIR, "google_client.json"))
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", os.path.join(_STATE_DIR, "google_token.json"))
EMAIL_RULES_FILE = os.path.join(INTERNAL_PATH, "Email_Rules.md")
EMAIL_DAILY_MAX_MESSAGES = int(os.getenv("EMAIL_DAILY_MAX_MESSAGES", "20"))
EMAIL_DAILY_MAX_PENCE = float(os.getenv("EMAIL_DAILY_MAX_PENCE", "3"))
EMAIL_MAX_BODY_CHARS = int(os.getenv("EMAIL_MAX_BODY_CHARS", "6000"))
EMAIL_FIRST_RUN_DAYS = int(os.getenv("EMAIL_FIRST_RUN_DAYS", "7"))
# "single": 04_Resources/Email_Digest/<year>/<month>/. "by_area": <area>/Email/.
EMAIL_NOTES_FILING = os.getenv("EMAIL_NOTES_FILING", "single").lower()
EMAIL_NOTES_DIR = "04_Resources/Email_Digest"

# Household reference backfill (backend/feeds/household.py). The scan itself runs on the
# laptop (backend/feeds/household_scan.py); approved documents arrive here for reading.
HOUSEHOLD_STAGING_PATH = os.getenv("HOUSEHOLD_STAGING_PATH", os.path.join(CHAT_EXPORTS_PATH, "household"))
HOUSEHOLD_FALLBACK_DIR = "04_Resources/Household_Reference"
HOUSEHOLD_MAX_CHARS = int(os.getenv("HOUSEHOLD_MAX_CHARS", "24000"))
HOUSEHOLD_REMINDER_DAYS = int(os.getenv("HOUSEHOLD_REMINDER_DAYS", "30"))
