import { useEffect, useRef, useState } from "react";
import { shortDate } from "./dates";
import { ChevronIcon, MicIcon } from "./icons";
import { store, useStore } from "./store";
import type { JournalDay, JournalDaySummary, JournalEntry } from "./types";

/** A journal can run long; the server takes up to 25 MB, about 20 minutes of speech. */
const MAX_SECONDS = 20 * 60;
const canRecord = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";
const QUESTIONS = ["What happened today?", "What did you promise anyone?", "What's on your mind?"];

const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const entries = (n: number) => (n === 1 ? "1 entry" : `${n} entries`);

/** Before 04:00 an entry still belongs to the evening before, as on the server. */
function journalDate(): string {
  const d = new Date(Date.now() - 4 * 3600_000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function JournalTab() {
  const state = useStore();
  // Re-read the list of days whenever an entry has just been saved.
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (state.journalMessage) setRefresh((n) => n + 1);
  }, [state.journalMessage]);

  return (
    <div className="space-y-8">
      <Tonight />
      <PastDays refresh={refresh} />
    </div>
  );
}

function Tonight() {
  const state = useStore();
  const [mode, setMode] = useState<"idle" | "recording" | "typing">("idle");
  const [seconds, setSeconds] = useState(0);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const keep = useRef(true);

  const date = state.journal?.date ?? journalDate();
  const recorded = !!state.journal && state.journal.date === date && state.journal.recorded;
  const queued = state.journalQueue.length;

  useEffect(() => {
    if (mode !== "recording") return;
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [mode]);

  useEffect(() => {
    if (mode === "recording" && seconds >= MAX_SECONDS) stop(true);
  }, [seconds, mode]);

  // Leaving the page mid-recording discards it rather than leaving the mic on.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => () => stop(false), []);

  const stop = (save: boolean) => {
    keep.current = save;
    if (recorder.current?.state === "recording") recorder.current.stop();
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
      setMode("idle");
      setSeconds(0);
      const length = (Date.now() - started) / 1000;
      if (keep.current && chunks.length && length > 1) {
        store.recordJournal(new Blob(chunks, { type: rec.mimeType || mimeType || "audio/webm" }), Math.round(length));
      }
    };
    recorder.current = rec;
    keep.current = true;
    rec.start(1000);
    setSeconds(0);
    setMode("recording");
  };

  const save = () => {
    store.writeJournal(text);
    setText("");
    setMode("idle");
  };

  const primary = "flex items-center justify-center rounded-xl bg-(--accent) font-semibold text-white disabled:opacity-40 dark:text-slate-950";

  return (
    <section>
      <div className="flex items-baseline justify-between gap-3 px-1">
        <h2 className="text-lg font-bold tracking-tight">Tonight&apos;s journal</h2>
        <span className="text-sm text-slate-500 dark:text-slate-400">
          {shortDate(date)}
          {recorded && <span className="ml-2 font-medium text-(--accent)">✓ Recorded</span>}
        </span>
      </div>

      <div className="mt-3 rounded-3xl bg-white p-5 shadow-sm ring-1 ring-slate-900/5 dark:bg-slate-900 dark:ring-white/10">
        <ul className="space-y-1.5 text-slate-600 dark:text-slate-300">
          {QUESTIONS.map((q) => (
            <li key={q} className="flex gap-2">
              <span className="text-(--accent)">•</span>
              {q}
            </li>
          ))}
        </ul>

        <div className="mt-5">
          {mode === "recording" ? (
            <div className="flex items-center gap-3">
              <div className="flex min-w-0 flex-1 items-center gap-3 rounded-xl border border-rose-200 bg-white px-3 py-3 dark:border-rose-900 dark:bg-slate-800" aria-live="polite">
                <span className="size-3 shrink-0 animate-pulse rounded-full bg-rose-500" />
                <span className="font-medium tabular-nums">{clock(seconds)}</span>
                <span className="truncate text-sm text-slate-400">
                  {seconds > MAX_SECONDS - 60 ? "Stopping at 20 minutes" : "Listening…"}
                </span>
              </div>
              <button type="button" onClick={() => stop(false)} className="shrink-0 px-2 py-3 text-sm font-medium text-slate-500">
                Cancel
              </button>
              <button type="button" onClick={() => stop(true)} className={`${primary} px-5 py-3`}>
                Done
              </button>
            </div>
          ) : mode === "typing" ? (
            <div>
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={6}
                autoFocus
                placeholder="Write your journal…"
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-3 text-base outline-none focus:border-(--accent) focus:ring-2 focus:ring-(--accent)/20 dark:border-slate-700 dark:bg-slate-800"
              />
              <div className="mt-2 flex justify-end gap-2">
                <button type="button" onClick={() => setMode("idle")} className="px-3 py-2.5 text-sm font-medium text-slate-500">
                  Cancel
                </button>
                <button type="button" onClick={save} disabled={!text.trim()} className={`${primary} px-5 py-2.5`}>
                  Save
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3">
              {canRecord ? (
                <button
                  type="button"
                  onClick={() => void record()}
                  className={`${primary} gap-2 px-6 py-3.5 text-base shadow-md shadow-slate-900/10`}
                >
                  <MicIcon className="size-5" />
                  {recorded ? "Record another entry" : "Record journal"}
                </button>
              ) : (
                <p className="text-sm text-slate-500">This browser can&apos;t record audio. You can type your entry instead.</p>
              )}
              <button type="button" onClick={() => setMode("typing")} className="text-sm font-medium text-slate-500 underline-offset-4 hover:underline dark:text-slate-400">
                Type instead
              </button>
            </div>
          )}
        </div>

        {error && <p className="mt-3 text-sm text-rose-600 dark:text-rose-400">{error}</p>}

        {queued > 0 && (
          <p className="mt-4 text-sm text-slate-500 dark:text-slate-400" aria-live="polite">
            {state.offline
              ? `${entries(queued)} kept on this phone until you're back online.`
              : `Saving ${entries(queued)}… transcribing can take a little while.`}
          </p>
        )}
        {state.journalMessage && (
          <div className="mt-4 flex items-start gap-3 rounded-2xl bg-(--accent-soft) px-4 py-3 text-sm text-(--accent-ink)">
            <p className="flex-1">{state.journalMessage}</p>
            <button type="button" onClick={() => store.dismissJournalMessage()} className="font-semibold">
              OK
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

function PastDays({ refresh }: { refresh: number }) {
  const [days, setDays] = useState<JournalDaySummary[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    store
      .journalDays()
      .then((r) => {
        if (!live) return;
        setDays(r.days);
        setFailed(false);
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [refresh]);

  return (
    <section>
      <h2 className="px-1 text-lg font-bold tracking-tight">Past entries</h2>
      {failed && !days ? (
        <p className="mt-3 px-1 text-sm text-slate-500 dark:text-slate-400">Past entries need a connection to your Second Brain.</p>
      ) : !days ? (
        <ul className="mt-3 space-y-2.5" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <li key={i} className="h-14 animate-pulse rounded-2xl bg-slate-200/70 dark:bg-slate-800/70" />
          ))}
        </ul>
      ) : days.length === 0 ? (
        <p className="mt-3 px-1 text-sm text-slate-500 dark:text-slate-400">No journal entries yet.</p>
      ) : (
        <ul className="mt-3 space-y-2.5">
          {days.map((d) => (
            <li key={d.date} className="rounded-2xl bg-white shadow-sm ring-1 ring-slate-900/5 dark:bg-slate-900 dark:ring-white/10">
              <button
                type="button"
                onClick={() => setOpen(open === d.date ? null : d.date)}
                aria-expanded={open === d.date}
                className="flex w-full items-center gap-3 px-4 py-3.5 text-left"
              >
                <ChevronIcon className={`size-4 shrink-0 text-slate-400 transition-transform ${open === d.date ? "rotate-90" : ""}`} />
                <span className="flex-1 font-medium">{shortDate(d.date)}</span>
                <span className="text-sm text-slate-400">{entries(d.entries)}</span>
              </button>
              {open === d.date && <DayDetail date={d.date} refresh={refresh} />}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function DayDetail({ date, refresh }: { date: string; refresh: number }) {
  const [day, setDay] = useState<JournalDay | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    store
      .journalDay(date)
      .then((d) => live && setDay(d))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [date, refresh]);

  if (failed && !day) return <p className="px-4 pb-4 text-sm text-slate-500">Couldn&apos;t load this day. Check your connection.</p>;
  if (!day) return <div className="mx-4 mb-4 h-20 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />;

  return (
    <div className="space-y-5 border-t border-slate-100 px-4 pt-4 pb-5 dark:border-slate-800">
      {day.notes && <p className="text-sm whitespace-pre-line text-slate-600 dark:text-slate-300">{day.notes}</p>}
      {day.entries.map((e, i) => (
        <Entry key={`${e.time}-${i}`} entry={e} />
      ))}
    </div>
  );
}

function Entry({ entry }: { entry: JournalEntry }) {
  return (
    <article>
      <p className="text-xs font-semibold tracking-wide text-slate-400 uppercase">
        {entry.time} · {entry.via}
      </p>
      {entry.summary && <p className="mt-1.5 whitespace-pre-line">{entry.summary}</p>}
      <Bullets title="Commitments" items={entry.commitments} />
      <Bullets title="On my mind" items={entry.on_my_mind} />
      {entry.transcript && (
        <details className="mt-3 group">
          <summary className="cursor-pointer text-sm font-medium text-slate-500 select-none dark:text-slate-400">Transcript</summary>
          <p className="mt-2 border-l-2 border-slate-200 pl-3 text-sm whitespace-pre-line text-slate-600 dark:border-slate-700 dark:text-slate-300">
            {entry.transcript}
          </p>
        </details>
      )}
      {entry.audio && <Recording name={entry.audio} />}
    </article>
  );
}

function Bullets({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-3">
      <p className="text-sm font-semibold text-slate-500 dark:text-slate-400">{title}</p>
      <ul className="mt-1 space-y-1">
        {items.map((item, i) => (
          <li key={i} className="flex gap-2 text-sm">
            <span className="text-(--accent)">•</span>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Fetched only when asked for, and held in memory, not on the phone's storage. */
function Recording({ name }: { name: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "failed">("idle");

  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);

  if (url) return <audio controls autoPlay src={url} className="mt-3 w-full" />;
  return (
    <button
      type="button"
      disabled={state === "loading"}
      onClick={() => {
        setState("loading");
        store
          .journalAudio(name)
          .then(setUrl)
          .catch(() => setState("failed"));
      }}
      className="mt-3 rounded-full bg-slate-100 px-3.5 py-1.5 text-sm font-medium text-slate-600 disabled:opacity-60 dark:bg-slate-800 dark:text-slate-300"
    >
      {state === "loading" ? "Loading…" : state === "failed" ? "Couldn't load · try again" : "▶ Play recording"}
    </button>
  );
}
