import { useEffect, useRef, useState } from "react";
import { CloseIcon } from "./icons";
import { store } from "./store";
import type { Category, ClarifyFields, Item, Outcome } from "./types";

const OUTCOMES: { id: Outcome; emoji: string; label: string; hint: string }[] = [
  { id: "next", emoji: "⚡", label: "Next", hint: "Something you can do" },
  { id: "waiting", emoji: "⏳", label: "Waiting", hint: "Someone else has it" },
  { id: "someday", emoji: "💡", label: "Someday", hint: "Maybe, not now" },
  { id: "project_step", emoji: "📌", label: "Project step", hint: "Add to a project's roadmap" },
  { id: "new_project", emoji: "📁", label: "New project", hint: "It takes more than one step" },
  { id: "done", emoji: "✓", label: "Done", hint: "Took two minutes; already done" },
  { id: "reference", emoji: "📚", label: "Reference", hint: "Keep it; nothing to do" },
  { id: "trash", emoji: "🗑", label: "Trash", hint: "Not needed" },
];

const label = "mb-1.5 block text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400";
const input =
  "w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-[15px] outline-none focus:border-(--accent) focus:ring-2 focus:ring-(--accent)/20 dark:border-slate-700 dark:bg-slate-800 dark:[color-scheme:dark]";

interface Props {
  item: Item;
  /** The area on screen; what the item is filed under unless changed here. */
  category: Category;
  /** Active project names, per area. */
  projects: Record<Category, string[]>;
  knownContexts: string[];
  onClose: () => void;
  onTrashed: (opId: string, text: string) => void;
}

/** One sheet that does what Telegram's /clarify does over several messages. */
export function ClarifySheet({ item, category: initialCategory, projects, knownContexts, onClose, onTrashed }: Props) {
  const [text, setText] = useState(item.text);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [category, setCategory] = useState<Category>(initialCategory);
  const [contexts, setContexts] = useState<string[]>([]);
  const [project, setProject] = useState("");
  const [waitingOn, setWaitingOn] = useState("");
  const [tickler, setTickler] = useState("");
  const [name, setName] = useState("");
  const [projectOutcome, setProjectOutcome] = useState("");
  const [firstAction, setFirstAction] = useState("");
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const el = textRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);

  const available = projects[category] ?? [];
  const wording = text.replace(/\s+/g, " ").trim();

  const choose = (o: Outcome) => {
    setOutcome(o);
    // A project is usually named after the thought that prompted it.
    if (o === "new_project" && !name) setName(wording);
  };

  const changeCategory = (c: Category) => {
    setCategory(c);
    setProject(""); // projects belong to one area
  };

  const ready =
    !!wording &&
    !!outcome &&
    (outcome !== "waiting" || !!waitingOn.trim()) &&
    (outcome !== "project_step" || !!project) &&
    (outcome !== "new_project" || (!!name.trim() && !!firstAction.trim()));

  const save = () => {
    if (!outcome || !ready) return;
    const fields: ClarifyFields = {};
    if (wording !== item.text) fields.text = wording;
    const filed = outcome === "next" || outcome === "waiting" || outcome === "someday";
    if (filed || outcome === "new_project") fields.category = category;
    if (outcome === "next" || outcome === "new_project") fields.contexts = contexts;
    if ((outcome === "next" || outcome === "waiting" || outcome === "project_step") && project) fields.project = project;
    if (outcome === "waiting") fields.waiting_on = waitingOn.trim();
    if ((outcome === "next" || outcome === "waiting") && tickler) fields.tickler = tickler;
    if (outcome === "new_project") {
      fields.name = name.trim();
      if (projectOutcome.trim()) fields.project_outcome = projectOutcome.trim();
      fields.first_action = firstAction.trim();
    }
    const opId = store.clarify(item.raw, outcome, fields);
    if (outcome === "trash") onTrashed(opId, item.text);
    onClose();
  };

  const contextChips = (
    <div>
      <span className={label}>Context</span>
      <div className="flex flex-wrap gap-2">
        {knownContexts.map((c) => {
          const on = contexts.includes(c);
          return (
            <button
              key={c}
              type="button"
              onClick={() => setContexts((cur) => (on ? cur.filter((x) => x !== c) : [...cur, c]))}
              aria-pressed={on}
              className={`rounded-full px-3 py-1.5 text-sm font-medium transition-colors ${
                on
                  ? "bg-(--accent) text-white dark:text-slate-950"
                  : "bg-white text-slate-600 ring-1 ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700"
              }`}
            >
              {c}
            </button>
          );
        })}
      </div>
    </div>
  );

  const projectPicker = (required: boolean) => (
    <label className="block">
      <span className={label}>Project{required ? "" : " (optional)"}</span>
      <select className={input} value={project} onChange={(e) => setProject(e.target.value)}>
        <option value="">{required ? "Choose a project…" : "None"}</option>
        {available.map((p) => (
          <option key={p} value={p}>
            {p.replace(/_/g, " ")}
          </option>
        ))}
      </select>
      {required && available.length === 0 && (
        <span className="mt-1.5 block text-xs text-slate-400">No {category} projects yet. Choose “New project” instead.</span>
      )}
    </label>
  );

  const dateField = (title: string) => (
    <label className="block">
      <span className="mb-1.5 flex items-center justify-between text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
        {title}
        {tickler && (
          <button type="button" onClick={() => setTickler("")} className="font-medium tracking-normal text-(--accent) normal-case">
            Clear
          </button>
        )}
      </span>
      <input type="date" className={input} value={tickler} onChange={(e) => setTickler(e.target.value)} />
    </label>
  );

  const showArea = outcome === "next" || outcome === "waiting" || outcome === "someday" || outcome === "new_project" || outcome === "project_step";

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center sm:items-center sm:p-6" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-slate-950/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="animate-sheet relative flex max-h-[92dvh] w-full flex-col rounded-t-3xl bg-slate-50 shadow-2xl sm:max-w-lg sm:rounded-3xl dark:bg-slate-900">
        <div className="flex items-center justify-between px-5 pt-4 pb-2">
          <h2 className="text-sm font-semibold text-slate-500 dark:text-slate-400">Clarify</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="-mr-2 rounded-full p-2 text-slate-400 hover:bg-slate-200/60 dark:hover:bg-slate-800">
            <CloseIcon className="size-5" />
          </button>
        </div>

        <div className="space-y-5 overflow-y-auto px-5 pb-4">
          <div>
            <textarea
              ref={textRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={1}
              aria-label="Captured item"
              className={`${input} resize-none overflow-hidden text-base leading-snug font-medium`}
            />
            {item.note && <p className="mt-1.5 text-xs text-slate-400">{item.note}</p>}
          </div>

          <div>
            <span className={label}>What is it?</span>
            <div className="grid grid-cols-4 gap-2">
              {OUTCOMES.map((o) => {
                const on = outcome === o.id;
                return (
                  <button
                    key={o.id}
                    type="button"
                    onClick={() => choose(o.id)}
                    aria-pressed={on}
                    title={o.hint}
                    className={`flex flex-col items-center gap-1 rounded-xl px-1 py-2.5 text-center text-xs leading-tight font-medium transition-colors ${
                      on
                        ? "bg-(--accent) text-white dark:text-slate-950"
                        : "bg-white text-slate-700 ring-1 ring-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-700"
                    }`}
                  >
                    <span aria-hidden className="text-lg leading-none">
                      {o.emoji}
                    </span>
                    {o.label}
                  </button>
                );
              })}
            </div>
            {outcome && <p className="mt-2 text-xs text-slate-400">{OUTCOMES.find((o) => o.id === outcome)!.hint}.</p>}
          </div>

          {showArea && (
            <div>
              <span className={label}>Area</span>
              <div className="grid grid-cols-2 gap-1 rounded-xl bg-slate-200/70 p-1 dark:bg-slate-800">
                {(["personal", "professional"] as Category[]).map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => changeCategory(c)}
                    aria-pressed={category === c}
                    className={`rounded-lg py-2 text-sm font-medium capitalize transition-colors ${
                      category === c ? "bg-white shadow-sm dark:bg-slate-600" : "text-slate-500 dark:text-slate-400"
                    }`}
                  >
                    {c}
                  </button>
                ))}
              </div>
            </div>
          )}

          {outcome === "next" && (
            <>
              {contextChips}
              <div className="grid grid-cols-2 gap-3">
                {projectPicker(false)}
                {dateField("Hide until")}
              </div>
            </>
          )}

          {outcome === "waiting" && (
            <>
              <label className="block">
                <span className={label}>Waiting on</span>
                <input className={input} value={waitingOn} onChange={(e) => setWaitingOn(e.target.value)} placeholder="Who has it?" autoFocus />
              </label>
              <div className="grid grid-cols-2 gap-3">
                {projectPicker(false)}
                {dateField("Follow up")}
              </div>
            </>
          )}

          {outcome === "project_step" && projectPicker(true)}

          {outcome === "new_project" && (
            <>
              <label className="block">
                <span className={label}>Project name</span>
                <input className={input} value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label className="block">
                <span className={label}>What does done look like? (optional)</span>
                <input className={input} value={projectOutcome} onChange={(e) => setProjectOutcome(e.target.value)} placeholder="The outcome you want" />
              </label>
              <label className="block">
                <span className={label}>First next action</span>
                <input className={input} value={firstAction} onChange={(e) => setFirstAction(e.target.value)} placeholder="The very next physical step" />
              </label>
              {contextChips}
            </>
          )}
        </div>

        <div className="flex gap-3 border-t border-slate-200 px-5 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))] dark:border-slate-800">
          <button type="button" onClick={onClose} className="flex-1 rounded-xl py-3 text-sm font-medium text-slate-500">
            Cancel
          </button>
          <button
            type="button"
            onClick={save}
            disabled={!ready}
            className="flex-1 rounded-xl bg-(--accent) py-3 text-sm font-semibold text-white disabled:opacity-40 dark:text-slate-950"
          >
            {outcome === "trash" ? "Trash it" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
