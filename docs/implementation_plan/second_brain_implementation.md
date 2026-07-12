# 🧠 Second Brain: Implementation & Architecture Guide

This document outlines the vision, daily workflows, and technical implementation for an **Agent-as-Librarian**, local-first Second Brain. It is designed to act as both a conceptual blueprint for the user and a concrete implementation guide for any AI coding agent executing the build.

---

## 🌟 1. The Vision & Benefits

The goal is to transition from a **Passive Archive** (where you manually file information for later) to an **Active Operating System** (where an AI agent manages your knowledge, extracts your tasks, and assists your thinking).

### Core Benefits
*   **Zero-Friction Capture:** Capture thoughts, meeting transcripts, PDFs, or emails without worrying about formatting or folder structures. The AI handles the sorting.
*   **Focus on Execution, Not Organization:** Stop acting as a data-entry clerk. You provide the raw input; the AI extracts the tasks, links the concepts, and files the reference material.
*   **Total Ownership & Portability:** No vendor lock-in. No complex proprietary databases. The entire system is built on plain text `.md` (Markdown) files stored locally on your hard drive, accessible via Obsidian or any text editor.
*   **Conversational Retrieval (RAG):** Stop searching for keywords. Ask your Brain questions like, *"Synthesize my notes on market expansion strategies and draft an email to the team."*
*   **Self-Evolving System:** If your workflow changes, you don't rewrite code. You simply tell the AI to update its own `_WORKFLOWS.md` instruction file.

---

## 🗓️ 2. Day-to-Day Workflows & Interactions

How you will actually use this system day-to-day:

### A. The Messy Brain Dump (Voice & Text)
*   **The Action:** You are walking the dog. You open a Telegram bot linked to your local machine and record a 3-minute voice note combining a grocery list, a project idea, and a reflection on a podcast.
*   **The Agent's Job:** Transcribe the audio, read your workflow rules, split the messy input into three distinct outputs, append the groceries to your `shopping_list.md`, draft a new idea note in `01_Inbox/`, and link the podcast reflection to your Knowledge Graph.

### B. The Meeting & Document Ingestion
*   **The Action:** You receive a Teams meeting transcript (`.docx`), a PowerPoint presentation (`.pptx`), or an important forwarded email. You drop them into your `01_Inbox/` folder or forward them to the Telegram bot.
*   **The Agent's Job:** 
    *   *Meeting:* Summarize key decisions, extract action items (assigned to you) and append them to `Tasks.md` with deadlines, and link to the relevant active project file.
    *   *Presentation:* Extract the core arguments, summarize them, map them to your ontology (Concepts/Themes), and save as a Resource.
    *   *Email:* Identify if it's an actionable task or reference material, and route accordingly.

### C. The Morning Briefing
*   **The Action:** You open your command line or local chat UI and type: *"Morning briefing."*
*   **The Agent's Job:** Scan your `Tasks.md`, check your active `02_Projects/` folder, and summarize what is due today, surfacing any stalled projects or recently ingested articles you might want to review.

### D. Deep Work Synthesis (RAG)
*   **The Action:** You need to write a proposal. You ask the chat interface: *"Based on my notes and the transcripts from last week's meetings, draft a one-page proposal for the Alpha project."*
*   **The Agent's Job:** Use vector search to retrieve the relevant meeting transcripts and concept notes from your vault, synthesize the information, and stream back the draft.

---

## 🛠️ 3. Design & Implementation Guide (For the Coding Agent)

This system abandons complex SQL databases in favor of a **Files-as-API** approach. The File System *is* the database.

### 3.1 Folder Structure (Modified P.A.R.A.)
The root of the Obsidian Vault should be structured as follows:

```text
Vault/
├── 00_System/               # The AI's Constitution and Instructions
│   ├── _INDEX.md            # Directory of the vault for the AI
│   ├── _MASTER_PROMPT.md    # The core persona and operating rules
│   └── _WORKFLOWS.md        # Specific logic for handling Tasks, Meetings, Voice notes, etc.
├── 01_Inbox/                # Universal drop-zone (Telegram, Emails, manual drops)
├── 02_Projects/             # Active, ongoing work with clear endpoints
├── 03_Areas/                # Ongoing responsibilities (Health, Finances, Management)
├── 04_Resources/            # Your Knowledge Graph (Summaries, Atlas, Concepts)
│   ├── Summaries/           # Source summaries (Articles, PDFs, Transcripts)
│   ├── Concepts/            # Extracted ontology nodes
│   └── Atlas/               # High-level themes / Maps of Content
├── 05_Archive/              # Inactive projects and old data
└── Tasks.md                 # A master task file managed by the AI
```

### 3.2 The Python Backend (Lightweight Orchestrator)
The backend `main.py` should be significantly refactored from the previous Centaur Brain. It should **not** contain hardcoded prompts. It acts purely as middleware.

1.  **Watchdog / Listener:** Monitors the `01_Inbox/` folder for new files and polls the Telegram API for messages/files.
2.  **Context Assembly:** When a new file appears, it reads `_MASTER_PROMPT.md` and `_WORKFLOWS.md` to establish context.
3.  **LLM Invocation:** It passes the new file + the System Constitution to the LLM.
4.  **Execution:** It parses the LLM's structured output (which dictates file operations) and performs the file creation/moves/appends on the local filesystem.
5.  **Vector Sync:** Whenever a markdown file is changed, a lightweight background process updates a local vector index (e.g., ChromaDB or FAISS) to ensure the RAG chatbot always has up-to-date context.

### 3.3 The Conversational Interface
A simple local UI (e.g., a lightweight Streamlit app, a dedicated terminal CLI, or just Telegram) that connects to the RAG pipeline. This allows the user to query the vault and command the AI to update its own instruction files.

### 3.4 The Hosting Topology (Local Server + Global Access)
To achieve a 24/7 "always-on" brain without paying for cloud hosting (like GCP), the system uses a distributed local architecture:
1.  **The Home Server (The Core):** A local machine (e.g., a Raspberry Pi, Mac Mini, or home lab server) runs the Python Orchestrator 24/7. It holds the "Master Vault" of all markdown files and runs the local vector database.
2.  **Mobile Capture (Telegram):** The Python orchestrator long-polls the Telegram API. This means you can send voice notes or files to your bot from anywhere in the world, and your home server will pull them down instantly, bypassing home firewalls without needing port-forwarding.
3.  **Laptop Sync (Syncthing):** To interact with your vault via Obsidian on your laptop, you use **Syncthing** (a free, peer-to-peer file synchronization tool). Even if you are at a coffee shop, Syncthing securely tunnels through NAT/firewalls to sync your laptop's Obsidian folder with the home server's Master Vault in real-time.

## 🤝 4. Scaling to a Team Brain (Multi-Player Mode)

The architecture described above translates brilliantly to a team environment, shifting the AI's role from a "Personal Assistant" to a "Chief of Staff." However, multi-player mode introduces specific architectural changes.

### The Multiplier Effect
*   **Collective Intelligence:** When team members drop meeting transcripts, reports, or research into the shared Inbox, the AI links it all into a single Knowledge Graph. A developer can ask the AI about a client requirement that the sales team ingested a week prior.
*   **Automatic Task Delegation:** Instead of just extracting a task, the AI *routes* it. Reading a transcript, it notes, *"David agreed to update the slides,"* and updates the master `Tasks.md` while assigning it to David's personal queue.
*   **Instant Onboarding:** New team members can ask the Team Brain for the history of a project, key decisions, and active blockers without digging through months of chat history.

### Required Architectural Shifts
To support a team, the foundational architecture needs a few upgrades:

1.  **Version Control (Mandatory):** Flat files break when five people and an AI agent edit them simultaneously. The entire Vault must be backed by a **Git repository** to handle merge conflicts and maintain an audit trail.
2.  **Privacy & Boundaries:** A Team Brain must be strictly professional. Individuals will likely maintain *two* systems: a local personal brain, and the shared Team Brain.
3.  **The Capture Interface (Slack/Teams):** Instead of a personal Telegram bot, the Python orchestrator watches a dedicated Slack or Microsoft Teams channel. Tagging `@TeamBrain` with a file or request triggers the agent.
4.  **Multi-User Constitution:** The `_MASTER_PROMPT.md` must map the team's roles (e.g., *"Sarah is Lead Dev, David handles Marketing"*), so the AI knows how to assign tasks and direct queries appropriately.
5.  **Infrastructure:** The Python backend and vector database move from a local laptop to an always-on server (VPS or office machine) to ensure 24/7 availability for all team members.

---

## 🔗 5. External Integrations (Future-Proofing via APIs)

Because the Python backend acts as an orchestrator, it can act as a two-way bridge between your personal markdown notes and external tools like Jira, Trello, or Linear.

### The Jira Use Case
1.  **Agentic Ticket Creation (Markdown -> Jira):** The AI reads a messy meeting transcript dropped in the `01_Inbox/`, identifies a software bug, and automatically hits the Jira API to create a ticket. It then drops a link to the new ticket in your local `Tasks.md`.
2.  **Live Syncing (Jira -> Markdown):** A background task polls Jira for tickets assigned to you that are "In Progress." It creates/updates a markdown file in your `02_Projects/` folder, pulling the acceptance criteria into plain text so your local RAG chatbot knows exactly what you are working on.
3.  **Bidirectional Status Updates:** You check off a task in your local Obsidian vault (`- [x] PROJ-123`). The Python file-watcher notices the markdown change and fires an API request to Jira to transition the official ticket to "Done."
4.  **Live Chat Queries:** When you ask your chatbot, *"What did I accomplish this week?"*, it uses the Model Context Protocol (MCP) to ping Jira live, merging your official ticket history with your private local notes.

---

## 💰 6. Cost Reality Check: How to Achieve Near-Zero Cost

A common concern is that constantly prompting an LLM and transcribing audio will be prohibitively expensive. For a solopreneur or individual user in 2026, **this is entirely realistic to run for $0/month.**

Here is the breakdown of how to architect this for free:

### A. LLM Prompting & Reasoning (Cost: $0)
*   **The Solution:** Google's Gemini API Free Tier.
*   **The Reality:** The free tier for Gemini Flash and Flash Lite is exceptionally generous (e.g., 1,500 requests per minute / 1 million tokens per minute). Even if the agent runs a background reasoning loop 50 times a day to process your inbox, you will not come close to hitting the free tier limits for personal use.

### B. Audio Transcription (Cost: $0)
*   **The Solution:** Local processing via `faster-whisper`.
*   **The Reality:** You do not need to send audio to a paid API (like OpenAI's Whisper API). Python libraries like `faster-whisper` run highly optimized models directly on your local CPU. A 5-minute voice note can be transcribed locally in seconds for absolutely free. Furthermore, this guarantees complete privacy for your spoken thoughts. (Alternatively, the Gemini Audio API handles multimodal audio natively within the free tier).

### C. Vector Embeddings for RAG (Cost: $0)
*   **The Solution:** Local embedding models or Gemini's free tier.
*   **The Reality:** You can generate embeddings locally using libraries like `SentenceTransformers` (`all-MiniLM-L6-v2`), which run effortlessly on a standard CPU. Alternatively, Gemini's `text-embedding-004` is included in the generous free tier. The vector database itself (e.g., ChromaDB or SQLite with `sqlite-vss`) runs locally for free.

### D. Hosting & Infrastructure (Cost: $0)
*   **The Solution:** Localhost + Obsidian.
*   **The Reality:** Because the backend is a Python script running on your local machine, and the frontend is Obsidian (free) and Telegram (free), there are no cloud hosting costs, no database subscription fees, and no SaaS subscriptions.

**Conclusion:** The only "cost" is the initial time investment to set up the Python orchestrator and conduct the AI interview to dial in your `_WORKFLOWS.md` instructions. Month-to-month, the system runs on your own hardware and free-tier APIs.
