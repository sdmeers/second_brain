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
| `/next [@context]` | **Context Filter** — Filter active tasks (`@home`, `@computer`, `@errands`, `@phone`, `@anywhere`, `@office`, `@WFH`). |
| `/waiting` | **Delegated Tracking** — View pending items owed by others with capture age and follow-up dates. |
| `/project <name>` | **Project Dashboard** — View Desired Outcome, Active Actions, Roadmap Steps, and Linked References. |
| `/project <name> add <step>` | **Roadmap Backlog** — Append a new future step under `## Project Steps` in a project note. |
| `/archive <name>` | **Archive Project** — Marks a project complete and moves it from `02_Projects/` to `05_Archive/`. |
| `/done <item>` | **Complete Action** — Archives task to `01_Tasks/Completed.md` and triggers 1-tap step promotion. |
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
   - **`[⚡ 2-Min Rule (Done)]`**: Immediately logs to `01_Tasks/Completed.md` with completion timestamp.
   - **`[🗑️ Trash]`**: Archives and removes the item.
3. **Interactive Calendar Picker:**
   - When deferring an item, a visual keyboard calendar opens (`◀ August 2026 ▶`) allowing you to tap the exact date.

---

## 🎯 Project Master Notes & Step Auto-Promotion

### Project Note Template (`02_Projects/`)
Every project note uses a standard, structured layout:
```markdown
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
```

### 1-Tap Roadmap Step Promotion
When you complete a project action (either via **Telegram `/done <item>`** or **ticking `- [x]` in Obsidian**):
1. The completed task is archived to `01_Tasks/Completed.md`.
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

### Weekly Review (`/reflect` or Friday Briefing)
Designed for strategic reflection:
- **Inbox Zero Audit:** Process remaining unclarified captures.
- **Orphaned Project Check:** Identifies projects with 0 active next actions.
- **Stale Waiting Items:** Flags delegated items waiting for > 5 days.
- **Someday/Maybe Review:** Re-evaluates backlogged ideas for potential activation.
- **AI Research Synthesis:** Synthesizes new concepts ingested into your Research Brain over the past 7 days.

### Holiday Review Pause (`/holiday`)
- Run `/holiday` to pause daily digests and review briefings while on annual leave.
- Choose scope: `[🏠 Personal]`, `[💼 Professional]`, or `[🌴 All Reviews]`.
- Pick your return date using the visual calendar picker.
- Run `/holiday resume` or `/holiday end` to resume reviews early at any time.

---

## 🏃 Day-to-Day Execution Flow

* 🌅 **Morning:** Run `/daily` or review your morning Telegram briefing. Check upcoming deadlines and high-priority contexts.
* 💻 **At Desk:** Type `/next @computer` to focus exclusively on workstation tasks.
* 🚗 **Out & About:** Send voice notes to capture ideas. Type `/next @errands` or `/next @anywhere` when running errands.
* ✅ **Completing Tasks:** Run `/done <task>` or check the box in Obsidian; tap to promote the next roadmap step when prompted.
* 📚 **Filing Reading Material:** Send `/file <URL>` for standard reading or `/research <URL>` for deep concept mapping.
