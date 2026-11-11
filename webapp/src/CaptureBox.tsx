import { useEffect, useRef, useState, type FormEvent } from "react";
import { CloseIcon, PlusIcon } from "./icons";
import { store } from "./store";

/** The floating + and the one-field box it opens. Enter saves and stays open, so several
 *  thoughts can go in one after another. */
export function CaptureBox() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [saved, setSaved] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const close = () => {
    setOpen(false);
    setText("");
    setSaved(0);
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    store.capture(text);
    setText("");
    setSaved((n) => n + 1);
    inputRef.current?.focus();
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Capture"
        className="fixed right-4 bottom-[calc(4.75rem+env(safe-area-inset-bottom))] z-20 flex size-14 items-center justify-center rounded-full bg-(--accent) text-white shadow-lg shadow-slate-900/20 transition-transform active:scale-95 sm:right-[max(1.5rem,calc(50%-23rem))] sm:bottom-8 dark:text-slate-950"
      >
        <PlusIcon className="size-7" />
      </button>
    );
  }

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center sm:items-start sm:p-6 sm:pt-28" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-slate-950/40 backdrop-blur-[2px]" onClick={close} />
      <form
        onSubmit={submit}
        className="animate-sheet relative w-full rounded-t-3xl bg-slate-50 px-5 pt-4 pb-[max(1rem,env(safe-area-inset-bottom))] shadow-2xl sm:max-w-lg sm:rounded-3xl sm:pb-5 dark:bg-slate-900"
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-500 dark:text-slate-400">
            Capture
            {saved > 0 && <span className="ml-2 font-medium text-(--accent)">✓ {saved} added</span>}
          </h2>
          <button type="button" onClick={close} aria-label="Close" className="-mr-2 rounded-full p-2 text-slate-400 hover:bg-slate-200/60 dark:hover:bg-slate-800">
            <CloseIcon className="size-5" />
          </button>
        </div>
        <div className="flex gap-2">
          <input
            ref={inputRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="What's on your mind?"
            enterKeyHint="done"
            className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3 py-3 text-base outline-none focus:border-(--accent) focus:ring-2 focus:ring-(--accent)/20 dark:border-slate-700 dark:bg-slate-800"
          />
          <button
            type="submit"
            disabled={!text.trim()}
            className="rounded-xl bg-(--accent) px-5 font-semibold text-white disabled:opacity-40 dark:text-slate-950"
          >
            Add
          </button>
        </div>
        <p className="mt-2.5 text-xs text-slate-400">Goes to Captured. Sort it out later, when you clarify.</p>
      </form>
    </div>
  );
}
