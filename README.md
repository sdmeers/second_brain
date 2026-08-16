# 🧠 Centaur Brain (Second Brain Orchestrator)

Centaur Brain is an agentic **Second Brain** personal productivity and knowledge system designed to bridge the gap between high-velocity information capture (voice notes, web articles, PDFs) and structured, actionable knowledge in **Obsidian**.

It unifies David Allen's **Getting Things Done (GTD)** actionable workflows with Tiago Forte's **P.A.R.A** organizational system, running 24/7 on Fedora Linux.

---

## 🏗️ Architecture Overview

1. **Unified Telegram Bot:** Single conversational interface for:
   - **Capture:** Voice notes transcribed locally via CUDA Faster-Whisper; text capture via `/in`.
   - **Triage:** `/clarify` with an interactive 8-option GTD decision tree and visual inline calendar date picker.
   - **Execution:** `/next @context`, `/waiting`, `/project <name>`, `/done <item>`, `/tickle <item>`.
   - **Review:** `/daily` action-oriented review, `/reflect` weekly review, and `/holiday` review pause.
   - **Knowledge Q&A:** Prefix-free natural language queries with 5-turn conversational memory.
2. **Project Roadmap Auto-Promotion:** When completing a task via Telegram `/done` or ticking `- [x]` in Obsidian, the system automatically suggests the next step from `## Project Steps` to promote to `Next_Actions.md`.
3. **Deep Research Knowledge Graph:** `/research <URL>` extracts atomic concepts bidirectionally linked in `04_Resources/Research_Brain/Concepts/`.
4. **Syncthing Auto-Healer:** Detects sync conflicts on task files across mobile/desktop, performs line-level merges, and commits changes to Git.

---

## 📂 Vault Hierarchy (GTD + P.A.R.A)

```text
vault/
├── 00_System/
│   ├── Inbox/                     <-- Raw captures and dropped files
│   ├── Daily_Digest.md            <-- Latest review briefing
│   └── Librarian_Activity.md      <-- Filing and ingestion log
├── 01_Tasks/                      <-- GTD Task Lists
│   ├── Next_Actions.md            <-- Context-tagged immediate actions (@home, @computer, etc.)
│   ├── Waiting_For.md             <-- Delegated actions (#waiting(Name))
│   ├── Someday_Maybe.md           <-- Future ideas and backlog
│   ├── Captured.md                <-- Raw inbox captures awaiting /clarify
│   └── Completed.md               <-- Archived completed actions
├── 02_Projects/                   <-- Active Projects (Master notes with ## Project Steps)
├── 03_Areas/                      <-- Ongoing Responsibilities (Family, Health, Coding)
├── 04_Resources/                  <-- Research Brain & Reference Material
│   └── Research_Brain/
│       ├── Concepts/              <-- Atomic concept pages
│       ├── Sources/               <-- Raw source files & transcripts
│       └── Summaries/             <-- Structured AI summary nodes
└── 05_Archive/                    <-- Completed Projects & Inactive Reference
```

---

## 🛠️ Service Management (systemd)

The backend runs as a systemd user service:

```bash
# Status
systemctl --user status second-brain.service

# Restart
systemctl --user restart second-brain.service

# Logs
journalctl --user -u second-brain.service -f
```

---

## 📚 Complete Documentation

Detailed guides are located in `docs/` and within the vault at `02_Projects/Personal/Second Brain/docs/`:
- **[User Guide & Workflows](docs/user_guide.md):** Complete manual for commands, triage, reviews, and contexts.
- **[Technical Implementation](docs/technical_implementation.md):** Backend schemas, state machines, and module architecture.
- **[Product Description](docs/product_description.md):** Vision, value proposition, and tech stack.
