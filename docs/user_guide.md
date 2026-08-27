# 📘 Second Brain: User Guide & Workflows

This guide outlines your day-to-day workflow with the Second Brain, leveraging the integrated **Getting Things Done (GTD)** and **P.A.R.A** methodologies.

---

## 🔄 The 5 GTD Stages in Practice

1. **Capture:** Get everything out of your head with zero friction.
   - **Voice:** Send a voice note in Telegram while driving or walking. The local GPU transcribes and saves it to `01_Tasks/Captured.md`.
   - **Text:** Type `/in <thought>` in Telegram or drop files into `00_System/Inbox/`.
   - **Reference/Files:** Send `/file <URL/PDF>` to route straight to the AI Librarian (skipping GTD triage).
2. **Clarify:** Process your capture backlog.
   - Run `/clarify` in Telegram to triage items through an interactive 8-option GTD menu (`Next Action`, `Existing Project Roadmap`, `New Project`, `Waiting For`, `Someday/Maybe`, `Pass to AI Librarian`, `2-Min Rule`, `Trash`).
   - Use the built-in **Telegram Visual Calendar Picker** for 1-tap date deferrals.
3. **Organise:** Everything lands in its exact, structured home:
   - `01_Tasks/Next_Actions.md` for immediate actions tagged with `@context` and `#project/<Name>`.
   - `01_Tasks/Waiting_For.md` for delegated actions (`#waiting(Name)`).
   - `01_Tasks/Someday_Maybe.md` for deferred ideas and uncommitted desires.
   - `02_Projects/.../<Project>.md` for multi-step goals, future roadmaps (`## Project Steps`), and active actions.
4. **Reflect:** Keep your system trustworthy and current.
   - **Daily Review (`/daily`):** Action-oriented morning briefing listing upcoming deadlines (<= 14 days), surfaced ticklers, active Next Actions grouped by context, and pending follow-ups.
   - **Weekly Review (`/reflect`):** Strategic Friday/Monday review covering orphaned projects, stale items, Someday/Maybe ideas, and AI concept synthesis.
   - **Holiday Mode (`/holiday`):** Temporarily pause automated reviews while away.
5. **Engage:** Execute with clarity and focus.
   - Run `/next @errands` when out shopping or `/next @computer` at your desk.
   - Check off tasks via **`/done <item>`** in Telegram or by ticking the **`- [x]` checkbox in Obsidian**.
   - Completing a project task automatically prompts you to promote the next step from your roadmap.

---

## 🤖 Telegram Command Reference

The Unified Telegram Bot is your central command interface.

| Command | Purpose & Description |
| :--- | :--- |
| *(text)* | **Q&A** — Natural language conversational Q&A against your vault (with 5-turn context memory). |
| *(voice)* | **Capture** — Transcribes speech via local Faster-Whisper and logs to `01_Tasks/Captured.md`. |
| `/in <text>` | **GTD Capture** — Append a raw thought directly to `01_Tasks/Captured.md`. |
| `/file <URL/doc>` | **Direct Filing** — Passes URLs, PDFs, or text directly to the AI Librarian (skipping GTD clarify). |
| `/research <URL>` | **Deep Research** — Ingests content into Research Brain with full knowledge graph & concept extraction. |
| `/clarify` | **Interactive Triage** — Process captured items in 5-item batches with inline buttons & calendar picker. |
| `/next [@context]` | **Context Filter** — Bare `/next` offers a Personal / Professional / Both picker; `/next @context` filters directly (`@home`, `@computer`, `@errands`, `@phone`, `@anywhere`, `@office`, `@WFH`). |
| `/waiting` | **Delegated Tracking** — View pending items owed by others with capture age and follow-up dates. |
| `/project <name>` | **Project Dashboard** — View Desired Outcome, Active Actions, Roadmap Steps, and Linked References. |
| `/project <name> add <step>` | **Roadmap Backlog** — Append a new future step under `## Project Steps` in a project note. |
| `/archive <name>` | **Archive Project** — Sets `status: completed` and moves the whole project folder to `05_Archive/02_Projects/<Category>/`. |
| `/done <item>` | **Complete Action** — Archives task to `01_Tasks/z_Completed.md` and triggers 1-tap step promotion. |
| `/tickle <item>` | **Defer Task** — Opens interactive Telegram calendar picker to set a `🔔 YYYY-MM-DD` tickler date. |
| `/daily` | **Daily Review** — On-demand action-oriented review (deadlines <=14d, surfaced ticklers, next actions by context). |
| `/weekly` / `/status` | **System Sitrep** — High-level counts across all GTD task lists, active projects, and 7-day completed actions. |
| `/reflect` | **Guided Review** — Step-by-step interactive weekly reflection (inbox zero, stale waitings, orphaned projects, Someday/Maybe). |
| `/holiday` | **Pause Reviews** — Set holiday mode by scope (`Personal`, `Professional`, `All`) via visual calendar picker. |
| `/jira <summary>` | **Jira Export** — Creates a ticket directly in Jira backlog. |
| `/undo` | **Safety Revert** — Reverts the last automated Git commit (`git revert HEAD`). |
| `/help` | **Help Menu** — Full command reference and workflow guide. |

---

## ⚡ Interactive Clarification Workflow (`/clarify`)

When you run `/clarify`, unclarified items in `01_Tasks/Captured.md` are presented one by one in batches of 5:

1. **AI Smart Defaults (One-Tap Fast Path):**
   - High-confidence suggestions (e.g. `📥 "Buy paint at B&Q" -> [⚡ Add as @errands #project/Garage]`).
2. **8-Option Classification Menu:**
   - **`[⚡ Next Action]`**: Select context (`@home`, `@computer`, `@errands`, `@phone`, `@anywhere`, `@office`) and optionally link an active project.
   - **`[📌 Existing Project Roadmap]`**: Select category (`Personal` vs `Professional`) and pick an active project from a 2-column list to append as a future step under `## Project Steps`.
   - **`[📁 New Project]`**: Prompts for Name and Desired Outcome, auto-generating the project master note.
   - **`[⏳ Waiting For]`**: Prompts for who or what you are waiting on (`#waiting(Name)`), then optionally links an active project (`#project/Project_Name`) before recording to `Waiting_For.md`.
   - **`[💡 Someday / Maybe]`**: Logs item to `Someday_Maybe.md` for future weekly review.
   - **`[📚 Pass to AI Librarian]`**: Forwards reference material to PARA folders (`03_Areas/` or `04_Resources/`).
   - **`[⚡ 2-Min Rule (Done)]`**: Immediately logs to `01_Tasks/z_Completed.md` with completion timestamp.
   - **`[🗑️ Trash]`**: Archives and removes the item.
3. **Interactive Calendar Picker:**
   - When deferring an item, a visual keyboard calendar opens (`◀ August 2026 ▶`) allowing you to tap the exact date.

---

## 🎯 Project Master Notes & Step Auto-Promotion

### Dedicated Project Folders (`02_Projects/`)
Every project is housed in its own dedicated directory under `02_Projects/Personal/<Project_Name>/` or `02_Projects/Professional/<Project_Name>/`:
- **Master Summary Note (`<Project_Name>.md`):** Contains the YAML metadata, Desired Outcome, future `## Project Steps` roadmap backlog, and dynamic Dataview task queries.
- **Project Files & Subfolders:** You can freely store project-specific notes, meeting minutes (`Meetings/`), deliverables (`Outputs/`), and reference documents directly within the project's folder.

### Project Note Template
````markdown
---
title: "Garage"
type: project
category: Personal
outcome: "Fully organised garage with shelving, workbench, and storage"
due_date: ""
date_created: "2026-08-08"
status: active
---
# Garage

## Desired Outcome
Fully organised garage with shelving, workbench, and storage.

## Project Steps
- [ ] Buy storage for the top box in the garage
- [ ] Buy shelves and a workbench for the garage

## Active Actions
> View via Telegram: `/project Garage` or search `#project/Garage` in Obsidian.

```dataview
TASK
FROM "01_Tasks"
WHERE contains(tags, "#project/Garage") AND !completed
```

## Reference Material
- [[02_Projects/Personal/Garage/Garage shelving dimensions.md]]
````

### 1-Tap Roadmap Step Promotion
When you complete a project action (either via **Telegram `/done <item>`** or **ticking `- [x]` in Obsidian**):
1. The completed task is archived to `01_Tasks/z_Completed.md`.
2. The bot inspects `## Project Steps` in the project note for unpromoted steps.
3. Telegram immediately pings you with an interactive prompt:
   ```text
   🎯 Project Roadmap: #Garage

   Which step would you like to promote to Next Actions?
   [1. Buy storage for the top box...]
   [2. Buy shelves and a workbench...]
   [⏭️ Skip / None for now]
   ```
4. Tapping a step prompts for the context (`[@home]`, `[@anywhere]`, etc.).
5. The step is appended to `01_Tasks/Next_Actions.md` and **automatically deleted from `## Project Steps`**, keeping your roadmap clean!

---

## 📅 Review Routines & Holiday Mode

### Daily Review (`/daily` or 07:30 Morning Briefing)
Designed for immediate daily execution:
- 🚨 **Upcoming & Overdue Deadlines:** Projects with `due_date` within 14 days.
- 🔔 **Surfaced Deferrals:** Tasks deferred to today unlocking for action.
- ⚡ **Next Actions by Context:** Actual actionable tasks grouped by `@home`, `@computer`, `@errands`, `@phone`, `@anywhere`, `@office`.
- ⏳ **Waiting For:** Active delegated items to follow up on.
*(Note: Someday/Maybe ideas are excluded from the daily review to eliminate noise).*

### Guided Weekly Reflection (`/reflect`)
An interactive, 5-step review routine in Telegram:
- **Step 1: 📥 Get Clear (Inbox Zero):** Audits unclarified items in `01_Tasks/Captured.md` with a 1-tap **`[⚡ Launch /clarify]`** button.
- **Step 2: ⏳ Audit Delegations (Waiting For):** Lists all pending delegated items with owner names and age, highlighting any `⚠️ STALE (>5d)` items.
- **Step 3: 🎯 Project Health & Momentum:** Scans for **Orphaned Projects** (0 active next actions) and offers 1-tap buttons to promote roadmap steps immediately.
- **Step 4: 💡 Someday / Maybe Review:** Re-evaluates backlogged ideas for activation or cleanup.
- **Step 5: 🚨 Deadlines & Accomplishments:** Reviews upcoming project deadlines (`due_date <= 14d`) and celebrates weekly completed tasks.
*(You can navigate between reflection steps at your own pace, or run `/clarify` independently anytime).*

### System Status & Sitrep (`/weekly` or `/status`)
- Run `/weekly` or `/status` at any time for a high-level system sitrep (item counts across all lists and full active project directory).

### Holiday Review Pause (`/holiday`)
- Run `/holiday` to pause daily digests and review briefings while on annual leave.
- Choose scope: `[🏠 Personal]`, `[💼 Professional]`, or `[🌴 All Reviews]`.
- Pick your return date using the visual calendar picker, **or type it directly**:
  `/holiday personal 23rd Aug`, `/holiday all 14/09`, `/holiday professional +14d`.
- Run `/holiday resume` or `/holiday end` to resume reviews early at any time.
- `/holiday status` shows which scopes are currently paused and until when.

### Deferring a Task (`/tickle`)
- `/tickle <task search text>` opens the visual calendar picker; tapping a date stamps the
  matching task with `🔔 YYYY-MM-DD`.
- Deferred tasks are hidden from `/next` and the daily review until that date arrives,
  then surface automatically.

### Accepted Date Formats
Anywhere the system accepts a typed date (`/holiday`, clarify deferrals):

| Style | Examples |
| :--- | :--- |
| Relative | `tomorrow`, `next week`, `next month`, `+7d`, `14d` |
| ISO | `2026-09-01` |
| Day-month | `23rd Aug`, `23 Aug`, `23 August 2027` |
| Month-day | `Aug 23`, `August 23 2027` |
| Numeric | `14/03/2027`, `14/03` |

A bare day-and-month that has already passed this year rolls forward to next year.
Anything unrecognised defaults to one week out.

---

## 🏃 Day-to-Day Execution Flow

* 🌅 **Morning:** Run `/daily` or review your morning Telegram briefing. Check upcoming deadlines and high-priority contexts.
* 💻 **At Desk:** Type `/next @computer` to focus exclusively on workstation tasks.
* 🚗 **Out & About:** Send voice notes to capture ideas. Type `/next @errands` or `/next @anywhere` when running errands.
* ✅ **Completing Tasks:** Run `/done <task>` or check the box in Obsidian; tap to promote the next roadmap step when prompted.
* 📚 **Filing Reading Material:** Send `/file <URL>` for standard reading or `/research <URL>` for deep concept mapping.

---

## 🧾 Writing Tasks by Hand in Obsidian

You can edit `01_Tasks/*.md` directly — the daemon reconciles them within ~15 seconds.
It rewrites `Next_Actions.md` on each sweep (sorting by due date and splitting Personal /
Professional), so a few conventions matter:

- **Follow the line format:**
  `- [ ] <description> @<context> #project/<Name> #<category> 📅 YYYY-MM-DD 🔔 YYYY-MM-DD`
- **Ticking `- [x]`** anywhere in `01_Tasks/` archives the item to `z_Completed.md` on the
  next sweep, and triggers roadmap step promotion if it carried a `#project/` tag.
- **Untagged items** automatically gain `#personal` or `#professional` (inferred from the
  project's folder) the next time the dashboard is rebuilt.
- **Square brackets in task text are safe** — `- [ ] [Draft] review the spec` is preserved
  intact.
- **Placeholder lines** (`🎉 *All clear!*`, `*None*`) are managed by the system; you can
  ignore them, and they are removed automatically once a real item exists.

Project master notes are equally editable. Frontmatter is written as standard YAML, so
titles containing colons or quotes (`Phase 1: Discovery`) render correctly in Obsidian's
properties panel.
