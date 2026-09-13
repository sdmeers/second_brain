# 🧠 Second Brain Orchestrator (GTD + P.A.R.A Fusion)

The **Second Brain Orchestrator** is an "Agent-as-Librarian" personal knowledge operating system running 24/7 on Fedora Linux. It bridges the gap between passive note-taking (Obsidian Markdown files) and active automation (Gemini AI parsing, local GPU Whisper audio transcription, SQLite-backed task queues, and vector Q&A).

With the unified **GTD + P.A.R.A** architecture, it integrates David Allen's Getting Things Done (GTD) actionable workflows with Tiago Forte's P.A.R.A method for clean knowledge organisation. All data is stored in standard Markdown and synced across devices using Syncthing.

---

## 📂 Vault Folder Hierarchy

```text
vault/
├── 00_System/
│   ├── Inbox/                     <-- CAPTURE STAGING (raw items & drops)
│   │   └── Archive/               <-- Clarified items kept for traceability
│   ├── Internal/
│   │   └── Digests/               <-- Rolling archive of daily digests (14-day)
│   ├── Daily_Digest.md            <-- Latest daily review briefing
│   ├── _INDEX.md                  <-- Auto-generated vault sitemap
│   └── Librarian_Activity.md      <-- Ingestion and filing log
├── 01_Tasks/                      <-- GTD ACTION HUB
│   ├── Next_Actions.md            <-- ⚡ Context-tagged actions (@home, @computer, etc.)
│   ├── Waiting_For.md             <-- ⏳ Delegated items (#waiting(Name))
│   ├── Someday_Maybe.md           <-- 💡 Future ideas & uncommitted desires
│   ├── Captured.md                <-- 📥 Raw captures awaiting /clarify
│   └── z_Completed.md             <-- ✅ Archived completed items (newest first)
├── 02_Projects/                   <-- ACTIVE PROJECTS (Master Notes & Roadmaps)
│   ├── Personal/                  <-- Personal deliverables
│   └── Professional/              <-- Work & career deliverables
├── 03_Areas/                      <-- ONGOING RESPONSIBILITIES (Family, Health, Coding)
├── 04_Resources/                  <-- REFERENCE & RESEARCH BRAIN
│   └── Research_Brain/
│       ├── Concepts/              <-- Atomic, bidirectional knowledge graph pages
│       ├── Sources/               <-- Raw ingested source transcripts & docs
│       └── Summaries/             <-- Structured AI knowledge summaries
└── 05_Archive/                    <-- COMPLETED DELIVERABLES & HISTORICAL RECORDS
```

---

## ⚙️ Core System Architecture

1. **Unified Telegram Bot:** Central conversational interface for Capture (voice/text), Q&A (prefix-free questions), Status (`/status` / `/sitrep`), GTD Commands (`/clarify`, `/next`, `/waiting`, `/project`, `/done`, `/tickle`), and on-demand reviews (`/daily`, `/reflect`, `/holiday`).
2. **Interactive Clarify State Machine:** Triage captured thoughts into Next Actions, Roadmaps, Waiting items, or Reference materials with 1-tap buttons and a visual inline calendar picker.
3. **Project Roadmap Auto-Promotion:** Automatically prompts to promote the next step from `## Project Steps` whenever a project action is marked done in Telegram or ticked in Obsidian.
4. **Action-Oriented Daily Review:** Morning briefing and `/daily` command surfacing upcoming deadlines (<= 14 days), today's ticklers, and active tasks grouped by context.
5. **Deep Research Engine:** Ingests articles and documents via `/research`, building interconnected concept nodes in `04_Resources/Research_Brain/Concepts/`.
6. **Auto-Healer Daemon:** Transparently detects and resolves Syncthing sync conflicts on task files with automated Git commits.

---

## 🛠️ Service Management & Commands

```bash
# Check service status
systemctl --user status second-brain.service

# Restart service after code changes  (REQUIRED — edits do not take effect until you do)
systemctl --user restart second-brain.service

# Inspect live application logs
journalctl --user -u second-brain.service -f
```

## 🧪 Development

```bash
cd backend
uv sync                    # install/update dependencies
uv run pytest              # ~320 tests, ~2s — no vault, API key or network needed
uv run ruff check .        # lint; must pass clean
uv run ruff check . --fix  # apply safe autofixes
```

Tests build a throwaway vault in `conftest.py`, so they never touch your real one.

> **When adding features that touch tasks or projects**, use the helpers in
> `backend/vault/tasks.py` rather than writing another Markdown parser — see
> [technical_implementation.md](technical_implementation.md) §6.

## 📚 Documentation Index

| Document | Contents |
| :--- | :--- |
| [user_guide.md](user_guide.md) | Day-to-day workflows and the full Telegram command reference |
| [technical_implementation.md](technical_implementation.md) | Architecture, schemas, state machines, module breakdown |
| [manual_testing.md](manual_testing.md) | Manual verification plan for behaviour the test suite cannot cover |
| [changelog.md](changelog.md) | What changed and why, most recent first |
| [product_description.md](product_description.md) | What the system is, in brief |
