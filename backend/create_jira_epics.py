import os
import json
import subprocess
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

JIRA_BASE_URL = os.getenv("JIRA_URL", "https://your-domain.atlassian.net")
JIRA_URL = f"{JIRA_BASE_URL.rstrip('/')}/rest/api/3/issue"
JIRA_EMAIL = os.getenv("JIRA_USER_EMAIL", "")
JIRA_TOKEN = os.getenv("JIRA_API_TOKEN", "")
AUTH = f"{JIRA_EMAIL}:{JIRA_TOKEN}"

def create_issue(payload):
    result = subprocess.run(
        ["curl", "-s", "-u", AUTH, "-H", "Content-Type: application/json",
         "-X", "POST", "--data", json.dumps(payload), JIRA_URL],
        capture_output=True, text=True
    )
    return json.loads(result.stdout)

def delete_issue(key):
    result = subprocess.run(
        ["curl", "-s", "-u", AUTH, "-X", "DELETE", f"{JIRA_URL}/{key}"],
        capture_output=True, text=True
    )
    return result.returncode == 0

# Step 1: Delete old epics (SCRUM-5 through SCRUM-13)
print("Cleaning up old epics...")
for i in range(5, 14):
    key = f"SCRUM-{i}"
    delete_issue(key)
    print(f"  Deleted {key}")

# Step 2: Create the parent Epic
print("\nCreating parent Epic...")
epic_payload = {
    "fields": {
        "project": {"key": "SCRUM"},
        "summary": "Second Brain Build",
        "description": {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "Complete build of the AI-powered Second Brain system: vault setup, backend refactor, ingestion pipeline, task management, RAG, voice capture, and integrations."}
            ]}]
        },
        "issuetype": {"id": "10001"}  # Epic
    }
}
epic_resp = create_issue(epic_payload)
epic_key = epic_resp.get("key", "FAILED")
print(f"{'✅' if epic_key != 'FAILED' else '❌'} Epic: {epic_key}")
if epic_key == "FAILED":
    print(f"  Error: {epic_resp}")
    exit(1)

# Step 3: Create Stories under the Epic
stories = [
    {
        "summary": "Phase 0: Interview & System Constitution ✅",
        "description": "COMPLETE. Conducted AI interview. Produced _MASTER_PROMPT.md, _WORKFLOWS.md, _PREFERENCES.md, _INDEX.md, _CHANGELOG.md. Created vault P.A.R.A. folder structure in /home/sdmeers/vault. Git initialized."
    },
    {
        "summary": "Phase 1: Vault Setup & Data Migration",
        "description": "Migrate existing Centaur Brain content into P.A.R.A. structure (01 Sources → 04_Resources/Sources/, 02 Summaries → 04_Resources/Summaries/, etc.). Update all wikilinks. Configure Syncthing between server and laptop. ~2 hours."
    },
    {
        "summary": "Phase 2: Backend Refactor",
        "description": "Decompose 1,482-line main.py into modules: config.py, ingestion/ (router, queue, telegram, email, watcher), processing/ (extractor, brain_node, task_extractor, transcriber), vault/ (writer, index, concepts, tasks), utils/. All existing functionality must still work. ~4-6 hours."
    },
    {
        "summary": "Phase 3: Unified Ingestion Pipeline",
        "description": "SQLite-backed ingestion queue. LLM router with structured JSON output (Gemini Flash Lite). Filesystem watchdog for 01_Inbox/. Unify Telegram, file drop, email into single queue. NEEDS_REVIEW routing for low-confidence items. ~3-4 hours."
    },
    {
        "summary": "Phase 4: Task Extraction & Daily Operations",
        "description": "Task extractor (structured LLM output). Tasks.md management. Morning briefing via Telegram (Mon/Fri 07:45, Tue-Thu 07:15, Sat-Sun 08:30). Task completion detection. Auto-git-commit. Auto-update _INDEX.md. ~3-4 hours."
    },
    {
        "summary": "Phase 5: RAG & Conversational Queries",
        "description": "SQLite-VSS for vector storage. Embedding pipeline (Gemini text-embedding-004 + local fallback). Incremental indexing. RAG chat via Telegram and CLI — ask questions, get sourced answers from the vault. ~4-6 hours."
    },
    {
        "summary": "Phase 6: Voice & Audio",
        "description": "faster-whisper with CUDA on GTX 1070 (large-v3 model). Telegram voice notes auto-transcribed and routed. Multi-topic splitting for brain dumps on dog walks. Transcription caching. ~2-3 hours."
    },
    {
        "summary": "Phase 7: Polish & Resilience",
        "description": "Atomic file writes. Queue resilience. Structured logging. Remote Git backup. Syncthing conflict detection. Health check endpoint. systemd service. Obsidian mobile. Telegram rollback. ~3-4 hours."
    },
    {
        "summary": "Phase 8: External Integrations (Deferred)",
        "description": "Jira bidirectional sync with stevenmeers.atlassian.net. Jira → Markdown, Markdown → Jira, agentic ticket creation. Deferred until core is stable."
    },
]

print(f"\nCreating stories under {epic_key}...")
for story in stories:
    payload = {
        "fields": {
            "project": {"key": "SCRUM"},
            "summary": story["summary"],
            "description": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph", "content": [
                    {"type": "text", "text": story["description"]}
                ]}]
            },
            "issuetype": {"id": "10004"},  # Story
            "parent": {"key": epic_key}
        }
    }
    resp = create_issue(payload)
    key = resp.get("key", "FAILED")
    print(f"  {'✅' if key != 'FAILED' else '❌'} {key}: {story['summary']}")
    if key == "FAILED":
        print(f"     Error: {resp}")

print("\nDone!")
