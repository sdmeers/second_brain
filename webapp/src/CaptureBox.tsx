import { useEffect, useRef, useState, type FormEvent } from "react";
import { CloseIcon, MicIcon, PlusIcon } from "./icons";
import { store } from "./store";

const MAX_SECONDS = 300;
const canRecord = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";

const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/** The floating + and the box it opens: type a thought, or tap the mic and say it.
 *  Enter saves and stays open, so several thoughts can go in one after another. */
export function CaptureBox() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [saved, setSaved] = useState(0);
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const keep = useRef(true);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!recording) return;
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [recording]);

  useEffect(() => {
    if (recording && seconds >= MAX_SECONDS) stop(true);
  }, [seconds, recording]);

  const stop = (save: boolean) => {
    keep.current = save;
    if (recorder.current?.state === "recording") recorder.current.stop();
  };

  const close = () => {
    stop(false);
    setOpen(false);
    setText("");
    setSaved(0);
    setError(null);
  };

  const record = async () => {
    setError(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("Microphone access is blocked. Allow it for this site in your browser settings.");
      return;
    }
    // Chrome and Android record WebM/Opus; Safari and iPhone record MP4. The server takes either.
    const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported(t));
    const rec = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks: Blob[] = [];
    const started = Date.now();
    rec.ondataavailable = (e) => {
      if (e.data.size) chunks.push(e.data);
    };
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      recorder.current = null;
      setRecording(false);
      setSeconds(0);
      // A tap-and-release by accident isn't a note.
      if (keep.current && chunks.length && Date.now() - started > 600) {
        store.captureVoice(new Blob(chunks, { type: rec.mimeType || mimeType || "audio/webm" }));
        setSaved((n) => n + 1);
      }
    };
    recorder.current = rec;
    keep.current = true;
    rec.start();
    setRecording(true);
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

  const primary = "flex shrink-0 items-center justify-center rounded-xl bg-(--accent) font-semibold text-white disabled:opacity-40 dark:text-slate-950";

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

        {recording ? (
          <div className="flex items-center gap-3">
            <div className="flex min-w-0 flex-1 items-center gap-3 rounded-xl border border-rose-200 bg-white px-3 py-3 dark:border-rose-900 dark:bg-slate-800" aria-live="polite">
              <span className="size-3 shrink-0 animate-pulse rounded-full bg-rose-500" />
              <span className="font-medium tabular-nums">{clock(seconds)}</span>
              <span className="truncate text-sm text-slate-400">Listening…</span>
            </div>
            <button type="button" onClick={() => stop(false)} className="shrink-0 px-2 py-3 text-sm font-medium text-slate-500">
              Cancel
            </button>
            <button type="button" onClick={() => stop(true)} className={`${primary} px-5 py-3`}>
              Done
            </button>
          </div>
        ) : (
          <div className="flex gap-2">
            <input
              ref={inputRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="What's on your mind?"
              enterKeyHint="done"
              className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3 py-3 text-base outline-none focus:border-(--accent) focus:ring-2 focus:ring-(--accent)/20 dark:border-slate-700 dark:bg-slate-800"
            />
            {/* Like a messaging app: a mic until you start typing, then Add. */}
            {text.trim() || !canRecord ? (
              <button type="submit" disabled={!text.trim()} className={`${primary} px-5`}>
                Add
              </button>
            ) : (
              <button type="button" onClick={() => void record()} aria-label="Record a voice note" className={`${primary} w-14`}>
                <MicIcon className="size-6" />
              </button>
            )}
          </div>
        )}

        <p className={`mt-2.5 text-xs ${error ? "text-rose-600 dark:text-rose-400" : "text-slate-400"}`}>
          {error ?? "Goes to Captured. Sort it out later, when you clarify."}
        </p>
      </form>
    </div>
  );
}
