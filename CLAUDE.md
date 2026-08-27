# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Second Brain** ("Centaur Brain") is a personal GTD + P.A.R.A. knowledge/task system for Obsidian. A Python/FastAPI backend running as a local daemon ingests captures from Telegram, a Chrome extension, Gmail, and a filesystem inbox; an LLM (Gemini by default, OpenAI/Ollama also supported) classifies, summarizes, and files everything into a plain-Markdown Obsidian vault. There is no separate database of record — the vault (Markdown files under `OBSIDIAN_VAULT_PATH`) *is* the data, plus a local SQLite ingestion queue and a `sqlite-vss` vector index for RAG.

Read `README.md` for the full feature/command tour and `docs/technical_implementation.md` for schemas, state machines, and module-by-module architecture — both are kept up to date and are more detailed than what's summarized below.

## Commands

All backend commands run from `backend/` using `uv` (dependency manager, `pyproject.toml` + `uv.lock`, Python 3.11+).

```bash
cd backend
uv sync                          # install/update dependencies
cp .env.example .env             # then fill in OBSIDIAN_VAULT_PATH, GEMINI_API_KEY, etc.
uv run python3 -m backend.main   # run the FastAPI server + background watcher (run from repo root, or `python3 -m backend.main` after cd backend, see note below)
```

Note: `backend/main.py` inserts the repo root onto `sys.path` and imports via the `backend.` package prefix, so it must be run as a module (`python3 -m backend.main`) rather than executed directly, and generally from the repository root so `backend` resolves as a package.

Other operational scripts (all live in `backend/`, run the same way with `uv run python3 <script>.py`):
- `index_brain.py` / `reindex_all.py` / `reindex_covers.py` — rebuild/backfill the vector index or book cover cache.
- `rebuild_vault.py` / `resume_rebuild.py` — bulk regenerate brain nodes across the vault (resumable).
- `brain_cleaner.py` — the maintenance/review engine (daily & weekly review generation, orphan/stale audits).
- `add_book.py` — CLI to generate a book summary node directly into the vault ("Book Mode").
- `create_jira_epics.py` — Jira export helper.

There is no test suite, linter, or type-checker configured in this repo (no `pytest`/`ruff`/`mypy` config, no `tests/` directory) — do not assume one exists.

The systemd unit for persistent operation is `docs/centaur-brain.service`; deploy path is `~/.config/systemd/user/second-brain.service`.

**Important:** in normal operation, Second Brain runs as this background systemd user service — not as a foreground process started ad hoc. Any code change to `backend/` will NOT take effect until the service is restarted:

```bash
systemctl --user restart second-brain.service
systemctl --user status second-brain.service   # verify it came back up
journalctl --user -u second-brain.service -f   # tail logs
```

After editing backend code, restart the service (and check its status/logs) before telling the user a change is live or asking them to test it.

The Chrome extension (`extension/`) is a plain Manifest V3 extension (no build step) using vendored `Readability.js`/`turndown.js` in `extension/lib/`. `extension/package.json`'s `test` script is a stub.

## Architecture

### Ingestion → classification → filing pipeline

All input channels (Telegram bot, Chrome extension POSTs to `/process`, Gmail IMAP poller, filesystem drops into `00_System/Inbox/`) funnel through a **SQLite-backed queue** (`backend/ingestion/queue.py`) that a background **watcher loop** (`backend/ingestion/watcher.py`, started in FastAPI's `startup` event in `backend/api/routes.py`) polls continuously.

The watcher/`classifier.py` routes each item to one of three destinations:
- **GTD Engine** — interactive triage via the `/clarify` state machine (`backend/ingestion/handlers/clarify.py`) into `01_Tasks/*.md` and project roadmaps.
- **AI Librarian** — direct semantic filing into P.A.R.A. reference folders under `04_Resources/` (`backend/utils/librarian.py`).
- **Deep Research** — full extraction + concept-graph pipeline (`backend/processing/capture.py`, `brain_node.py`, `concept_updater.py`) into `04_Resources/Research_Brain/`.

Slash commands (`/research`, `/file`, etc.) deterministically bypass classifier heuristics and route straight to their handler — see `backend/ingestion/handlers/` (`voice.py`, `project.py`, `clarify.py`, `engage.py`, `instruction.py`, `task.py`) and `backend/ingestion/telegram.py` for the Telegram command dispatch table (full reference in the README's "Telegram Command Reference" table).

### The vault is the schema

There's no ORM/migrations — the vault's structure *is* the data model, and multiple modules independently parse/write Markdown against shared conventions:
- **Task lines** in `01_Tasks/*.md` follow a fixed inline format (`- [ ] <desc> @<context> #project/<name> #<category> 📅 <due> 🔔 <tickler> (Source: [[...]])`) — see `docs/technical_implementation.md` §2 for the full field table before parsing or writing task lines.
- **Project master notes** live one-per-directory under `02_Projects/{Personal,Professional}/<Project_Name>/<Project_Name>.md`, with YAML frontmatter (`due_date`, `status`, `category`) plus `## Project Steps` (future roadmap backlog) and `## Active Actions` (a live Dataview query) sections. Completing a project-tagged action (via Telegram `/done` or ticking `- [x]` in Obsidian) triggers **step auto-promotion**: `engage.py` finds the project note, offers unchecked `## Project Steps` items as candidates, and on selection appends them to `Next_Actions.md` while removing them from the roadmap.
- Archiving a project moves its whole directory to `05_Archive/02_Projects/<Category>/` (recent convention — see git history).

When adding features that touch tasks or projects, grep for existing parsers (`tasks_dashboard.py`, `engage.py`) rather than writing a new Markdown parser — the line/frontmatter formats are relied on by several independent readers.

### Config and paths

`backend/config.py` is the single source of truth for all vault sub-paths (derived from `OBSIDIAN_VAULT_PATH`) and provider settings (`LLM_PROVIDER`/`EMBEDDING_PROVIDER`: `gemini` | `openai` | `ollama`, set via `backend/.env`). It also creates required vault directories on import and will raise at import time if `OBSIDIAN_VAULT_PATH` (and the active provider's API key) isn't set — so any script importing `backend.config` requires a valid `.env` to even load.

### RAG / Q&A

Plain-text Telegram messages (no command prefix) are answered via `backend/rag/chat.py`, which queries a `sqlite-vss` vector index (`backend/rag/vectorstore.py`, embeddings via `backend/rag/embeddings.py`) over indexed vault content, with a 5-turn conversational memory window. The same query path is exposed over HTTP at `POST /query` in `backend/api/routes.py`.

### Sync conflict healing

The watcher also detects multi-device Syncthing conflict files (`*.sync-conflict-*.md` and device-suffixed variants) and auto-resolves them via line-level merge (preserving `- [x]` completion state), committing the result with `backend/utils/git.py`.

### Multi-provider LLM abstraction

`backend/utils/llm_client.py` and `backend/utils/gemini.py` abstract over the three supported providers; Gemini-specific code paths use context caching (`get_or_create_context_cache`) to keep extraction prompts cheap across repeated calls (book mode, research ingestion, brain-node generation).
