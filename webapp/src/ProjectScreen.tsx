import { closestCenter, DndContext, KeyboardSensor, PointerSensor, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { restrictToParentElement, restrictToVerticalAxis } from "@dnd-kit/modifiers";
import { arrayMove, SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useEffect, useState, type FormEvent } from "react";
import { ArrowUpIcon, CheckIcon, ChevronIcon, GripIcon, HourglassIcon, TrashIcon } from "./icons";
import { store } from "./store";
import type { Item, ListName, Project } from "./types";

const label = "mb-2 block text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400";
const input =
  "w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-[15px] outline-none focus:border-(--accent) focus:ring-2 focus:ring-(--accent)/20 dark:border-slate-700 dark:bg-slate-800 dark:[color-scheme:dark]";
const card = "rounded-2xl bg-white ring-1 ring-slate-900/5 dark:bg-slate-800/60 dark:ring-white/10";

interface Props {
  project: Project;
  /** Its open Next and Waiting actions. */
  actions: (Item & { list: ListName })[];
  knownContexts: string[];
  onClose: () => void;
  onOpenAction: (item: Item, list: ListName) => void;
  onComplete: (item: Item, list: ListName) => void;
}

export function ProjectScreen({ project, actions, knownContexts, onClose, onOpenAction, onComplete }: Props) {
  const [outcome, setOutcome] = useState(project.outcome);
  const [newStep, setNewStep] = useState("");
  /** The step being given a context on its way to Next. */
  const [promoting, setPromoting] = useState<string | null>(null);
  const [editingStep, setEditingStep] = useState<string | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const name = project.name;

  useEffect(() => {
    // Escape closes the top-most sheet only: if an action is open over this screen,
    // that sheet handles it.
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && document.querySelectorAll('[role="dialog"]').length === 1 && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  // Two steps can read the same, so ids carry their position.
  const ids = project.steps.map((s, i) => `${i}:${s}`);

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    const order = arrayMove(project.steps, ids.indexOf(String(active.id)), ids.indexOf(String(over.id)));
    store.send({ type: "step_reorder", project: name, order });
  };

  const saveOutcome = () => {
    const clean = outcome.replace(/\s+/g, " ").trim();
    if (clean !== project.outcome) store.send({ type: "project_update", project: name, fields: { outcome: clean } });
  };

  const addStep = (e: FormEvent) => {
    e.preventDefault();
    const clean = newStep.replace(/\s+/g, " ").trim();
    if (!clean) return;
    store.send({ type: "step_add", project: name, text: clean });
    setNewStep("");
  };

  const promote = (step: string, context?: string) => {
    store.send({ type: "step_promote", project: name, step, fields: { contexts: context ? [context] : [] } });
    setPromoting(null);
  };

  return (
    <div className="fixed inset-0 z-30 flex justify-center bg-slate-950/40 backdrop-blur-[2px] sm:p-6" role="dialog" aria-modal="true">
      <div className="absolute inset-0" onClick={onClose} />
      <div className="animate-sheet relative flex h-full w-full flex-col bg-slate-50 shadow-2xl sm:max-w-2xl sm:rounded-3xl dark:bg-slate-900">
        <div className="flex items-center gap-1 px-2 pt-[max(0.75rem,env(safe-area-inset-top))] pb-2 sm:pt-3">
          <button type="button" onClick={onClose} aria-label="Back to projects" className="rounded-full p-2 text-slate-500 hover:bg-slate-200/60 dark:hover:bg-slate-800">
            <ChevronIcon className="size-6 rotate-180" />
          </button>
          <h2 className="min-w-0 flex-1 truncate text-lg font-bold tracking-tight">{name.replace(/_/g, " ")}</h2>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto px-4 pt-1 pb-10">
          <div className="grid gap-3 sm:grid-cols-[1fr_11rem]">
            <label className="block">
              <span className={label}>Outcome</span>
              <input
                className={input}
                value={outcome}
                onChange={(e) => setOutcome(e.target.value)}
                onBlur={saveOutcome}
                onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                placeholder="What does done look like?"
              />
            </label>
            <label className="block">
              <span className={label}>Due</span>
              <input
                type="date"
                className={input}
                value={project.due ?? ""}
                onChange={(e) => store.send({ type: "project_update", project: name, fields: { due_date: e.target.value || null } })}
              />
            </label>
          </div>

          <section>
            <span className={label}>Actions</span>
            {actions.length === 0 ? (
              <p className="rounded-2xl bg-amber-100 px-4 py-3 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
                Nothing in Next or Waiting for this project.{" "}
                {project.steps.length > 0 ? "Move a roadmap step up to give it a next action." : "Add a step below, then move it to Next."}
              </p>
            ) : (
              <ul className={`${card} divide-y divide-slate-100 dark:divide-slate-700/60`}>
                {actions.map((a, i) => (
                  <li key={`${i}:${a.raw}`} className={`flex items-stretch ${a.pending ? "opacity-60" : ""}`}>
                    <button
                      type="button"
                      disabled={a.pending}
                      onClick={() => onComplete(a, a.list)}
                      aria-label={`Complete: ${a.text}`}
                      className="group flex shrink-0 items-center py-3 pr-2 pl-3.5"
                    >
                      <span className="flex size-6 items-center justify-center rounded-full border-2 border-slate-300 text-transparent group-hover:border-(--accent) group-hover:text-(--accent)/50 dark:border-slate-600">
                        <CheckIcon className="size-3.5" />
                      </span>
                    </button>
                    <button type="button" disabled={a.pending} onClick={() => onOpenAction(a, a.list)} className="min-w-0 flex-1 py-3 pr-3.5 text-left">
                      <p className="text-[15px] leading-snug break-words">{a.text}</p>
                      <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-slate-400">
                        {a.list === "waiting" ? (
                          <span className="inline-flex items-center gap-1 text-(--accent-ink)">
                            <HourglassIcon className="size-3" />
                            Waiting{a.waiting_on ? ` on ${a.waiting_on}` : ""}
                          </span>
                        ) : (
                          <span>Next</span>
                        )}
                        {a.contexts?.map((c) => <span key={c}>{c}</span>)}
                        {a.pending && <span>Saving…</span>}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <span className={label}>Roadmap</span>
            {project.steps.length > 0 && (
              <DndContext sensors={sensors} collisionDetection={closestCenter} modifiers={[restrictToVerticalAxis, restrictToParentElement]} onDragEnd={onDragEnd}>
                <SortableContext items={ids} strategy={verticalListSortingStrategy}>
                  <ul className="mb-2.5 space-y-2">
                    {project.steps.map((step, i) => (
                      <StepRow
                        key={ids[i]}
                        id={ids[i]}
                        step={step}
                        canDrag={project.steps.length > 1}
                        editing={editingStep === ids[i]}
                        promoting={promoting === ids[i]}
                        knownContexts={knownContexts}
                        onEdit={() => setEditingStep(ids[i])}
                        onRename={(text) => {
                          // Blur can fire more than once as the field goes away; a second
                          // rename would name a step that no longer exists.
                          if (editingStep !== ids[i]) return;
                          if (text && text !== step) store.send({ type: "step_edit", project: name, step, text });
                          setEditingStep(null);
                        }}
                        onRemove={() => store.send({ type: "step_remove", project: name, step })}
                        onPromote={() => setPromoting(promoting === ids[i] ? null : ids[i])}
                        onContext={(c) => promote(step, c)}
                      />
                    ))}
                  </ul>
                </SortableContext>
              </DndContext>
            )}
            <form onSubmit={addStep} className="flex gap-2">
              <input className={input} value={newStep} onChange={(e) => setNewStep(e.target.value)} placeholder="Add a step…" enterKeyHint="done" />
              <button type="submit" disabled={!newStep.trim()} className="shrink-0 rounded-xl bg-(--accent) px-4 text-sm font-semibold text-white disabled:opacity-40 dark:text-slate-950">
                Add
              </button>
            </form>
          </section>

          <section className="pt-2">
            {confirmArchive ? (
              <div className={`${card} space-y-3 p-4`}>
                <p className="text-sm text-slate-600 dark:text-slate-300">
                  Archive <b>{name.replace(/_/g, " ")}</b>? It moves out of your active projects; you can restore it later.
                  {actions.length > 0 && (
                    <>
                      {" "}
                      Its {actions.length} open action{actions.length > 1 ? "s" : ""} will stay on your lists.
                    </>
                  )}
                </p>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setConfirmArchive(false)} className="flex-1 rounded-xl py-2.5 text-sm font-medium text-slate-500">
                    Keep it
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      store.send({ type: "project_archive", project: name });
                      onClose();
                    }}
                    className="flex-1 rounded-xl bg-slate-800 py-2.5 text-sm font-semibold text-white dark:bg-slate-200 dark:text-slate-900"
                  >
                    Archive project
                  </button>
                </div>
              </div>
            ) : (
              <button type="button" onClick={() => setConfirmArchive(true)} className="w-full rounded-xl py-3 text-sm font-semibold text-slate-500 ring-1 ring-slate-200 dark:text-slate-400 dark:ring-slate-700">
                Project finished? Archive it
              </button>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

interface StepProps {
  id: string;
  step: string;
  canDrag: boolean;
  editing: boolean;
  promoting: boolean;
  knownContexts: string[];
  onEdit: () => void;
  onRename: (text: string) => void;
  onRemove: () => void;
  onPromote: () => void;
  onContext: (context?: string) => void;
}

function StepRow({ id, step, canDrag, editing, promoting, knownContexts, onEdit, onRename, onRemove, onPromote, onContext }: StepProps) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({ id, disabled: !canDrag });
  const [draft, setDraft] = useState(step);
  const icon = "flex shrink-0 items-center px-2.5 text-slate-400 hover:text-(--accent)";

  return (
    <li ref={setNodeRef} style={{ transform: CSS.Translate.toString(transform), transition }} className={`relative ${isDragging ? "z-10" : ""}`}>
      <div className={`${card} ${isDragging ? "shadow-xl ring-(--accent)/40" : ""}`}>
        <div className="flex items-stretch">
          {canDrag && (
            <button
              type="button"
              ref={setActivatorNodeRef}
              {...attributes}
              {...listeners}
              aria-label={`Reorder: ${step}`}
              className="flex shrink-0 cursor-grab touch-none items-center pr-1 pl-2.5 text-slate-300 active:cursor-grabbing dark:text-slate-600"
            >
              <GripIcon className="size-5" />
            </button>
          )}
          {editing ? (
            <input
              className="min-w-0 flex-1 bg-transparent px-2 py-3 text-[15px] outline-none"
              value={draft}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => onRename(draft.replace(/\s+/g, " ").trim())}
              onKeyDown={(e) => {
                if (e.key === "Enter") e.currentTarget.blur();
                if (e.key === "Escape") {
                  // Leave Escape for this field only; don't also close the project.
                  e.stopPropagation();
                  setDraft(step);
                  onRename(step);
                }
              }}
            />
          ) : (
            <button type="button" onClick={onEdit} className={`min-w-0 flex-1 py-3 text-left text-[15px] leading-snug break-words ${canDrag ? "pl-1.5" : "pl-4"}`}>
              {step}
            </button>
          )}
          <button type="button" onClick={onPromote} aria-label={`Make "${step}" a next action`} aria-expanded={promoting} title="Make this a next action" className={`${icon} ${promoting ? "text-(--accent)" : ""}`}>
            <ArrowUpIcon className="size-5" />
          </button>
          <button type="button" onClick={onRemove} aria-label={`Delete step: ${step}`} title="Delete step" className={`${icon} pr-3.5 hover:text-rose-500`}>
            <TrashIcon className="size-[18px]" />
          </button>
        </div>
        {promoting && (
          <div className="border-t border-slate-100 px-3 py-3 dark:border-slate-700/60">
            <p className="mb-2 text-xs font-medium text-slate-500 dark:text-slate-400">Make it a next action. Where will you do it?</p>
            <div className="flex flex-wrap gap-2">
              {knownContexts.map((c) => (
                <button key={c} type="button" onClick={() => onContext(c)} className="rounded-full bg-white px-3 py-1.5 text-sm font-medium text-slate-600 ring-1 ring-slate-200 hover:ring-(--accent) dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700">
                  {c}
                </button>
              ))}
              <button type="button" onClick={() => onContext()} className="rounded-full px-3 py-1.5 text-sm font-medium text-slate-400">
                No context
              </button>
            </div>
          </div>
        )}
      </div>
    </li>
  );
}
