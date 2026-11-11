import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { restrictToParentElement, restrictToVerticalAxis } from "@dnd-kit/modifiers";
import { arrayMove, SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { ActionCard } from "./ActionCard";
import { CaptureBox } from "./CaptureBox";
import { ClarifySheet } from "./ClarifySheet";
import { EditSheet } from "./EditSheet";
import { BoltIcon, BulbIcon, ChevronIcon, FolderIcon, HourglassIcon, InboxIcon } from "./icons";
import { NextStepPrompt } from "./NextStepPrompt";
import { projectKey } from "./optimistic";
import { ProjectScreen } from "./ProjectScreen";
import { ProjectsTab } from "./ProjectsTab";
import { pendingCount, statusOf, store, useStore, viewOf, type SyncStatus } from "./store";
import type { Category, Item, ListName, Project } from "./types";

type Tab = ListName | "projects";

const TABS: { id: Tab; label: string; icon: ReactNode; empty: string }[] = [
  { id: "next", label: "Next", icon: <BoltIcon className="size-5" />, empty: "All clear. Nothing next on your plate." },
  { id: "waiting", label: "Waiting", icon: <HourglassIcon className="size-5" />, empty: "Nobody owes you anything right now." },
  { id: "someday", label: "Someday", icon: <BulbIcon className="size-5" />, empty: "No someday / maybe ideas parked here." },
  { id: "captured", label: "Captured", icon: <InboxIcon className="size-5" />, empty: "Inbox zero. Tap + to capture a thought." },
  { id: "projects", label: "Projects", icon: <FolderIcon className="size-5" />, empty: "" },
];

const DEFAULT_CONTEXTS = ["@home", "@computer", "@errands", "@phone", "@anywhere", "@office", "@WFH"];

function usePref<T extends string>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => (localStorage.getItem(key) as T | null) ?? initial);
  return [
    value,
    (v: T) => {
      localStorage.setItem(key, v);
      setValue(v);
    },
  ];
}

export default function App() {
  const state = useStore();
  const [category, setCategory] = usePref<Category>("sb_category", "personal");
  const [tab, setTab] = usePref<Tab>("sb_tab", "next");
  const [openProject, setOpenProject] = useState<string | null>(null);
  /** Project whose roadmap is being offered after one of its actions was completed. */
  const [nextStepFor, setNextStepFor] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ item: Item; list: ListName } | null>(null);
  const [showDeferred, setShowDeferred] = useState(false);
  const [undo, setUndo] = useState<{ opId: string; text: string; verb: string } | null>(null);

  useEffect(() => {
    document.documentElement.dataset.category = category;
  }, [category]);

  useEffect(() => {
    if (!undo) return;
    const timer = setTimeout(() => setUndo(null), 5000);
    return () => clearTimeout(timer);
  }, [undo]);

  const view = useMemo(() => viewOf(state), [state]);
  const inCategory = (list: ListName) =>
    list === "captured" ? (view?.lists.captured ?? []) : (view?.lists[list] ?? []).filter((i) => i.category === category);
  const projects = (view?.projects.active ?? []).filter((p) => p.category === category);
  const archived = (view?.projects.archived ?? []).filter((p) => p.category === category);
  const actionsOf = (project: Project) =>
    (["next", "waiting"] as const).flatMap((list) =>
      (view?.lists[list] ?? []).filter((i) => i.project && projectKey(i.project) === projectKey(project.name)).map((i) => ({ ...i, list })),
    );
  const countOf = (t: Tab) => (t === "projects" ? projects.length : inCategory(t).filter((i) => !i.deferred).length);

  const all = tab === "projects" ? [] : inCategory(tab);
  const active = all.filter((i) => !i.deferred);
  const deferred = all.filter((i) => i.deferred);
  // Two identical lines are possible in a Markdown file, so ids carry their position.
  const ids = active.map((item, i) => `${i}:${item.raw}`);

  const knownContexts = useMemo(() => {
    const seen = new Set(DEFAULT_CONTEXTS);
    for (const list of ["next", "waiting", "someday"] as const) view?.lists[list].forEach((i) => i.contexts?.forEach((c) => seen.add(c)));
    return [...seen];
  }, [view]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const onDragEnd = ({ active: dragged, over }: DragEndEvent) => {
    if (!over || dragged.id === over.id || tab === "projects") return;
    const moved = arrayMove(active, ids.indexOf(String(dragged.id)), ids.indexOf(String(over.id)));
    store.reorder(tab, category, [...moved, ...deferred].map((i) => i.raw));
  };

  const complete = (item: Item, list: ListName) => {
    setUndo({ opId: store.complete(list, item.raw), text: item.text, verb: "Done" });
    // Finishing a project's action is the moment to pick its next one. Inside the
    // project screen the roadmap is already in front of you, so no prompt there.
    const project = item.project && view?.projects.active.find((p) => projectKey(p.name) === projectKey(item.project));
    setNextStepFor(project && project.steps.length > 0 && !openProject ? project.name : null);
  };

  const list: ListName = tab === "projects" ? "next" : tab;
  const shownProject = openProject ? view?.projects.active.find((p) => p.name === openProject) : undefined;
  const offered = nextStepFor ? view?.projects.active.find((p) => p.name === nextStepFor && p.steps.length > 0) : undefined;
  const projectNames = {
    personal: (view?.projects.active ?? []).filter((p) => p.category === "personal").map((p) => p.name),
    professional: (view?.projects.active ?? []).filter((p) => p.category === "professional").map((p) => p.name),
  };

  if (state.needToken) return <TokenPrompt />;

  const tabs = (
    <ul className="mx-auto grid max-w-2xl grid-cols-5 sm:gap-1">
      {TABS.map((t) => {
        const on = t.id === tab;
        const count = countOf(t.id);
        return (
          <li key={t.id}>
            <button
              type="button"
              onClick={() => setTab(t.id)}
              aria-current={on ? "page" : undefined}
              className={`flex w-full flex-col items-center gap-0.5 pt-2.5 pb-2 text-[11px] font-medium transition-colors sm:flex-row sm:justify-center sm:gap-2 sm:rounded-xl sm:py-2 sm:text-sm ${
                on
                  ? "text-(--accent) sm:bg-(--accent-soft) sm:text-(--accent-ink)"
                  : "text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300"
              }`}
            >
              <span className="relative">
                {t.icon}
                {count > 0 && (
                  <span
                    className={`absolute -top-1.5 left-3.5 min-w-4 rounded-full px-1 text-center text-[10px] leading-4 font-bold sm:hidden ${
                      on ? "bg-(--accent) text-white dark:text-slate-950" : "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300"
                    }`}
                  >
                    {count}
                  </span>
                )}
              </span>
              {t.label}
              {count > 0 && <span className="hidden text-xs tabular-nums opacity-60 sm:inline">{count}</span>}
            </button>
          </li>
        );
      })}
    </ul>
  );

  const canEditList = tab !== "captured" && tab !== "projects";
  const current = TABS.find((t) => t.id === tab)!;

  return (
    <div className="mx-auto flex min-h-dvh max-w-2xl flex-col">
      <header className="sticky top-0 z-20 bg-slate-50/85 px-4 pt-[max(0.75rem,env(safe-area-inset-top))] pb-3 backdrop-blur-md dark:bg-slate-950/85">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold tracking-tight">GTD</h1>
          <StatusPill status={statusOf(state)} pending={pendingCount(state)} />
        </div>

        <div className="mt-3 grid grid-cols-2 gap-1 rounded-2xl bg-slate-200/70 p-1 dark:bg-slate-800/80">
          {(["personal", "professional"] as Category[]).map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setCategory(c)}
              aria-pressed={category === c}
              className={`rounded-xl py-2 text-sm font-semibold capitalize transition-all ${
                category === c
                  ? "bg-white text-(--accent-ink) shadow-sm dark:bg-slate-700 dark:text-(--accent)"
                  : "text-slate-500 dark:text-slate-400"
              }`}
            >
              {c}
            </button>
          ))}
        </div>

        <div className="mt-3 hidden sm:block">{tabs}</div>
      </header>

      <main className="flex-1 px-4 pt-1 pb-28 sm:pb-10">
        {state.notice && (
          <div className="mb-3 flex items-start gap-3 rounded-2xl bg-amber-100 px-4 py-3 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
            <p className="flex-1">{state.notice}</p>
            <button type="button" onClick={() => store.dismissNotice()} className="font-semibold">
              OK
            </button>
          </div>
        )}

        {tab === "captured" && active.length > 0 && (
          <p className="mb-3 px-1 text-sm text-slate-500 dark:text-slate-400">
Tap an item to decide what it is and where it goes.
          </p>
        )}

        {!view ? (
          state.offline ? (
            <Empty title="Can't reach your Second Brain" hint="Check that Tailscale is connected, then pull this page back into view." />
          ) : (
            <ul className="space-y-2.5" aria-busy="true">
              {[72, 56, 64, 56].map((h, i) => (
                <li key={i} className="animate-pulse rounded-2xl bg-slate-200/70 dark:bg-slate-800/70" style={{ height: h }} />
              ))}
            </ul>
          )
        ) : tab === "projects" ? (
          <ProjectsTab category={category} projects={projects} archived={archived} actionsOf={actionsOf} onOpen={setOpenProject} />
        ) : all.length === 0 ? (
          <Empty title={current.empty} />
        ) : (
          <>
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              modifiers={[restrictToVerticalAxis, restrictToParentElement]}
              onDragEnd={onDragEnd}
            >
              <SortableContext items={ids} strategy={verticalListSortingStrategy}>
                <ul className="space-y-2.5">
                  {active.map((item, i) => (
                    <ActionCard
                      key={ids[i]}
                      id={ids[i]}
                      item={item}
                      canComplete={canEditList && !item.pending}
                      canDrag={canEditList && !item.pending && active.length > 1}
                      onOpen={() => !item.pending && setEditing({ item, list })}
                      onComplete={() => complete(item, list)}
                    />
                  ))}
                </ul>
              </SortableContext>
            </DndContext>

            {deferred.length > 0 && (
              <section className="mt-6">
                <button
                  type="button"
                  onClick={() => setShowDeferred(!showDeferred)}
                  aria-expanded={showDeferred}
                  className="flex items-center gap-1.5 px-1 text-sm font-semibold text-slate-500 dark:text-slate-400"
                >
                  <ChevronIcon className={`size-4 transition-transform ${showDeferred ? "rotate-90" : ""}`} />
                  Hidden until later · {deferred.length}
                </button>
                {showDeferred && (
                  <ul className="mt-2.5 space-y-2.5 opacity-70">
                    {deferred.map((item, i) => (
                      <ActionCard
                        key={`d${i}:${item.raw}`}
                        id={`d${i}:${item.raw}`}
                        item={item}
                        canComplete={canEditList && !item.pending}
                        canDrag={false}
                        onOpen={() => !item.pending && setEditing({ item, list })}
                        onComplete={() => complete(item, list)}
                      />
                    ))}
                  </ul>
                )}
              </section>
            )}
          </>
        )}
      </main>

      {/* On phones the tabs sit at the bottom, within thumb reach. This must stay outside
          <header>: its backdrop blur makes it the containing block for fixed children. */}
      <nav className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200 bg-white/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-md sm:hidden dark:border-slate-800 dark:bg-slate-950/90">
        {tabs}
      </nav>

      {undo && (
        <div className="pointer-events-none fixed inset-x-0 bottom-20 z-30 flex justify-center px-4 sm:bottom-8">
          <div className="animate-rise pointer-events-auto flex max-w-md items-center gap-4 rounded-2xl bg-slate-900 py-3 pr-3 pl-4 text-sm text-white shadow-xl dark:bg-slate-100 dark:text-slate-900">
            <span className="min-w-0 truncate">
              {undo.verb}: {undo.text}
            </span>
            <button
              type="button"
              onClick={() => {
                store.undo(undo.opId);
                setUndo(null);
                setNextStepFor(null);
              }}
              className="shrink-0 rounded-lg px-2 py-1 font-semibold text-teal-300 dark:text-teal-700"
            >
              Undo
            </button>
          </div>
        </div>
      )}

      {offered && !editing && !shownProject && (
        <NextStepPrompt key={offered.name} project={offered} knownContexts={knownContexts} onClose={() => setNextStepFor(null)} />
      )}

      {shownProject && (
        <ProjectScreen
          key={shownProject.name}
          project={shownProject}
          actions={actionsOf(shownProject)}
          knownContexts={knownContexts}
          onClose={() => setOpenProject(null)}
          onOpenAction={(item, l) => setEditing({ item, list: l })}
          onComplete={complete}
        />
      )}

      {!editing && !shownProject && <CaptureBox />}

      {editing && editing.list === "captured" && (
        <ClarifySheet
          key={editing.item.raw}
          item={editing.item}
          category={category}
          projects={projectNames}
          knownContexts={knownContexts}
          onClose={() => setEditing(null)}
          onTrashed={(opId, text) => setUndo({ opId, text, verb: "Trashed" })}
        />
      )}
      {editing && editing.list !== "captured" && (
        <EditSheet
          key={editing.item.raw}
          item={editing.item}
          list={editing.list}
          knownContexts={knownContexts}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mt-20 px-6 text-center">
      <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-full bg-(--accent-soft) text-2xl text-(--accent-ink)">
        ✓
      </div>
      <p className="font-medium text-slate-600 dark:text-slate-300">{title}</p>
      {hint && <p className="mt-1 text-sm text-slate-400">{hint}</p>}
    </div>
  );
}

function StatusPill({ status, pending }: { status: SyncStatus; pending: number }) {
  const looks: Record<SyncStatus, { dot: string; text: string }> = {
    synced: { dot: "bg-emerald-500", text: "Synced" },
    syncing: { dot: "bg-sky-500 animate-pulse", text: "Syncing…" },
    pending: { dot: "bg-amber-500", text: `${pending} to sync` },
    offline: { dot: "bg-slate-400", text: pending ? `Offline · ${pending} to sync` : "Offline" },
  };
  const { dot, text } = looks[status];
  return (
    <button
      type="button"
      onClick={() => void store.sync()}
      title="Sync now"
      className="flex items-center gap-2 rounded-full bg-white px-3 py-1.5 text-xs font-medium text-slate-600 shadow-sm ring-1 ring-slate-900/5 dark:bg-slate-800 dark:text-slate-300 dark:ring-white/10"
    >
      <span className={`size-2 rounded-full ${dot}`} />
      {text}
    </button>
  );
}

function TokenPrompt() {
  const [token, setToken] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (token.trim()) store.setToken(token);
  };
  return (
    <form onSubmit={submit} className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-4 px-6">
      <h1 className="text-2xl font-bold tracking-tight">GTD</h1>
      <p className="text-sm text-slate-500 dark:text-slate-400">
        Enter the API token from your Second Brain&apos;s <span className="font-mono">backend/.env</span>. It&apos;s kept on
        this device only.
      </p>
      <input
        type="password"
        value={token}
        onChange={(e) => setToken(e.target.value)}
        placeholder="API token"
        autoFocus
        className="rounded-xl border border-slate-200 bg-white px-3 py-3 outline-none focus:border-(--accent) focus:ring-2 focus:ring-(--accent)/20 dark:border-slate-700 dark:bg-slate-800"
      />
      <button type="submit" className="rounded-xl bg-(--accent) py-3 font-semibold text-white dark:text-slate-950">
        Continue
      </button>
    </form>
  );
}
