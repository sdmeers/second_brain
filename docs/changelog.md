# 📋 Changelog

## GTD App: Capture, Clarify, Voice and Projects — November 2026 (`feat/app-clarify-projects`)

The web app can now do what previously needed Telegram: capture (typed or spoken),
clarify captured items on a single sheet, and manage projects and their roadmaps. See
README "GTD Web App" and technical_implementation.md §10.

The write side of `/clarify`, voice-note storage, `/archive` and step promotion moved out
of the Telegram handlers into shared modules (`vault/clarify_ops.py`,
`processing/voice_capture.py`, `vault/projects.py`) that both front ends call.

**Behaviour changes in Telegram:**

- `/archive` no longer deletes an existing archive folder of the same name to make room.
  It refuses and tells you. It also edits the note's frontmatter through the YAML
  helpers instead of a regex over `status:`.
- Clarifying an item removes exactly its line from `Captured.md`. It used to
  `str.replace` the text across the whole file, leaving a blank line each time and
  cutting the same words out of any longer item that contained them.
- Task lines written by `/clarify` now put tags in the canonical order
  (`@context #project/… #category`); nothing that reads them depends on the order.

**Also fixed:** the inbox scanner only excluded `_voice.ogg` and `_voice.mp3` from
ingestion; any other voice recording left in the inbox would have been ingested as a
dropped file. It now skips all `*_voice.*`.

## Actions Web App — November 2026 (`feat/actions-api`)

An installable web app for working with Captured, Next, Waiting For and Someday/Maybe
from a laptop or phone, served by the backend at `/app`. See README "Actions Web App"
and technical_implementation.md §10.

**Behaviour change:** the task files are no longer re-sorted by due date every 15
seconds. The order of the lines is now your priority order, set by dragging in the app
(or by moving lines in Obsidian), and `/next` in Telegram lists them in that order.
Due and overdue dates are shown as badges in the app instead.

Also fixed along the way: the dashboard sweeper awaited a Telegram call between reading
the task files and rewriting them, so a change written in that gap could be overwritten.
It now writes first and prompts afterwards.

## Code Review Remediation — August 2026 (`refactor/code-review-fixes`)

A full review of the ~10k-line backend. No functionality was removed; four features that
had silently stopped working were restored, and the code was consolidated around a single
shared parser with a test suite to hold it in place.

### Features that were completely broken

| Feature | What happened | Cause |
| :--- | :--- | :--- |
| `/tickle` | Nothing at all — no reply | `TICKLE_SESSIONS` was used but never defined; `NameError` on the first line |
| `/holiday <scope> <date>` | Failed on any typed date | `timedelta` was never imported in `clarify.py`, so every relative-date branch raised — including the fallback |
| `/reflect` step 5 | Always reported "0 actions completed" | Read `01_Tasks/Completed.md`; the archive is `z_Completed.md` |
| Instruction agent task commands | Silently did nothing | Pointed at `00_System/Tasks.md`, a path from a pre-`01_Tasks` schema |

### Other corrected bugs

- **PDF links without a `.pdf` suffix** skipped deep extraction. The content-type probe
  that decides this raised `NameError` (`httpx` was never imported in `watcher.py`) inside
  a bare `except`, so it never ran.
- **Sync-conflict auto-healing for task files never fired** — the branch tested for
  `tasks.md` while the file is `Next_Actions.md`.
- **Conflict detection was too broad**: the `-XXXXXXX.md` device-suffix pattern also
  matched ordinary notes like `Notes-ABC1234.md`, which the healer would then move or
  delete. It now requires the shadowed original to exist.
- **RAG answer quality** — the entire retrieved context, chat history and wikilink prompt
  were sent to the model as one unbroken line, because `\n` in several f-strings emitted a
  literal backslash-n rather than a newline.
- **Blank voice notes** were reported as processing failures instead of being skipped
  quietly (wrong keyword argument), and leaked a SQLite connection each time.
- **Task text starting or ending with `[`, `]`, `-` or a space was corrupted** —
  `[Draft] review notes` became `Draft] review notes`. `str.strip` takes a character set,
  not a prefix, and the `.strip("- [ ]")` idiom appeared in eight places.
- **Project names containing a quote or colon produced invalid YAML** frontmatter, which
  Obsidian's properties panel and the librarian's own parser both reject.
- **A clean `uv sync` produced an install that crashed on DOCX/PPTX** — `python-docx` and
  `python-pptx` were imported but never declared.
- **Queue timestamps disagreed by an hour under BST** — inserts wrote UTC, status updates
  wrote local time.

### Responsiveness

- Web scraping, PDF/DOCX download and cover fetching used **synchronous** HTTP with 30-second
  timeouts inside async code, freezing the Telegram bot for the duration of any slow site
  during a capture. All are now async.
- `git commit` (which can include a 30-second `git push`) moved off the event loop.
- The vector indexer read and hashed **every** file in the vault every two minutes. It now
  skips unchanged files on `mtime`+`size`: **0.49 s → 0.04 s** on a 1,696-file / 253 MB
  vault, and it now scales with changed files rather than vault size.
- The queue drains up to 5 items per tick instead of exactly one — a backlog of 20 captures
  took five minutes to clear.
- Removed `poll_telegram_unified`, dead code that was the superseded design polling inside
  the 15-second watcher tick (the original cause of 0–15 s button latency).

### Structure

- **`backend/vault/tasks.py` is now the single parser** for task lines and project notes,
  replacing ad-hoc regexes duplicated across five modules. Convention drift between those
  copies caused several of the bugs above.
- `process_queue_worker` (580 lines) split into `prepare_content`, `classify_item`,
  `route_para_note` and `finalise_item`.
- Two near-identical 45-line PARA note writers collapsed into one; likewise the reflect-hub
  screen, which was duplicated verbatim between `/reflect` and its Hub button.
- Session state moved to an expiring `SessionStore`, so abandoned flows no longer leak and
  the "session expired" paths are actually reachable.
- Deleted `backend/logger.py`, a near-duplicate of `backend/utils/logger.py`.
- Repaired `rebuild_vault.py` and `resume_rebuild.py`, which imported names from `main.py`
  that had moved into `backend/` and had been broken since.

### Robustness

- `atomic_write` now uses a unique dot-prefixed temp file, `fsync`s before renaming, and
  keeps the temp file in the same directory — so concurrent writes cannot clobber each
  other, a crash cannot leave an empty file, and Syncthing ignores it mid-write.
- `sanitize_filename` could return an empty string (writing to the directory itself); it
  now collapses to a single path segment and falls back to `Untitled`.
- The LLM-chosen PARA destination is validated against an allowlist and confined to the
  vault before being used to create directories.
- Failed queue items now back off 1 / 5 / 30 minutes instead of retrying on the next tick
  (which produced three failures and three Telegram alerts inside a minute).
- Optional `API_AUTH_TOKEN` bearer auth on `/process` and `/query`. Unset by default, so
  behaviour is unchanged; `/health` stays open for monitoring.
- The Gemini client is constructed lazily — importing the classifier under
  `LLM_PROVIDER=openai` previously still demanded a Gemini key.

### Tooling

- **218 tests** (`uv run pytest`), ~1.5 s, requiring no vault, API key or network.
- **ruff** configured and passing clean. Its `F` rules catch exactly the undefined-name
  class of bug that broke `/tickle` and `/holiday`; `B005` catches the `.strip()` corruption.
- Extension network configuration moved to a gitignored `extension/config.js`, keeping
  personal Tailscale/LAN addresses out of the repository.

### Documentation corrections

`Completed.md` → `z_Completed.md` in four places; the `/archive` destination is
`05_Archive/02_Projects/<Category>/`; the embedding dimension is 3072, not 768; and the
systemd unit in the docs now matches the one actually deployed.
