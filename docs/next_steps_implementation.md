# 🗺️ Second Brain: Next Steps & Implementation Plan

This document outlines the updated roadmap and technical specifications for the upcoming phases of the Second Brain orchestrator. It incorporates your feedback on token efficiency, high-signal synthesis, and avoiding vault file bloat.

---

## Phase 8: Intentional Jira Integration (Completed)
*   **Status:** Done.
*   **Features:** Inline Telegram confirmation buttons (`📤 Send to Jira`) and `/jira [description]` commands. Successfully maps tasks to your `Team Brain` (`SCRUM`) project backlog.

---

## Phase 9: Smart AI Librarian (In Progress / Next)

The goal is to elevate the AI Librarian from a simple organizer to a smart cataloger that automatically tags notes and generates links to related concepts without increasing token costs.

### 1. Structured, Token-Efficient [[Wikilinks]] Generation
To prevent the 1,000+ concepts in the Research Brain from drowning out project-level and area-level matches, candidate retrieval will be segregated by path:
1.  **Project Match:** SQLite-VSS queries `02_Projects/` to find the **single most similar project note** (if similarity is above a threshold).
2.  **Area Match:** SQLite-VSS queries `03_Areas/` to find the **single most similar area note** (if similarity is above a threshold).
3.  **Concept Match:** SQLite-VSS queries `04_Resources/` to find the **top 5 most similar concepts/summaries**.
*   These candidates (max 7) are passed to Gemini, which determines the final relevant connections to write as frontmatter:
    ```yaml
    related: ["[[TF RAID - AI Enabled Swarms]]", "[[Agentic AI]]"]
    ```

### 2. Standardized Classification Tags (with `/research` exceptions)
Gemini will automatically assign standardized nested tags to ingested notes to keep them organized:
*   **Standard Ingest Tags:**
    *   **Types:** `#type/idea`, `#type/task`, `#type/concept`, `#type/meeting_transcript`, `#type/reference`, `#type/summary`
    *   **Review Status:** `#status/to-review`
*   **`/research` Exception:** Notes ingested with the `/research` tag (destined for the Research Brain) **will bypass these standard tags** to avoid cluttering your research vault. Instead, they will use optional topic tags (e.g. `#research/ai`).

### 3. Human-Readable Librarian Activity Log
*   **Target File:** `00_System/Librarian_Activity.md` (Obsidian note).
*   **Behavior:** Appends a clean, human-readable line to this file on every successful ingestion.
*   **Example Log Entry:**
    *   `- **2026-07-18 10:43:** Ingested voice note [[Add Audio Responses to Bot]] to `02_Projects/Second Brain/Notes/` (transcribed from audio).`

---

## Phase 10: Voice Q&A Upgrade

Enhance the **Q&A Bot** to handle speech queries when you are on the move.
*   **The Flow:**
    1.  You send a voice note query to the Q&A Bot.
    2.  The bot downloads the `.ogg` file and transcribes it using the existing backend audio pipeline.
    3.  The transcription is fed directly into the RAG search engine as a text query.
    4.  The bot responds with a structured text answer.

---

## Phase 11: High-Signal Cognitive Synthesis & Maintenance

A consolidated nightly cron job (or systemd user timer) running at **2:00 AM** to clean the vault, manage retention, and generate your proactive briefing.

### 1. High-Signal Daily Digest
To avoid vault file bloat, the engine will overwrite a single file: **`00_System/Daily_Digest.md`** every night. It will consist of:
*   **🔍 Conceptual Connections:** If and only if a strong connection exists between yesterday's notes and older files, Gemini will highlight it. No forced or generic links.
*   **💡 Idea Builder:** If you ingested an idea note, Gemini will propose 2-3 technical implementation steps or extensions to build on it.
*   **⚠️ Task & Drift Reminders:**
    *   Lists open tasks in `Tasks.md` created >14 days ago.
    *   Alerts you to "Stagnant Ideas" (notes tagged `#status/to-review` that haven't been touched in 7 days).

### 2. Daily Brain Cleaning & Audio Retention Policy
The nightly run will also execute maintenance tasks:
*   **30-Day Audio Cleanup:** Scan your vault for raw voice files (`*_raw.ogg`) that are **older than 30 days and delete them** to prevent storage bloat, leaving the text transcripts intact.
*   Clean up dangling files and tmp folders in `01_Inbox/` and `staging/`.
*   Re-index the vector DB for any modified files.
*   Checkpoint SQLite's Write-Ahead Log (`WAL` database files) to disk.

---

## Phase 12: Decoupled Model Agnosticism (Future)

*   **The Goal:** Make the core backend independent of any single model provider (Gemini, Claude, local LLaMA).
*   **Action:** Refactor API calls in `backend/utils/gemini.py` and the vector database chunking layers into a generic LLM wrapper client. Model choice, embedding models, and credentials will be fully managed via simple `.env` toggles.

---

## Phase 13: Voice Output (Deferred / Future)
*   **The Goal:** Upgrade the Q&A bot to respond with an audio note as well as text (Text-to-Speech).
*   *Note: Not for current implementation. Deferred to minimize complexity in the short term.*
