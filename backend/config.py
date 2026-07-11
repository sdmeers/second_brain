import os
from dotenv import load_dotenv

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=dotenv_path)

OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

if not GEMINI_API_KEY:
    print(f"CRITICAL: GEMINI_API_KEY not found in {dotenv_path}")
    raise RuntimeError(f"CRITICAL: GEMINI_API_KEY missing from {dotenv_path}")

if not OBSIDIAN_VAULT_PATH:
    raise RuntimeError("CRITICAL: OBSIDIAN_VAULT_PATH missing from .env")

# Ensure Obsidian folders exist (using P.A.R.A. folders)
SUMMARIES_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "04_Resources", "Summaries")
SOURCES_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "04_Resources", "Sources")
ATLAS_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "04_Resources", "Atlas")
CONCEPTS_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "04_Resources", "Concepts")

# New P.A.R.A paths
INBOX_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "01_Inbox")
INBOX_FAILED_PATH = os.path.join(INBOX_PATH, "Failed")

IMPORT_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "01_Inbox")
INBOX_MD_FILE = os.path.join(IMPORT_PATH, "Inbox.md")
COMPLETED_MD_FILE = os.path.join(IMPORT_PATH, "Completed.md")
FAILED_MD_FILE = os.path.join(IMPORT_PATH, "Failed.md")

os.makedirs(SUMMARIES_PATH, exist_ok=True)
os.makedirs(SOURCES_PATH, exist_ok=True)
os.makedirs(ATLAS_PATH, exist_ok=True)
os.makedirs(CONCEPTS_PATH, exist_ok=True)
if os.getenv("ENABLE_OBSIDIAN_INBOX_POLLING", "false").lower() == "true":
    os.makedirs(INBOX_PATH, exist_ok=True)
    os.makedirs(INBOX_FAILED_PATH, exist_ok=True)
os.makedirs(IMPORT_PATH, exist_ok=True)

# Initialize import files if they do not exist
for filepath in [INBOX_MD_FILE, COMPLETED_MD_FILE, FAILED_MD_FILE]:
    if not os.path.exists(filepath):
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("")
        except Exception as e:
            print(f"Backend [Startup]: Error creating initialization file {filepath}: {e}")

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ALLOWED_CHAT_IDS = []
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
