# Revisions to the GTD & P.A.R.A Architecture Spec

Response to the coding agent's proposal. Sections 1, 3 (Reflect/Engage checks), 4, and 5 are sound as written and don't need changes. This document covers what does.

---

## A. File structure changes

1. **Project subfolders are optional, not default.** Don't stamp every new project with `Notes/ Meetings/ Outputs/` on creation. Create a flat `ProjectName.md` by default; add subfolders only when the project actually generates that kind of material.
2. **Inbox items need a lifecycle after clarify.** Currently unspecified. Processed items should move to `01_Inbox/Archive/` rather than being deleted, and the resulting task/project note should backlink to the source item, so you can trace what you originally said.

---

## B. Corrected Clarify decision tree

The original tree (section 3 of the proposal) has three bugs:

1. **No 2-minute rule.** Every actionable item gets filed, even ones that take 30 seconds.
2. **Projects don't loop back to a next action.** Creating a project folder is a dead end in the flow — nothing forces the bot to also extract the project's first next action in the same pass. The proposal's Reflect check #3 catches this reactively (once a week), but it should never happen in the first place.
3. **"Delegated or Timed?" is a false 3-way choice.** Delegation (who does it) and date-specificity (when it happens) are independent — an item can be both delegated *and* carry a follow-up date. The current tree can only represent one.

### Fixed logic

```
1. Actionable?
   No  → Trash / Reference (Areas or Resources) / Someday-Maybe
   Yes → 2

2. Under 2 minutes?
   Yes → Do it now. Log as done. Never touches a list.
   No  → 3

3. Multi-step outcome (a project)?
   Yes → Create 02_Projects/ note with the outcome defined,
         THEN IMMEDIATELY continue this same clarify exchange
         to extract its first next action → re-enter at step 4
         for that action.
   No  → 4 (this single action)

4. Someone else's to do?
   Yes → Waiting For. Record who, and optionally a follow-up
         date (this is independent of step 5 below — a
         delegated item can still have a date attached).
   No  → 5

5. Must happen at a specific date/time?
   Yes → Calendar.
   No  → Next Actions, tagged #context/<x>.

Cross-cutting, applies to ANY outcome above:
   Optionally attach a defer-until date. The item stays hidden
   from /next and /reflect views until that date, then surfaces
   automatically. (This is the tickler — distinct from
   Someday/Maybe, which is uncertain commitment; tickler is
   certain commitment with deferred visibility.)
```

### Bot conversation implication

When the bot detects "multi-step" at step 3, it must not treat that as the end of the exchange for that inbox item. The prompt sequence becomes:

> 📥 *"Clear the garage"*
> That sounds like a project — what's the outcome? *(user answers)*
> Got it. What's the very next physical action to move it forward? *(user answers — this answer then goes through steps 4–5 above like any other action)*

Only once a next action exists does the bot move to the next inbox item.

### Tickler implementation note

Before building custom defer-date logic: Obsidian's Tasks plugin already has a start-date field designed to hide a task from views until a given date. Worth checking whether that covers this need directly rather than building parallel machinery.

---

## C. Updated command table addition

| Command | Purpose | Action performed |
| :--- | :--- | :--- |
| `/tickle [date]` | Defer an item | Attaches a defer-until date to the current item; hidden from `/next` and `/reflect` until that date |
| `/clarify` (existing) | Interactive clarification | Now loops back into itself when a project is created, until a next action exists for it |
