import { useEffect, useRef, useState } from "react";
import { CloseIcon } from "./icons";
import { store } from "./store";
import type { Category, EditFields, Item, ListName } from "./types";

const LIST_LABEL: Record<ListName, string> = {
  next: "Next",
  waiting: "Waiting For",
  someday: "Someday / Maybe",
  captured: "Captured",
};

const label = "mb-1.5 block text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400";
const input =
  "w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-[15px] outline-none focus:border-(--accent) focus:ring-2 focus:ring-(--accent)/20 dark:border-slate-700 dark:bg-slate-800 dark:[color-scheme:dark]";

interface Props {
  item: Item;
  list: ListName;
  knownContexts: string[];
  onClose: () => void;
}

export function EditSheet({ item, list, knownContexts, onClose }: Props) {
  const [text, setText] = useState(item.text);
  const [contexts, setContexts] = useState<string[]>(item.contexts ?? []);
  const [category, setCategory] = useState<Category>(item.category ?? "personal");
  const [due, setDue] = useState(item.due ?? "");
  const [tickler, setTickler] = useState(item.tickler ?? "");
  const [waitingOn, setWaitingOn] = useState(item.waiting_on ?? "");
  // Moving to Waiting For needs a name first, so that choice opens a small form.
  const [delegating, setDelegating] = useState(false);
  const [delegateTo, setDelegateTo] = useState("");
  const [followUp, setFollowUp] = useState("");
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

  const changes = (): EditFields => {
    const fields: EditFields = {};
    const clean = text.replace(/\s+/g, " ").trim();
    if (clean && clean !== item.text) fields.description = clean;
    if (contexts.join(" ") !== (item.contexts ?? []).join(" ")) fields.contexts = contexts;
    if (category !== (item.category ?? "personal")) fields.category = category;
    if (due !== (item.due ?? "")) fields.due = due || null;
    if (tickler !== (item.tickler ?? "")) fields.tickler = tickler || null;
    if (list === "waiting" && waitingOn.trim() !== (item.waiting_on ?? "")) fields.waiting_on = waitingOn.trim() || null;
    return fields;
  };

  const save = () => {
    store.edit(list, item.raw, changes());
    onClose();
  };

  const moveTo = (to: ListName) => {
    // Both ops name the line by the text we loaded; the server follows the edit.
    store.edit(list, item.raw, changes());
    store.move(list, item.raw, to, to === "waiting" ? delegateTo.trim() : undefined, followUp || undefined);
    onClose();
  };

  const toggleContext = (c: string) =>
    setContexts((cur) => (cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]));

  const destinations = (["next", "waiting", "someday"] as ListName[]).filter((l) => l !== list);

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center sm:items-center sm:p-6" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-slate-950/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="animate-sheet relative flex max-h-[92dvh] w-full flex-col rounded-t-3xl bg-slate-50 shadow-2xl sm:max-w-lg sm:rounded-3xl dark:bg-slate-900">
        <div className="flex items-center justify-between px-5 pt-4 pb-2">
          <h2 className="text-sm font-semibold text-slate-500 dark:text-slate-400">
            Edit · {LIST_LABEL[list]}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="-mr-2 rounded-full p-2 text-slate-400 hover:bg-slate-200/60 dark:hover:bg-slate-800">
            <CloseIcon className="size-5" />
          </button>
        </div>

        <div className="space-y-5 overflow-y-auto px-5 pb-4">
          <textarea
            ref={textRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={1}
            aria-label="Action"
            className={`${input} resize-none overflow-hidden text-base leading-snug font-medium`}
          />

          <>
              <div>
                <span className={label}>Context</span>
                <div className="flex flex-wrap gap-2">
                  {knownContexts.map((c) => {
                    const on = contexts.includes(c);
                    return (
                      <button
                        key={c}
                        type="button"
                        onClick={() => toggleContext(c)}
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

              {list === "waiting" && (
                <label className="block">
                  <span className={label}>Waiting on</span>
                  <input className={input} value={waitingOn} onChange={(e) => setWaitingOn(e.target.value)} placeholder="Name" />
                </label>
              )}

              <div className="grid grid-cols-2 gap-3">
                <DateField label="Due" value={due} onChange={setDue} />
                <DateField label={list === "waiting" ? "Follow up" : "Hide until"} value={tickler} onChange={setTickler} />
              </div>

              <div>
                <span className={label}>Area</span>
                <div className="grid grid-cols-2 gap-1 rounded-xl bg-slate-200/70 p-1 dark:bg-slate-800">
                  {(["personal", "professional"] as Category[]).map((c) => (
                    <button
                      key={c}
                      type="button"
                      onClick={() => setCategory(c)}
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

              <div>
                <span className={label}>Move to</span>
                {delegating ? (
                  <div className="space-y-3 rounded-2xl bg-white p-3 ring-1 ring-slate-200 dark:bg-slate-800 dark:ring-slate-700">
                    <input
                      className={input}
                      value={delegateTo}
                      onChange={(e) => setDelegateTo(e.target.value)}
                      placeholder="Who are you waiting on?"
                      autoFocus
                    />
                    <DateField label="Follow up on (optional)" value={followUp} onChange={setFollowUp} />
                    <div className="flex gap-2">
                      <button type="button" onClick={() => setDelegating(false)} className="flex-1 rounded-xl py-2.5 text-sm font-medium text-slate-500">
                        Back
                      </button>
                      <button
                        type="button"
                        disabled={!delegateTo.trim()}
                        onClick={() => moveTo("waiting")}
                        className="flex-1 rounded-xl bg-(--accent) py-2.5 text-sm font-semibold text-white disabled:opacity-40 dark:text-slate-950"
                      >
                        Move to Waiting For
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="grid grid-cols-2 gap-2">
                    {destinations.map((to) => (
                      <button
                        key={to}
                        type="button"
                        onClick={() => (to === "waiting" ? setDelegating(true) : moveTo(to))}
                        className="rounded-xl bg-white py-2.5 text-sm font-medium text-slate-700 ring-1 ring-slate-200 hover:ring-(--accent) dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-700"
                      >
                        {LIST_LABEL[to]}
                      </button>
                    ))}
                  </div>
                )}
              </div>
          </>
        </div>

        <div className="flex gap-3 border-t border-slate-200 px-5 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))] dark:border-slate-800">
          <button type="button" onClick={onClose} className="flex-1 rounded-xl py-3 text-sm font-medium text-slate-500">
            Cancel
          </button>
          <button
            type="button"
            onClick={save}
            disabled={!text.trim()}
            className="flex-1 rounded-xl bg-(--accent) py-3 text-sm font-semibold text-white disabled:opacity-40 dark:text-slate-950"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

function DateField({ label: text, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="block min-w-0">
      <span className="mb-1.5 flex items-center justify-between text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
        {text}
        {value && (
          <button type="button" onClick={() => onChange("")} className="font-medium tracking-normal text-(--accent) normal-case">
            Clear
          </button>
        )}
      </span>
      <input type="date" className={input} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}
