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
- `index_brain.py` / `reindex_covers.py` — rebuild/backfill the vector index or book cover cache.
- Server-only (gitignored, not in the repo): `reindex_all.py` (re-index via the running API) and `rebuild_vault.py` / `resume_rebuild.py` (bulk regenerate brain nodes, resumable).
- `brain_cleaner.py` — the maintenance/review engine (daily & weekly review generation, orphan/stale audits).
- `add_book.py` — CLI to generate a book summary node directly into the vault ("Book Mode").
- `create_jira_epics.py` — Jira export helper.

Testing and linting (both configured in `backend/pyproject.toml`):

```bash
cd backend
uv run pytest              # ~695 tests, ~5s, no vault/API key/network needed
uv run ruff check .        # must pass clean
uv run ruff check . --fix  # apply safe autofixes
```

`backend/tests/conftest.py` builds a throwaway vault and sets `OBSIDIAN_VAULT_PATH` before
any backend module is imported, so tests never touch the real vault. Ruff's `F` rules are
the important ones here — undefined names had previously broken `/tickle`, `/holiday` and
the PDF content-type probe silently. There is no type-checker configured.

Systemd unit templates live in `docs/systemd/`: `second-brain.service` (the daemon), plus `second-brain-cleaner.{service,timer}` (daily digest, Mon–Fri 07:30 / Sat–Sun 10:00) and `second-brain-weekly-review.{service,timer}` (Fri 14:00). They deploy to `~/.config/systemd/user/`. The timers run `backend/.venv/bin/python3` directly, so `uv sync` after dependency changes.

**Important:** in normal operation, Second Brain runs as this background systemd user service — not as a foreground process started ad hoc. Any code change to `backend/` will NOT take effect until the service is restarted:

```bash
systemctl --user restart second-brain.service
systemctl --user status second-brain.service   # verify it came back up
journalctl --user -u second-brain.service -f   # tail logs
```

After editing backend code, restart the service (and check its status/logs) before telling the user a change is live or asking them to test it.

The GTD web app (`webapp/`) is React 18 + TypeScript + Vite + Tailwind. `npm run build` (type-checks, then builds to `webapp/dist`, which the backend serves at `/app`) and `npm run lint`. Node is installed on the home server (since 16 Nov 2026): build in the worktree's `webapp/` (`npm ci` once, then `npm run build` and `npm run lint`) and `rsync -a --delete` its `dist/` into the live checkout's `webapp/dist/` (gitignored; static files need no restart; backend changes do). Its backend is `backend/api/actions.py` (HTTP), `backend/vault/actions.py` (action lists), `backend/vault/clarify_ops.py` (filing captured items), `backend/vault/projects.py` (projects and roadmaps) and `backend/processing/voice_capture.py` (voice notes). The last three are shared with the Telegram handlers: change behaviour there, not in a handler. The app identifies a task by its exact line text, a project by its name and a roadmap step by its text, so never reformat lines on read without going through the same normalisation the sweeper applies. `webapp/src/optimistic.ts` mirrors the backend's effects for offline use (including the exact captured-line format); keep the two in step.

The Chrome extension (`extension/`) is a plain Manifest V3 extension (no build step) using vendored `Readability.js`/`turndown.js` in `extension/lib/`. `extension/package.json`'s `test` script is a stub.

## Architecture

### Ingestion → classification → filing pipeline

All input channels (Telegram bot, Chrome extension POSTs to `/process`, Gmail IMAP poller, filesystem drops into `00_System/Inbox/`) funnel through a **SQLite-backed queue** (`backend/ingestion/queue.py`) that a background **watcher loop** (`backend/ingestion/watcher.py`, started by FastAPI's `lifespan` context in `backend/api/routes.py`) polls continuously.

The watcher/`classifier.py` routes each item to one of three destinations:
- **GTD Engine** — interactive triage via the `/clarify` state machine (`backend/ingestion/handlers/clarify.py`) into `01_Tasks/*.md` and project roadmaps.
- **AI Librarian** — direct semantic filing into P.A.R.A. reference folders under `04_Resources/` (`backend/utils/librarian.py`).
- **Deep Research** — full extraction + concept-graph pipeline (`backend/processing/capture.py`, `brain_node.py`, `concept_updater.py`) into `04_Resources/Research_Brain/`.

Slash commands (`/research`, `/file`, etc.) deterministically bypass classifier heuristics and route straight to their handler — see `backend/ingestion/handlers/` (`voice.py`, `project.py`, `clarify.py`, `engage.py`, `instruction.py`, `task.py`, `undo.py`) and `backend/ingestion/telegram.py` for the Telegram command dispatch table (full reference in the README's "Telegram Command Reference" table).

### The vault is the schema

There's no ORM/migrations — the vault's structure *is* the data model, and multiple modules independently parse/write Markdown against shared conventions:
- **Task lines** in `01_Tasks/*.md` follow a fixed inline format (`- [ ] <desc> @<context> #project/<name> #<category> 📅 <due> 🔔 <tickler> (Source: [[...]])`) — see `docs/technical_implementation.md` §2 for the full field table before parsing or writing task lines.
- **Project master notes** live one-per-directory under `02_Projects/{Personal,Professional}/<Project_Name>/<Project_Name>.md`, with YAML frontmatter (`due_date`, `status`, `category`) plus `## Project Steps` (future roadmap backlog) and `## Active Actions` (a live Dataview query) sections. Completing a project-tagged action (via Telegram `/done` or ticking `- [x]` in Obsidian) triggers **step auto-promotion**: `engage.py` finds the project note, offers unchecked `## Project Steps` items as candidates, and on selection appends them to `Next_Actions.md` while removing them from the roadmap.
- Archiving a project moves its whole directory to `05_Archive/02_Projects/<Category>/` (recent convention — see git history).

**`backend/vault/tasks.py` is the single source of truth for parsing and writing task
lines and project notes.** When adding features that touch tasks or projects, use its
helpers rather than writing another Markdown parser:

- `parse_task_line()` / `render_task_line()` / `strip_task_metadata()` — task lines.
  Never use `.strip("- [ ]")`: `str.strip` takes a *character set*, so it corrupts any
  task whose text starts or ends with `[`, `]`, `-` or a space.
- `archive_completed_lines()` — the only way to write `z_Completed.md`, so `/status` and
  `/reflect` can parse what every path produces.
- `find_project_note()` / `read_project()` / `ensure_project_note()` /
  `append_project_step()` / `remove_step_from_project()` — project notes.
- `parse_frontmatter()` / `dump_frontmatter()` — **always** use these instead of
  f-string interpolation; a title containing a quote or colon otherwise emits invalid YAML.

Conventions drifting between independent copies of these parsers is what previously
caused several silent failures (`Completed.md` vs `z_Completed.md`, `00_System/Tasks.md`
vs `01_Tasks/Next_Actions.md`).

### Context feeds (`backend/feeds/`)

Automatic feeds (voice journal, calendar, AI chat summaries, email digest, household documents) built from `docs/implementation_plan/ingestion/INTENT.md`. Each is a module with a `python3 -m backend.feeds.<name>` entry point run by its own systemd timer (`docs/systemd/second-brain-{journal,calendar,chats,email}.*`) and an `ENABLE_*_FEED` switch that defaults to off. Rules that hold for every feed:

- **Model calls go through `feeds.llm.generate()` only.** It masks identifiers, enforces the monthly cap (`feeds/ledger.py`, `FEEDS_MONTHLY_BUDGET_PENCE`), passes no tools, and records token counts. Never call `call_gemini_with_retry` or `llm_client` directly from a feed, and never give the summarising call tools: ingested text is untrusted.
- **Notes go through `feeds.notes.write_feed_note()` only.** It writes the required frontmatter, is idempotent on `(feed, source_id, hash)`, and charges the indexing estimate. A feed that appends to an existing note uses `notes.existing_body()` so hand-written text survives.
- **Every entry point takes `dry_run`** and then writes nothing and spends nothing.
- **State lives in `backend/state/feeds.db`** (`feeds/state.py`), outside the vault. It holds IDs, hashes, paths and counts. Never put message text, transcripts, senders, subjects or identifiers in it, in the ledger, or in a log line; use `feeds.masking.redact()` for anything logged.
- **Read-only at the source.** The calendar and email feeds only issue GETs; `feeds/google_auth.py` refuses any Google scope other than `gmail.readonly`. Do not add a write scope or a second OAuth client without asking.
- **Feed cores are transport-independent.** `feeds/journal.record_entry()` takes audio or text plus `via` and `entry_id`; Telegram (`feeds/journal_telegram.py`) and the web app's Journal page (`api/journal.py`, mounted under `/api/actions/journal` so it inherits the token check) are thin adapters. Each entry records its recording as `<!-- audio: name -->`; `journal.read_day()` parses notes back for the app. From the evening prompt until 04:00, a plain Telegram voice note gets Journal / Capture buttons. `JOURNAL_AUDIO_KEEP_MONTHS` (0 = forever) prunes old recordings when the evening prompt runs; transcripts are never deleted.
- **Secrets:** `GCAL_ICAL_URL`, `backend/state/google_client.json` and `google_token.json` must never be logged or committed. httpx exceptions contain the URL, so catch them and report the type only.

- **Chat exports differ by provider.** Claude's export has conversation objects (`conversations.json`). Gemini's Google Takeout is a flat activity log with one entry per prompt and no titles: `parse_gemini_export` groups entries by the ID in each entry's `gemini.google.com/app/<id>` link. Parsers are strict and refuse an unexpected shape rather than guess. A first import for a provider always asks for approval in Telegram.

- **Filing round-up:** the morning review (`brain_cleaner.generate_daily_digest`) uses `utils/filing_roundup.py` to report everything filed since the last scheduled review (librarian log plus email, household and chat feed notes): counts and folders in Telegram, links in the Daily Digest note. Feeds do not send their own "filed" messages.

Raw material (journal audio, export zips) goes to `00_System/Inbox/Raw/<feed>/`, which Syncthing and the RAG index both skip. `feeds/household_scan.py` must stay standard-library only: it runs on the laptop without the venv.

### Access control and secrets

- **The HTTP API requires a token.** `API_AUTH_TOKEN` is set in the live `backend/.env`; `/process`, `/query` and `/api/actions/*` return 401 without `Authorization: Bearer <token>`. `/health` and the static `/app` shell stay open by design. Any new route that returns or changes vault data must take `Depends(require_api_token)`. The web app stores the token in `localStorage`; the extension reads it from the gitignored `extension/config.js`.
- **The `+brain` Gmail poller has a sender allow-list** (`GMAIL_ALLOWED_SENDERS`, checked by `ingestion/gmail.sender_allowed()`). Unlisted senders and messages Gmail marked `dmarc=fail` are marked read, reported in Telegram and not ingested.
- **Never pass an httpx error message to the user or a log.** It contains the request URL, which for Telegram holds the bot token and for the calendar is the secret address. Report the exception type or status code only (see `download_telegram_file`).
- **Never load `backend/.env` with the shell's `source`/`.`**: values with spaces get executed and echoed. Use `dotenv.load_dotenv()` in Python.
- **The GitHub repo is public.** Nothing personal, no addresses and no credentials in commits; `tests/test_no_secrets.py` guards the obvious cases. Check dependencies with `uvx pip-audit` after changing `uv.lock`.

### Config and paths

`backend/config.py` is the single source of truth for all vault sub-paths (derived from `OBSIDIAN_VAULT_PATH`) and provider settings (`LLM_PROVIDER`/`EMBEDDING_PROVIDER`: `gemini` | `openai` | `ollama`, set via `backend/.env`). Importing it does not write to disk (`ensure_vault_layout()` creates the vault directories and is called explicitly at startup), but it will raise at import time if `OBSIDIAN_VAULT_PATH` (and the active provider's API key) isn't set — so any script importing `backend.config` requires a valid `.env` to even load.

### RAG / Q&A

Plain-text Telegram messages (no command prefix) are answered via `backend/rag/chat.py`, which queries a `sqlite-vss` vector index (`backend/rag/vectorstore.py`, embeddings via `backend/rag/embeddings.py`) over indexed vault content, with a 5-turn conversational memory window. The same query path is exposed over HTTP at `POST /query` in `backend/api/routes.py`.

### Sync conflict healing

The watcher also detects multi-device Syncthing conflict files (`*.sync-conflict-*.md` and device-suffixed variants). Conflicts on the task lists are union-merged by `backend/vault/task_merge.py` after backing up both copies and committing them via `backend/vault/task_history.py`; never make the healer trust one copy over the other, and never delete a conflict copy before the merged file is written.

### Prompts

All LLM prompts live in `backend/prompts/*.md` and are loaded with `backend.prompts.load_prompt(name, **vars)` (`${var}` placeholders, `string.Template`). Don't add inline prompt strings in Python; add a file there instead. `tests/test_prompts.py` fails if a call site and its template disagree on variables. Prompt edits need a service restart like any other backend change.

### Multi-provider LLM abstraction

`backend/utils/llm_client.py` and `backend/utils/gemini.py` abstract over the three supported providers; Gemini-specific code paths use context caching (`get_or_create_context_cache`) to keep extraction prompts cheap across repeated calls (book mode, research ingestion, brain-node generation).
