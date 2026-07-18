# 🗺️ Second Brain: Next Steps & Implementation Plan

This document outlines the updated roadmap and technical specifications for the upcoming phases of the Second Brain orchestrator. It incorporates feedback on token efficiency, high-signal synthesis, and avoiding vault file bloat.

---

## Phase 8: Intentional Jira Integration (Completed)
*   **Status:** Done.
*   **Features:** Inline Telegram confirmation buttons (`📤 Send to Jira`) and `/jira [description]` commands. Successfully maps tasks to your `Team Brain` (`SCRUM`) project backlog.

---

## Phase 9: Smart AI Librarian (Next)

Elevate the AI Librarian from a simple organizer to a smart cataloger that automatically tags notes and generates [[Wikilinks]] to related projects, areas, and concepts — without increasing token costs.

### 1. Structured, Token-Efficient [[Wikilinks]] Generation

**The Problem:** SQLite-VSS returns global nearest neighbours and cannot filter by path prefix during vector search. With 1,000+ concepts in the Research Brain, an unfiltered search would link almost everything to a concept and miss the more important project/area connections.

**The Solution: Local Over-Fetch + Path-Filtered Post-Processing**
1.  Generate the embedding for the newly ingested note (free, local).
2.  Query SQLite-VSS for the **top 200** nearest neighbours across the entire vault (all local, zero token cost).
3.  In Python, post-filter the results into 3 buckets:
    *   **Best Project Match:** The single closest result whose `file_path` matches a **project-level index note** (e.g. `02_Projects/Second Brain/Second Brain.md`, `02_Projects/TF RAID/TF RAID.md`). Individual sub-notes within project folders are excluded to avoid noisy matches.
    *   **Best Area Match:** The single closest result whose `file_path` matches a **top-level area index note** (e.g. `03_Areas/Health/Health.md`). Same logic — match the area itself, not a random note buried inside it.
    *   **Top 5 Concept Matches:** The 5 closest results from `04_Resources/`.
4.  Apply a configurable **similarity threshold** (`WIKILINK_SIMILARITY_THRESHOLD` in `.env`). Discard any candidate above this distance. Initial calibration: ingest 10 test items, log distances, set threshold at the empirical 70th percentile (likely ~0.85).
5.  Pass the surviving candidates (max 7 titles) to Gemini. Gemini determines which are genuinely related and outputs only the meaningful ones as frontmatter:
    ```yaml
    related: ["[[TF RAID]]", "[[Agentic AI]]"]
    ```

### 2. Standardized Classification Tags
Gemini will automatically assign standardized nested tags to ingested notes:
*   **Types:** `#type/idea`, `#type/task`, `#type/concept`, `#type/meeting_transcript`, `#type/reference`, `#type/summary`, `#type/voice_note`
*   **Review Status:** `#status/to-review` (default for new incoming ideas or resources).
*   **`/research` Exception:** Notes ingested with the `/research` tag (destined for the Research Brain) **will not receive any standard tags**. The Research Brain's own organisational structure (Atlas, Concepts, Sources, Summaries) is self-sufficient.

### 3. Human-Readable Librarian Activity Log
*   **Target File:** `00_System/Librarian_Activity.md` (Obsidian note).
*   **Behavior:** Appends a clean, human-readable line to this file on every successful ingestion.
*   **Example Log Entry:**
    *   `- **2026-07-18 10:43:** Ingested voice note [[Add Audio Responses to Bot]] to 02_Projects/Second Brain/Notes/ (transcribed from audio).`

---

## Phase 10: Voice Q&A Upgrade

Enhance the **Q&A Bot** to handle speech queries when you are on the move.
*   **The Flow:**
    1.  You send a voice note query to the Q&A Bot (the dedicated Q&A Telegram bot).
    2.  Extend `poll_telegram_qa()` in `telegram.py` to detect `message.voice` objects, download the `.ogg` file via the Telegram Bot API, and transcribe it using the existing Gemini audio pipeline.
    3.  The transcription is fed directly into `ask_second_brain()` as a text query.
    4.  The bot responds with a structured text answer.

---

## Phase 11: High-Signal Cognitive Synthesis & Maintenance

A consolidated nightly cron job (or systemd user timer) running at **2:00 AM** to clean the vault, manage retention, and generate your proactive briefing.

### 1. High-Signal Daily Digest
The engine writes to **`00_System/Digests/YYYY-MM-DD.md`** each night, with a rolling **14-day retention window** (digests older than 14 days are automatically deleted). This provides a two-week lookback without unbounded file growth.

Each digest will consist of:
*   **🔍 Conceptual Connections:** If and only if a strong semantic connection exists between yesterday's notes and older files, Gemini will highlight it. No forced or generic links. If nothing meaningful exists, this section is omitted entirely.
*   **💡 Idea Builder:** If you ingested an idea note tagged `#type/idea`, Gemini will propose 2-3 concrete implementation steps or extensions. If no new ideas were captured, this section is omitted.
*   **⚠️ Task & Drift Reminders:**
    *   Lists open tasks in `Tasks.md` created >14 days ago.
    *   Alerts you to "Stagnant Ideas":
        *   **Tagged notes:** Notes with `#status/to-review` that haven't been modified in 7+ days.
        *   **Untagged fallback (bootstrapping):** On first run and until tag coverage is complete, also scan `02_Projects/Ideas/` for any `.md` files not modified in 7+ days, regardless of tags.

### 2. Telegram Morning Briefing
*   At **7:00 AM**, the bot sends a concise 3-bullet summary of the latest digest to the Q&A Telegram bot.

### 3. Daily Brain Cleaning & Audio Retention Policy
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
