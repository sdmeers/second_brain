import { useState, type FormEvent } from "react";
import { describeDue, shortDate } from "./dates";
import { CalendarIcon, ChevronIcon, PlusIcon } from "./icons";
import { store } from "./store";
import type { ArchivedProject, Category, Item, Project } from "./types";

const chip = "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium";
const input =
  "w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-[15px] outline-none focus:border-(--accent) focus:ring-2 focus:ring-(--accent)/20 dark:border-slate-700 dark:bg-slate-800 dark:[color-scheme:dark]";

interface Props {
  category: Category;
  projects: Project[];
  archived: ArchivedProject[];
  /** Open Next and Waiting actions of a project. None means it is stuck. */
  actionsOf: (project: Project) => Item[];
  onOpen: (name: string) => void;
}

export function ProjectsTab({ category, projects, archived, actionsOf, onOpen }: Props) {
  const [adding, setAdding] = useState(false);
  const [showArchived, setShowArchived] = useState(false);

  return (
    <>
      {adding ? (
        <NewProject category={category} taken={projects.map((p) => p.name)} onClose={() => setAdding(false)} />
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="mb-3 flex w-full items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-slate-300 py-3 text-sm font-semibold text-slate-500 hover:border-(--accent) hover:text-(--accent) dark:border-slate-700 dark:text-slate-400"
        >
          <PlusIcon className="size-4" />
          New project
        </button>
      )}

      {projects.length === 0 && !adding && (
        <p className="mt-16 px-6 text-center font-medium text-slate-500 dark:text-slate-400">No {category} projects on the go.</p>
      )}

      <ul className="space-y-2.5">
        {projects.map((p) => {
          const actions = actionsOf(p).length;
          const due = p.due ? describeDue(p.due) : null;
          return (
            <li key={p.name}>
              <button
                type="button"
                onClick={() => onOpen(p.name)}
                className="flex w-full items-center gap-3 rounded-2xl bg-white px-4 py-3.5 text-left shadow-sm ring-1 ring-slate-900/5 dark:bg-slate-900 dark:ring-white/10"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-[15px] leading-snug font-semibold break-words">{p.name.replace(/_/g, " ")}</p>
                  {p.outcome && <p className="mt-0.5 truncate text-sm text-slate-500 dark:text-slate-400">{p.outcome}</p>}
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {actions === 0 ? (
                      <span className={`${chip} bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300`}>Stuck · no next action</span>
                    ) : (
                      <span className={`${chip} bg-(--accent-soft) text-(--accent-ink)`}>
                        {actions} action{actions > 1 ? "s" : ""}
                      </span>
                    )}
                    {p.steps.length > 0 && (
                      <span className={`${chip} bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300`}>
                        {p.steps.length} step{p.steps.length > 1 ? "s" : ""} planned
                      </span>
                    )}
                    {due && (
                      <span
                        className={`${chip} ${
                          due.tone === "overdue"
                            ? "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300"
                            : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                        }`}
                      >
                        <CalendarIcon className="size-3" />
                        {due.label}
                      </span>
                    )}
                  </div>
                </div>
                <ChevronIcon className="size-5 shrink-0 text-slate-300 dark:text-slate-600" />
              </button>
            </li>
          );
        })}
      </ul>

      {archived.length > 0 && (
        <section className="mt-6">
          <button
            type="button"
            onClick={() => setShowArchived(!showArchived)}
            aria-expanded={showArchived}
            className="flex items-center gap-1.5 px-1 text-sm font-semibold text-slate-500 dark:text-slate-400"
          >
            <ChevronIcon className={`size-4 transition-transform ${showArchived ? "rotate-90" : ""}`} />
            Archived · {archived.length}
          </button>
          {showArchived && (
            <ul className="mt-2.5 divide-y divide-slate-200 rounded-2xl bg-white px-4 ring-1 ring-slate-900/5 dark:divide-slate-800 dark:bg-slate-900 dark:ring-white/10">
              {archived.map((a) => (
                <li key={a.name} className="flex items-center gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-600 dark:text-slate-300">{a.name.replace(/_/g, " ")}</p>
                    {a.completed && <p className="text-xs text-slate-400">Completed {shortDate(a.completed.slice(0, 10))}</p>}
                  </div>
                  <button
                    type="button"
                    onClick={() => store.send({ type: "project_restore", project: a.name, category: a.category })}
                    className="shrink-0 rounded-lg px-2.5 py-1.5 text-sm font-semibold text-(--accent)"
                  >
                    Restore
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </>
  );
}

function NewProject({ category, taken, onClose }: { category: Category; taken: string[]; onClose: () => void }) {
  const [name, setName] = useState("");
  const [outcome, setOutcome] = useState("");
  const clean = name.replace(/\s+/g, " ").trim();
  const clash = taken.some((t) => t.replace(/_/g, " ").toLowerCase() === clean.toLowerCase());

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!clean || clash) return;
    store.send({ type: "project_create", fields: { name: clean, category, outcome: outcome.trim() } });
    onClose();
  };

  return (
    <form onSubmit={submit} className="mb-3 space-y-3 rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-900/5 dark:bg-slate-900 dark:ring-white/10">
      <input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="Project name" autoFocus />
      <input className={input} value={outcome} onChange={(e) => setOutcome(e.target.value)} placeholder="What does done look like? (optional)" />
      {clash && <p className="text-xs text-rose-600 dark:text-rose-400">There is already a project with that name.</p>}
      <div className="flex gap-2">
        <button type="button" onClick={onClose} className="flex-1 rounded-xl py-2.5 text-sm font-medium text-slate-500">
          Cancel
        </button>
        <button type="submit" disabled={!clean || clash} className="flex-1 rounded-xl bg-(--accent) py-2.5 text-sm font-semibold text-white disabled:opacity-40 dark:text-slate-950">
          Create <span className="capitalize">{category}</span> project
        </button>
      </div>
    </form>
  );
}
