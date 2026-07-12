# 🎯 Second Brain: Product Description

The **Second Brain** project is designed to solve the problem of information overload by establishing a **passive knowledge vault** managed by an **active agentic librarian**. 

Unlike modern "walled garden" knowledge apps that store your thoughts in proprietary cloud databases, Second Brain is founded on the principle of **absolute data ownership**—your thoughts are stored as plain markdown files on your own filesystem. The AI orchestrator acts as a background processor that cleans, catalogues, cross-references, and retrieves your information on command.

---

## 🏛️ Core Architectural Philosophies

1. **Passive Vault, Active Librarian:**
   The files in the vault must remain 100% human-readable and standard-compliant. The AI orchestrator operates out-of-band: it reads the vault, writes notes, updates indexes, and links nodes, but does not lock you into a custom interface. You can open, edit, or search your vault using Obsidian, VS Code, grep, or any other tool.

2. **The "Research Brain" Ontology Pipeline:**
   A key highlight of this system is the integration of the Research Brain ontology extraction pipeline (formerly Centaur Brain). When you feed the system complex PDFs, research papers, or deep web articles with explicit tags (like `/research`):
   *   It extracts an abstract and summaries.
   *   It identifies core concepts, theories, and methodologies.
   *   It dynamically links these concepts to existing files or manifests new ones.
   *   It creates a web of bidirectional links that maps your library into a cognitive graph.

3. **Privacy-First & Zero cost:**
   By leveraging a local GTX 1070 GPU for audio transcription (`faster-whisper`), using single-file SQLite databases for vector indexing (`sqlite-vss`), and utilising Gemini's high-performance free-tier API models, the system runs with **zero subscription fees** and high local privacy.

4. **Universal Ingestion (Write-Only Inbox):**
   The folder `01_Inbox/` acts as a transit queue. Once files or links are dropped into it (including via local Samba network share), they are classified, routed, and deleted. This prevents the inbox from becoming a cluttered dumping ground.

---

## 📥 Ingestion & Capture Interfaces

The orchestrator monitors multiple low-friction entry points:
*   **Telegram Bot Channels:** Uses two dedicated private Telegram bots: one for Ingest (capturing links, voice notes, files, or quick text) and one for Q&A (allowing conversational prefix-free queries against your knowledge base).
*   **Local Network Inbox Share:** Drop PDFs, PowerPoints, or text documents directly into your mounted Samba folder from your laptop's file manager.
*   **Obsidian Inbox Drops:** Put files directly into `01_Inbox/` or append text blocks to `01_Inbox/Inbox.md`. The daemon sweeps the folder every 15 seconds.
*   **Gmail/IMAP Polling:** Forward articles or emails to a designated inbox. The orchestrator automatically extracts the body and processes it.

---

## 📁 P.A.R.A. Folder Structure

The vault is strictly organized around the P.A.R.A. method to separate work, responsibilities, and long-term research:
*   **`00_System/`**: Self-describing system prompts, changelogs, indexes, and custom rules.
*   **`01_Inbox/`**: Server-side ingestion directory (exposed via Samba).
*   **`02_Projects/`**: Active work with clear outcomes and deadlines (e.g. [[Hermes]], [[Second Brain]]).
*   **`03_Areas/`**: Long-term responsibilities (e.g. [[Family]], [[Coding]], [[Cookery]], [[Health]], [[Server_Administration]], [[Work]]).
*   **`04_Resources/`**: Holds resources divided into two categories:
    *   **`Research_Brain/`**: The highly specialized research graph containing `Summaries/`, `Sources/`, `Concepts/`, and `Atlas/`. Ingestion here is strictly controlled and only triggered via explicit triggers (like `/research`).
    *   **`Reference_Notes/`**: General reference notes, bookmarks, manuals, and templates that do not require deep research ontology extraction.
*   **`05_Archive/`**: Inactive items from the other folders (which remain fully indexed for RAG vector search).

---

## 🗺️ Product Roadmap

*   **Phase 1-7 (Completed):** Core ingestion, refactoring, RAG search, local GPU transcription, service daemonization, and atomic write resilience.
*   **Phase 8 (Planned):** Bidirectional Jira integration (linking your personal project notes to `stevenmeers.atlassian.net` to automatically sync tasks and transitions).
*   **Phase 9 (Future):** Proactive cognitive synthesis (daily/weekly briefings that suggest connections between distant notes or alert you to outstanding tasks).
