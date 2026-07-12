# 📘 Second Brain: User Guide & Workflows

This guide outlines the day-to-day workflows for using your Second Brain orchestrator. It covers capture, task management, search, and system maintenance.

---

## 🏃 Workflows & Scenarios

### Scenario A: Mobile Voice Capture ("Walking the Dog")
*   **The Problem:** You have an idea, a task, or a voice memo while away from your desk.
*   **The Workflow:**
    1.  Open your private Telegram channel with the **Second Brain (Ingest) Bot**.
    2.  Record a voice note explaining your thought (e.g., *"Remind me to check the dmz proxy routes tomorrow, and I had an idea for Hermes: we should run it under Docker"*).
    3.  **Local processing:** The bot intercepts the audio, transcribes it via the local GTX 1070 GPU using Whisper, and caches the text.
    4.  **Auto-splitting:** Gemini detects that you mentioned two separate topics (a task and an idea). It splits them into two separate queue items.
    5.  **Filing:** The task is appended to your master `Tasks.md`, and the idea is analyzed against your active projects list and saved inside the [[Hermes]] project folder.
    6.  **Telegram Alert:** You receive a confirmation message from the Ingest Bot indicating where the item was filed (e.g., `Location: 02_Projects/Hermes/Notes`).

### Scenario B: Laptop File Import (Samba Share)
*   **The Problem:** You want to quickly copy files (PDFs, PowerPoints, notes) from your laptop to your Second Brain without using Telegram.
*   **The Workflow:**
    1.  Open your file manager on your laptop and go to the mounted Samba directory `~/SecondBrainInbox/`.
    2.  Copy or drag-and-drop your file directly into the folder.
    3.  **Filing:** Within 15 seconds, the server sweeps the folder, parses the internal text of the file (including slide text and speaker notes for PPTX files), determines the matched project/area, moves the raw file next to the generated note, and deletes it from the inbox to keep it clean.
    4.  You receive a Telegram confirmation containing the filed location (e.g. `Location: 03_Areas/Work/Dstl`).

### Scenario C: Reference Capture vs. Deep Research (Research Brain)
*   **The Problem:** You find an article, user manual, or dense academic paper and want to file it.
*   **The Default Workflow (Lightweight Reference):**
    1. Paste the URL or drop a PDF into `01_Inbox/`.
    2. The orchestrator automatically generates a lightweight summary and saves the files inside `04_Resources/Reference_Notes/` (and `04_Resources/Reference_Notes/Sources/`). No new concepts or maps-of-content are generated, leaving your high-level research graph clean.
*   **The Research Brain Workflow (Deep Graph):**
    1. Paste the URL or drop the PDF, but explicitly include a trigger tag like `/research` or the phrase `"add to research brain"` in your Telegram message, email, or note.
    2. The system triggers the **Research Brain** pipeline: it copies the raw file to `04_Resources/Research_Brain/Sources/` and creates a deep summary page in `04_Resources/Research_Brain/Summaries/`.
    3. It automatically identifies core topics and creates/updates concept pages inside `04_Resources/Research_Brain/Concepts/` (e.g., [[Large Language Models]]), linking them bidirectionally in your graph.

### Scenario D: Conversational Q&A ("Ask Your Brain")
*   **The Problem:** You want to recall details about a past note or query your vault's overall knowledge.
*   **The Workflow:**
    1.  Open your private Telegram channel with the dedicated **Second Brain (Q&A) Bot**.
    2.  Send a message containing your question directly **without any prefix** (e.g., *What is the Syncthing ID of my laptop?*).
    3.  **RAG pipeline:** The orchestrator embeds your question, runs a vector similarity search across all markdown files (including `05_Archive/`) via SQLite-VSS, gathers the top-5 matching paragraphs, and feeds them to Gemini. (The Q&A bot bypasses the ingestion queue entirely, delivering instant responses).
    4.  **Response:** The bot replies with a synthesized answer and links to the source files (e.g., `[[Syncthing IDs for Second Brain]]`) so you can open them in Obsidian.
    5.  **CLI alternative:** From your homeserver, run:
        ```bash
        PYTHONPATH=.. uv run python rag/chat.py --query "What is the Syncthing ID of my laptop?"
        ```

---

## 📋 Master Tasks Management

Your tasks are managed dynamically by the system loop in your main [Tasks.md](file:///home/sdmeers/vault/Tasks.md) file:
*   **Extraction:** Tasks are extracted from voice notes, emails, and dropped documents.
*   **Filing format:** The system writes tasks in standard Obsidian format:
    ```markdown
    - [ ] Task description 📅 2026-07-15 #work #coding (Captured from Telegram on 2026-07-11)
    ```
*   **Smart Dashboard sorting:** Every 15 seconds, the backend reads `Tasks.md`, parses deadline dates (resolving relative text like `📅 tomorrow` relative to creation time), and divides your tasks into `## 🚨 Overdue`, `## 📅 Due Today`, `## 🗓️ Upcoming`, and `## 🗃️ Backlog` sections.
*   **Completed Tasks Archiving:** When you check off a task (`- [x]`), the sorter automatically cuts it from `Tasks.md` and appends it to `Completed_Tasks.md` in your vault, adding a completion date tag and striking it through (e.g. `~~checked task~~ [completed: 2026-07-12]`).

---

## ⚙️ Syncthing Conflict Resolution

Syncthing uses a conflict-free sync model. If you modify the same file (e.g. `Tasks.md`) on both your server and laptop simultaneously, Syncthing will save one version as a conflict file (e.g. `Tasks.sync-conflict-20260711-224000-device.md`).

1.  **Notification:** Your Telegram bot will send you a warning stating that a conflict was detected:
    > ⚠️ **Syncthing Sync Conflicts Detected**
    > - `Tasks.sync-conflict-20260711-224000-device.md`
2.  **Resolution:**
    *   Open Obsidian or your file browser.
    *   Compare the conflict file with the original file.
    *   Merge any missing lines (e.g. newly added tasks) into the original file.
    *   Delete the `.sync-conflict-...` file. The warning will automatically clear on the next loop iteration.
