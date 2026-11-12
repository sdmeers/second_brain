# 🎯 Second Brain: Product Description

The **Second Brain** is an agentic personal productivity and knowledge operating system that unifies David Allen's **Getting Things Done (GTD)** methodology with Tiago Forte's **P.A.R.A** organizational architecture. It acts as an active, intelligent personal librarian operating over a self-hosted, private Markdown vault.

Unlike rigid, proprietary productivity apps, the Second Brain guarantees **100% data ownership**. Your projects, actions, and knowledge graphs reside as standard Markdown and SQLite files on your personal server, while the AI backend runs continuously to automate capture, triage, project roadmap progression, and cross-source research synthesis.

---

## 💎 Value Proposition

*   **Zero-Friction Ingestion:** Dictate voice notes while walking or driving, in Telegram or the GTD web app; they are transcribed straight into your GTD inbox.
*   **Works the Way You're Holding the Phone:** Telegram for conversation and commands; an installable web app for reviewing, reordering and ticking off actions by touch, including offline. Both edit the same Markdown files.
*   **Intelligent Clarification:** Eliminate decision fatigue with 1-tap AI suggestions, interactive Telegram decision trees, and visual calendar pickers.
*   **Proactive Project Momentum:** Never let project roadmaps stall. Completing an action in Telegram or Obsidian automatically prompts you to promote the next roadmap step into your active list.
*   **Conversational Knowledge Access:** Retrieve answers, follow-up context, and project status using natural language over a 5-turn conversational memory window.
*   **Zero Subscription Lock-In:** Local SQLite and vector search, local Whisper for dropped audio files, and one Google Gemini key for classification, summarising and voice-note transcription, operating at near-zero running cost.

---

## 🚀 Key Capabilities

1.  **GTD Clarification & Triage Engine:** State-machine-driven triage in Telegram converting raw inputs into Next Actions, Delegated Items, Someday/Maybe ideas, or Project roadmaps.
2.  **Visual Calendar Date Picker:** Tap-to-select dates for task deferrals, tickler reminders, and holiday pause management.
3.  **Dynamic Project Dashboards:** Standardized Project Master Notes with Dataview query blocks and automated roadmap step promotion upon task completion.
4.  **Action-Oriented Daily Reviews:** Morning and on-demand (`/daily`) reviews listing upcoming deadlines (<= 14 days), surfaced ticklers, and active tasks grouped by `@context`.
5.  **Strategic Weekly Reviews:** Guided weekly reviews (`/reflect`) auditing orphaned projects, stale waitings, Someday/Maybe ideas, and recent research concepts.
6.  **Deep Research Knowledge Graph:** Ingests articles, PDFs, and presentations (`/research`), generating dense summaries and linking atomic concept pages bidirectionally.
7.  **Syncthing Auto-Healer:** Transparent multi-device synchronization with automated conflict detection, union merging of task lists (nothing open is ever dropped), backups, and Git tracking.
8.  **GTD Web App:** An installable app served by the backend: capture by typing or voice, clarify captured items on one sheet (or step through the whole inbox), drag next actions into priority order, star the few to do next, and manage projects and their roadmaps, with a flag on any project that has no next action. Changes made offline sync when the device reconnects.

---

## 🛠️ Technology Stack

*   **Core Backend:** Python 3.11+ (FastAPI, Asyncio, systemd user services).
*   **Database & Search:** SQLite 3 (WAL mode) with `sqlite-vss` vector indexing (3072-dimensional `gemini-embedding-2` vectors).
*   **Audio Transcription:** Google Gemini for voice notes (Telegram and the web app); `faster-whisper` (CUDA on an NVIDIA GTX 1070, CPU fallback) for audio files dropped into the inbox.
*   **LLM & Intelligence:** Google Gemini (Gemini 3.1 Flash Lite) via `google-genai` SDK with Context Caching, plus decoupled model provider support (Gemini, OpenAI, Ollama).
*   **User Interface:** Telegram Bot API (`httpx` async polling) for voice notes, commands, and interactive inline keyboards; a React 18 + TypeScript + Vite + Tailwind progressive web app served at `/app`, reached over Tailscale.
*   **Storage & Sync:** Obsidian Markdown Vault synchronized seamlessly across Linux, macOS, and mobile via rootless Syncthing.
