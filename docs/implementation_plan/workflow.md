# 🔄 Second Brain Workflows

This document outlines the day-to-day operational workflows for the Second Brain, covering both individual use and team scaling.

## 1. Individual Workflows

### A. The "On-the-Go" Brain Dump
*   **Trigger:** You send a voice note or text message to your private Telegram Bot.
*   **Process:** The Python Orchestrator downloads the audio, transcribes it locally via `faster-whisper`, and passes the text to the LLM.
*   **Outcome:** The LLM splits the text. Groceries go to `Areas/Home/shopping.md`. Project ideas go to `01_Inbox/Idea_Name.md`. Tasks go to `Tasks.md`.

### B. Deep Research (The Centaur Brain Sub-System)
*   **Trigger:** You drop a dense PDF report or book into the `01_Inbox/`.
*   **Process:** The Orchestrator's lightweight router detects a complex document and delegates it to the "Centaur Brain" pipeline.
*   **Outcome:** The specialized Gemini Flash 3.1 prompt executes, extracting ontology, identifying concepts, and writing structured markdown directly into `04_Resources/Concepts/` and `04_Resources/Atlas/`.

### C. Meeting Ingestion & Task Extraction
*   **Trigger:** You drop a Teams meeting transcript (`.docx`) into the `01_Inbox/`.
*   **Process:** The LLM reads the transcript, summarizing decisions and extracting tasks assigned to you. *Critical Rule: The original source transcript is completely immutable.*
*   **Outcome:** The raw transcript is moved untouched to `05_Archive/Sources/`. A newly generated synthesis file is saved in `02_Projects/Project_Name/Meetings/`. Your `Tasks.md` is updated with action items and deadlines.

---

## 2. Team Workflows (Multi-Player Mode)

### A. Shared Inbox via Slack/Teams
*   **Trigger:** A team member tags `@TeamBrain` in a Slack channel with a file or a request.
*   **Process:** The Orchestrator pulls the file from the Slack API.
*   **Outcome:** The AI links the information into the team's shared Knowledge Graph, making it searchable for everyone.

### B. Automatic Task Delegation
*   **Trigger:** A meeting transcript is processed by the Team Brain.
*   **Process:** The AI cross-references the transcript with the team's role definitions in `_MASTER_PROMPT.md`.
*   **Outcome:** It updates the master `Tasks.md` and assigns specific action items to David, Sarah, and John based on who agreed to what in the meeting.

---

## 3. External Integrations (Jira)

### A. Agentic Ticket Creation
*   **Trigger:** The AI identifies a software bug mentioned in a voice note or transcript.
*   **Process:** It calls the Jira API to create a ticket.
*   **Outcome:** A link to the official Jira ticket is appended to the relevant local Markdown project file.

### B. Bidirectional Status Sync
*   **Trigger:** You change `- [ ]` to `- [x]` next to a Jira task in your local Obsidian vault.
*   **Process:** The Python file-watcher detects the file modification and extracts the Jira ID.
*   **Outcome:** An API call is made to Jira to transition the ticket to "Done."
