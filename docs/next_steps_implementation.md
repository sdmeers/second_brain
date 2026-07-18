# 🗺️ Second Brain: Next Steps & Implementation Plan

This document outlines the updated roadmap and technical specifications for the upcoming phases of the Second Brain orchestrator. It incorporates your feedback on token efficiency, high-signal synthesis, and avoiding vault file bloat.

---

## Phase 8: Intentional Jira Integration (Completed)
*   **Status:** Done.
*   **Features:** Inline Telegram confirmation buttons (`📤 Send to Jira`) and `/jira [description]` commands. Successfully maps tasks to your `Team Brain` (`SCRUM`) project backlog.

---

## Phase 9: Smart AI Librarian (In Progress / Next)

The goal is to elevate the AI Librarian from a simple organizer to a smart cataloger that automatically tags notes and generates links to related concepts without increasing token costs.

### 1. Token-Efficient [[Wikilinks]] Generation
*   **The Pipeline:**
    1.  When a note is ingested, its embedding is generated.
    2.  The system runs a local SQLite-VSS query to find the **top 8 most semantically similar notes** in your vault.
    3.  Only these 8 candidate titles are sent to Gemini.
    4.  Gemini evaluates the candidates and output links *only if* a strong conceptual connection exists.
*   **Output Format:** Appended in the note's frontmatter:
    ```yaml
    related: ["[[Agentic AI]]", "[[S&T Programme Planning]]"]
    ```

### 2. Standardized Classification Tags
Gemini will automatically assign standardized nested tags to ingested notes to keep them organized:
*   **Types:** `#type/idea`, `#type/task`, `#type/concept`, `#type/meeting`, `#type/project`
*   **Review Status:** `#status/to-review` (default for new incoming ideas or resources).

### 3. Human-Readable Librarian Activity Log
*   **Target File:** `00_System/Librarian_Activity.md` (Obsidian note).
*   **Behavior:** Instead of relying on raw git logs, the queue worker will append a clean, human-readable line to this file on every successful ingestion.
*   **Example Log Entry:**
    *   `- **2026-07-18 10:43:** Ingested voice note [[Add Audio Responses to Bot]] to `02_Projects/Second Brain/Notes/` (transcribed from audio).`

---

## Phase 10: Voice Q&A Upgrade

Enhance the **Q&A Bot** to handle speech queries when you are on the move.
*   **The Flow:**
    1.  You send a voice note query to the Q&A Bot.
    2.  The bot downloads the `.ogg` file and transcribes it using the existing backend audio pipeline.
    3.  The transcription is fed directly into the RAG search engine as a text query.
    4.  The bot responds with a structured text answer (TTS audio response is deferred to a later phase).

---

## Phase 11: High-Signal Cognitive Synthesis & Maintenance

A consolidated nightly cron job (or systemd user timer) running at **2:00 AM** to clean the vault, re-index, and generate your proactive briefing.

### 1. High-Signal Daily Digest
To avoid vault file bloat, the engine will overwrite a single file: **`00_System/Daily_Digest.md`** every night. It will consist of:
*   **🔍 Conceptual Connections:** If and only if a strong connection exists between yesterday's notes and older files, Gemini will highlight it. No forced or generic links.
*   **💡 Idea Builder:** If you ingested an idea note, Gemini will propose 2-3 technical implementation steps or extensions to build on it.
*   **⚠️ Task & Drift Reminders:**
    *   Lists open tasks in `Tasks.md` created >14 days ago.
    *   Alerts you to "Stagnant Ideas" (notes tagged `#status/to-review` that haven't been touched in 7 days).

### 2. Daily Brain Cleaning
The nightly run will also execute maintenance tasks:
*   Clean up dangling files and tmp folders in `01_Inbox/` and `staging/`.
*   Re-index the vector DB for any modified files (ensuring manual moves on your laptop are clean).
*   Checkpoint SQLite's Write-Ahead Log (`WAL` database files) to disk.

---

## Phase 12: Decoupled Model Agnosticism (Future)

*   **The Goal:** Make the core backend independent of any single model provider (Gemini, Claude, local LLaMA).
*   **Action:** Refactor API calls in `backend/utils/gemini.py` and the vector database chunking layers into a generic LLM wrapper client. Model choice, embedding models, and credentials will be fully managed via simple `.env` toggles.
