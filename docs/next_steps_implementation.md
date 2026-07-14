# 🗺️ Second Brain: Next Steps & Implementation Plan

This document outlines the roadmap and technical specifications for the upcoming phases of the Second Brain orchestrator. It incorporates planned integrations (Jira), future features (proactive briefings), and architecture improvements derived from our system reviews.

---

## Phase 8: Intentional Jira Integration (Completed)

The goal of this phase was to connect personal project notes and task lists to Atlassian Jira (`stevenmeers.atlassian.net`) while maintaining complete user control over what enters the corporate backlog.

### 1. Intentional Backlog Injection
*   **The Philosophy:** The system will **never** automatically push items to Jira. Any sync action must be explicitly triggered.
*   **Completed Ingest Triggers:**
    *   **Telegram Command:** Type `/jira [task description]` to immediately create a task in your Jira backlog.
    *   **Telegram Inline Button:** Whenever the bot successfully ingests a note, voice memo, link, or document, it sends a confirmation message with an inline `📤 Send to Jira` button. Clicking this button grabs the generated note title and details, calls the Jira REST API, and updates the Telegram message inline with the ticket key (e.g. `RAID-101`).
*   **Jira Backlog Placement:** Created tasks are filed under your default project (`RAID`) and automatically placed in the backlog.

### 2. Configuration Options
To run the integration, the following environment variables are loaded in `backend/.env`:
*   `JIRA_URL`: The URL of your Jira Cloud instance (default: `https://stevenmeers.atlassian.net`).
*   `JIRA_USER_EMAIL`: The email address associated with your Jira account.
*   `JIRA_API_TOKEN`: Your Atlassian API token.
*   `JIRA_DEFAULT_PROJECT_KEY`: The default project to file tickets under (default: `RAID`).

---

## Phase 9: Proactive Cognitive Synthesis (Future)

Transition the Second Brain from a passive archive to an active intellectual partner through scheduled synthesis runs.

### 1. The Nightly Synthesis Engine
*   **Trigger:** A scheduled systemd timer running nightly at 2:00 AM.
*   **Action:** The engine scans notes created or modified in the last 7 days, matches them against older vector embeddings in the RAG database, and synthesizes connections.
*   **Output:** 
    *   Generates a structured Obsidian note: `00_System/Daily_Digest.md`.
    *   Pings the user via the Telegram Q&A bot with a concise summary (e.g., *"Yesterday you captured research on AI Enabled Swarms. This strongly correlates with the theories in 'Neuroscience.md' regarding decentralized emergent systems."*).

### 2. Tedium & Drift Detection
*   Detects tasks in `Tasks.md` that have been sitting in the backlog for more than 14 days and warns the user about backlog drift.
*   Identifies orphan notes (notes with no incoming or outgoing wikilinks) and suggests logical parent notes to attach them to.

---

## Architecture & System Improvements

Refinements identified during our review of the original system specifications:

### 1. Human-Readable Librarian Activity Log
*   **Target File:** `00_System/Librarian_Activity.md` (Obsidian note).
*   **Behavior:** Instead of relying solely on raw Git commit logs, the queue worker will append a clean, human-readable line to this file on every successful ingestion.
*   **Example Log Entry:**
    *   `- **2026-07-12 21:15:** Ingested image `📝 Driving License Number` to `03_Areas/Family/` (OCR OCR-extracted from image).`
    *   `- **2026-07-12 21:52:** Initialized 4 new project dashboards for `TF RAID`.`

### 2. Decoupled Model Agnosticism
*   **The Goal:** Make the core backend independent of any single model provider (Gemini, Claude, local LLaMA).
*   **Action:** Refactor the API calls in `backend/utils/gemini.py` and the vector database chunking layers into a generic LLM wrapper client. Model choice, embedding models, and credentials will be fully managed via simple `.env` toggles.

### 3. Daily Brain Cleaner
*   Add a scheduled cron job (or systemd user timer) on the homeserver to run `brain_cleaner.py` at 2:00 AM to perform maintenance:
    *   Clean up dangling files and tmp folders in `01_Inbox/`.
    *   Re-index the vector DB for any modified files.
    *   Checkpoint SQLite's Write-Ahead Log (`WAL` database files) to disk.
