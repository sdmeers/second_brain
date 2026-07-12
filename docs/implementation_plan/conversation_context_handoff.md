# 🧠 Second Brain: Conversation Context & Handoff

**ATTENTION NEXT AI AGENT:** Read this document carefully. It contains the complete context of the design phase conducted between the user and the previous AI agent. You are taking over the implementation phase on the user's home server.

## 1. Project Background
The user previously built a "Centaur Brain"—a system using FastAPI and a specialized Gemini Flash 3.1 prompt to extract complex ontologies from deep research materials (books, papers). 
The user now wants to expand this into a comprehensive **"Second Brain" (or Team Brain)** that acts as an "Agent-as-Librarian." It needs to handle messy, everyday inputs (voice notes, emails, meeting transcripts) alongside deep research.

## 2. The Core Architectural Decisions
We agreed on a **Files-as-API** architecture.
*   **Plain Text Foundation:** There are no complex SQL databases. The entire system is built on Markdown (`.md`) files inside a modified P.A.R.A. folder structure.
*   **The Orchestrator:** A lightweight Python backend (using `uv` for package management and `SQLite` for local vector/RAG tracking). It watches an Inbox and routes files.
*   **The Sub-System:** The existing "Centaur Brain" logic will be ported over. The Orchestrator will route small tasks (groceries, quick emails) using a cheap, fast prompt, but will delegate dense PDFs/books to the heavy Centaur Brain prompt for ontology extraction.
*   **Immutability:** Source documents (transcripts, PDFs) are never modified by the AI. They are archived. The AI's synthesis (tasks, summaries) is written to separate files.

## 3. Deployment Topology (The "Always-On" Setup)
The user is deploying this on a local **Home Server (Fedora Linux)** to achieve a free, 24/7 cloud-like experience with total privacy.
*   **Capture:** A Telegram Bot (long-polling) allows the user to send voice notes and files to the home server from anywhere in the world, bypassing home firewalls.
*   **Sync (Syncthing):** The user will access the vault on their laptop via Obsidian. We are using **Syncthing** for real-time synchronization.
*   **The Inbox Sync Fix:** To prevent the AI and the laptop from fighting over files during ingestion, the `01_Inbox/` folder is explicitly excluded from Syncthing via a `.stignore` file. The Inbox exists *only* on the server. All other folders (`Tasks`, `Projects`, `Resources`) are bidirectionally synced so the user can check off tasks manually in Obsidian.

## 4. Future Integrations Discussed
*   **Jira:** We designed a future bridge where the Python Orchestrator can create Jira tickets from meeting transcripts, pull active ticket status into local Markdown files for RAG context, and push status updates back to Jira when the user checks a box in Obsidian.
*   **Team Scaling:** We discussed that this architecture scales perfectly to a team environment (replacing Telegram with Slack/Teams, and using Git for version control to track who changed what).

## 5. Next Steps for You (The Implementing Agent)
You have been provided with three key files:
1.  **`implementation_plan.md`:** The phased coding blueprint you must follow.
2.  **`workflow.md`:** The user's day-to-day operational logic.
3.  **`interview.md`:** A strict guide on how you must interview the user *before* you write any code.

**Your Immediate Action:**
Read `interview.md` and initiate the interview with the user. Do not generate the System Constitution (`_MASTER_PROMPT.md`, `_WORKFLOWS.md`, `_INDEX.md`) until the interview is complete and the user has approved your proposed design. Do not write any Python code until Phase 1 (Foundation) is complete.
