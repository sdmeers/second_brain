# Manual Testing — `refactor/code-review-fixes`

The service is already restarted on this branch, so you can test straight away.
Automated checks (`uv run pytest`, `uv run ruff check .`) are green; this plan covers what
they cannot: real Telegram interaction, real LLM calls, and real vault writes.

**Rollback at any point:** `git checkout main && systemctl --user restart second-brain.service`

Tail the logs in a second terminal while testing:
```bash
journalctl --user -u second-brain.service -f
```

---

## A. Previously broken — these did not work at all before

These four features were dead. They are the most important to verify.

| # | Command | Expected | Was |
|---|---|---|---|
| A1 | `/tickle Buy birthday card` | Calendar picker appears; pick a date; confirms "Deferred until DD-MM-YYYY"; the task in `Next_Actions.md` gains `🔔 YYYY-MM-DD` | Silent `NameError`, nothing happened |
| A2 | `/holiday personal 23rd Aug` | "Holiday Mode Activated", scope `#personal`, correct date | `NameError` on any non-ISO date |
| A3 | `/holiday` → 🏠 Personal → 📅 Open Calendar Picker → pick a date | Same confirmation via the button path | Button path worked, typed dates did not |
| A4 | `/reflect` → **5. 🚨 Deadlines** | "Weekly Accomplishments: **N** action(s) completed" where N > 0 | Always reported 0 |

**A5 — Instruction agent (was writing to a phantom file).** Send as plain text, not a command:
> `add a task to test the instruction handler`

Expect a confirmation, and the task to appear in `01_Tasks/Next_Actions.md` with a
`#personal` or `#professional` tag. Then:
> `mark the task about testing the instruction handler as done`

Expect it to leave `Next_Actions.md` and appear in `01_Tasks/z_Completed.md` as
`- [x] ~~...~~ (Completed on YYYY-MM-DD)`.
Confirm **no** `00_System/Tasks.md` file has been created — that was the phantom target.

---

## B. Core GTD loop — regression check

| # | Step | Expected |
|---|---|---|
| B1 | `/in Test capture from review branch` | "📥 Captured", appears in `01_Tasks/Captured.md` |
| B2 | `/clarify` | Menu appears for the captured item |
| B3 | Choose **⚡ Next Action**, pick a context | Lands in `Next_Actions.md` with `@context` and a category tag |
| B4 | `/next` | Personal / Professional / Both picker; each filters correctly |
| B5 | `/next @home` | Filters by context directly, no picker |
| B6 | `/done Test capture from review branch` | Marked complete, moved to `z_Completed.md` |
| B7 | `/status` | Counts match what is in the files |
| B8 | `/waiting` | Lists delegated items |
| B9 | `/reflect` → walk all 5 steps → **🏠 Reflection Hub** | Hub screen identical whether reached via `/reflect` or the Hub button (these were duplicated code paths; now one) |

**B10 — 2-minute rule.** `/in Quick two minute thing` → `/clarify` → **⚡ 2-Min Rule (Done)**.
Expect it archived to `z_Completed.md` in the same `- [x] ~~text~~ (Completed on ...)` format
as `/done` produces (previously this path wrote a slightly different format).

---

## C. Projects

| # | Step | Expected |
|---|---|---|
| C1 | `/project <existing project>` | Outcome, active actions and roadmap steps all render |
| C2 | `/project <existing> add Test a new roadmap step` | Step appended under `## Project Steps` |
| C3 | Complete a project-tagged action via `/done` | Step-promotion buttons appear; choosing one adds it to `Next_Actions.md` **and** removes it from the project roadmap |
| C4 | `/project Brand New Test Project` | Created at `02_Projects/Personal/Brand New Test Project/Brand New Test Project.md` |
| C5 | Open C4's note in Obsidian | Frontmatter renders as proper YAML in the properties panel — not raw text |

**C6 — YAML escaping (new).** `/project Phase 1: Discovery`
The colon previously produced invalid YAML. Expect the note to open cleanly in Obsidian
with `title: 'Phase 1: Discovery'` shown as a property, and `/project Phase 1: Discovery`
to render its outcome correctly.

**C7 — Bracket handling (new).** Add a next action whose text starts with a bracket, e.g.
`[Draft] review the spec`. Wait ~20 s for the dashboard sweep, then confirm the leading
`[` is still there in `Next_Actions.md`. The old `.strip("- [ ]")` ate it.

---

## D. Ingestion pipeline

| # | Step | Expected |
|---|---|---|
| D1 | Send a plain URL to a news article | Filed under `04_Resources/`, "Processed" notification with category and location |
| D2 | `/research <URL of a paper>` | Deep-research path: lands in `04_Resources/Research_Brain/`, concepts created |
| D3 | `/file <URL>` | Direct PARA filing, skips GTD |
| D4 | Send a voice note | Transcribed, appears in `Captured.md` |
| D5 | Send a photo of some text | OCR'd, filed |
| D6 | Send a PDF | Deep extraction, lands in `Research_Brain/` or `Manuals_and_Equipment/` |
| D7 | Ask a plain question, e.g. "what projects am I working on?" | RAG answers with `[[wikilink]]` citations |

**D8 — PDF URL without a `.pdf` suffix (was silently broken).** Send a link that serves a
PDF without the extension, e.g. an arXiv abstract's PDF link or a datasheet URL.
It should take the **deep extraction** path. The content-type probe that decides this
previously raised `NameError` inside a swallowed `except`, so such links always fell
through to the lightweight path.

**D9 — RAG answer quality (subjective).** Ask a question needing several notes. Answers
should be at least as good as before: the entire retrieved context was previously sent to
the model as one unbroken line with literal `\n` characters instead of real newlines.

---

## E. Chrome extension

| # | Step | Expected |
|---|---|---|
| E1 | Reload the extension at `chrome://extensions` | Loads without console errors |
| E2 | Capture an article | Succeeds as before |

> The endpoint list moved to `extension/config.js` (gitignored). Yours is already written
> with your existing addresses and port 8085, so this should be unchanged — E1 confirms
> the new `config.js` script tag loads correctly.

---

## F. Things to watch over a day or two

- **Nothing should be lost.** The dashboard sweep rewrites `Next_Actions.md` every 15 s;
  confirm no tasks disappear or get mangled over normal use.
- **Telegram responsiveness during a capture.** Send a URL, then immediately press a button
  (e.g. `/next`). It should respond straight away. Previously a slow site could freeze the
  bot for up to 30 s while scraping.
- **Failure notifications.** If an ingest fails you should now get *one* message, then
  retries at 1, 5 and 30 minutes — not three failures in a row within a minute.
- **Sync conflicts.** If Syncthing produces a `Next_Actions.sync-conflict-*.md`, it should
  now be auto-merged with a Telegram notification (this branch was never reached before).
  Normal notes with a trailing uppercase suffix must be left alone.

---

## G. Optional — API authentication

Off by default; the API stays open exactly as before. To enable:

1. Add `API_AUTH_TOKEN=<some-long-random-string>` to `backend/.env`
2. Put the same value in `SECOND_BRAIN_API_TOKEN` in `extension/config.js`
3. `systemctl --user restart second-brain.service`
4. Verify: `curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8085/query \
   -H 'Content-Type: application/json' -d '{"query":"test"}'` → `401`;
   adding `-H "Authorization: Bearer <token>"` → `200`.
   `/health` stays unauthenticated so monitoring keeps working.

---

## Merging

Once you are satisfied:

```bash
cd /home/sdmeers/second_brain
uv run --project backend pytest && uv run --project backend ruff check backend
git checkout main
git merge --no-ff refactor/code-review-fixes
systemctl --user restart second-brain.service
systemctl --user status second-brain.service
```

If anything is wrong, stay on `main` — it is untouched, and the vault changes made by this
branch are all in the normal task/project formats, so nothing needs undoing there.
