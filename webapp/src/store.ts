import { get, set } from "idb-keyval";
import { useSyncExternalStore } from "react";
import { applyOps, captureStamp, retarget, tidy } from "./optimistic";
import type {
  Category,
  ClarifyFields,
  EditFields,
  ListName,
  Lists,
  Op,
  OpResult,
  Outcome,
  Projects,
  VoiceNote,
} from "./types";

const UNDO_MS = 5000;
const POLL_MS = 30_000;
const TOKEN_KEY = "sb_actions_token";

export type SyncStatus = "synced" | "syncing" | "pending" | "offline";

export interface State {
  /** Last lists received from the server; null until the first load (or cache hit). */
  server: Lists | null;
  /** Active project names per area, for the pickers. */
  projects: Projects;
  /** Changes not yet acknowledged by the server, oldest first. Survives reloads. */
  outbox: Op[];
  /** Recordings not yet transcribed by the server, oldest first. Survives reloads. */
  voice: VoiceNote[];
  /** How many ops at the head of the outbox are in a request right now. */
  inFlight: number;
  busy: boolean;
  offline: boolean;
  needToken: boolean;
  /** One-line message about changes the server declined, shown until dismissed. */
  notice: string | null;
}

class NeedTokenError extends Error {}
class RejectedError extends Error {}

const newId = () =>
  typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

/** `notBefore` is the client's own Undo timer; the server has no use for it. */
const forWire = (op: Op) => {
  const copy = { ...op };
  delete copy.notBefore;
  return copy;
};

class ActionsStore {
  private state: State = {
    server: null,
    projects: { personal: [], professional: [] },
    outbox: [],
    voice: [],
    inFlight: 0,
    busy: false,
    offline: false,
    needToken: false,
    notice: null,
  };
  private listeners = new Set<() => void>();
  private wake: ReturnType<typeof setTimeout> | null = null;
  private voiceBusy = false;

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getState = () => this.state;

  private update(patch: Partial<State>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn());
    if (patch.outbox) void set("outbox", patch.outbox).catch(() => {});
    if (patch.server) void set("server", patch.server).catch(() => {});
    if (patch.projects) void set("projects", patch.projects).catch(() => {});
    if (patch.voice) void set("voice", patch.voice).catch(() => {});
  }

  async start() {
    // Show the last known lists (and any unsent changes) immediately, even offline.
    try {
      const [server, outbox, projects, voice] = await Promise.all([
        get<Lists>("server"),
        get<Op[]>("outbox"),
        get<Projects>("projects"),
        get<VoiceNote[]>("voice"),
      ]);
      this.update({
        server: this.state.server ?? server ?? null,
        outbox: [...(outbox ?? []), ...this.state.outbox],
        ...(projects ? { projects } : {}),
        voice: [...(voice ?? []), ...this.state.voice],
      });
    } catch {
      /* private mode or blocked storage: the app still works online */
    }
    window.addEventListener("online", () => void this.sync());
    window.addEventListener("focus", () => void this.sync());
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") void this.sync();
    });
    setInterval(() => void this.sync(), POLL_MS);
    void this.sync();
  }

  // --- Changes -----------------------------------------------------------------------

  private enqueue(op: Op) {
    this.update({ outbox: [...this.state.outbox, op] });
    void this.sync();
  }

  /** Returns the op id, which `undo` accepts for the next few seconds. */
  complete(list: ListName, raw: string): string {
    const op_id = newId();
    this.enqueue({ op_id, type: "complete", list, raw, notBefore: Date.now() + UNDO_MS });
    return op_id;
  }

  undo(op_id: string) {
    const at = this.state.outbox.findIndex((o) => o.op_id === op_id);
    if (at < this.state.inFlight) return; // already sent (or gone)
    this.update({ outbox: this.state.outbox.filter((o) => o.op_id !== op_id) });
  }

  edit(list: ListName, raw: string, fields: EditFields) {
    if (Object.keys(fields).length === 0) return;
    this.enqueue({ op_id: newId(), type: "edit", list, raw, fields });
  }

  move(list: ListName, raw: string, to: ListName, waiting_on?: string, follow_up?: string) {
    this.enqueue({ op_id: newId(), type: "move", list, raw, to, waiting_on, follow_up });
  }

  capture(text: string) {
    if (!tidy(text)) return;
    this.enqueue({ op_id: newId(), type: "capture", text: tidy(text), captured_at: captureStamp() });
  }

  /** Queue a recording. It is kept on the device until the server has transcribed it,
   *  so a note recorded with no signal is not lost. */
  captureVoice(blob: Blob) {
    this.update({ voice: [...this.state.voice, { id: newId(), blob, captured_at: captureStamp() }] });
    void this.syncVoice();
  }

  /** File a captured item. Trashing is held for a few seconds so it can be undone;
   *  the returned op id is what `undo` takes. */
  clarify(raw: string, outcome: Outcome, fields: ClarifyFields): string {
    const op_id = newId();
    this.enqueue({
      op_id,
      type: "clarify",
      list: "captured",
      raw,
      outcome,
      fields,
      ...(outcome === "trash" ? { notBefore: Date.now() + UNDO_MS } : {}),
    });
    return op_id;
  }

  reorder(list: ListName, category: Category, order: string[]) {
    const { outbox, inFlight } = this.state;
    const last = outbox[outbox.length - 1];
    const op: Op = { op_id: newId(), type: "reorder", list, category, order };
    // Several drags in a row only need the final order, unless the earlier one is
    // already on its way.
    if (last?.type === "reorder" && last.list === list && last.category === category && outbox.length > inFlight) {
      this.update({ outbox: [...outbox.slice(0, -1), op] });
      void this.sync();
    } else {
      this.enqueue(op);
    }
  }

  // --- Sync --------------------------------------------------------------------------

  setToken(token: string) {
    localStorage.setItem(TOKEN_KEY, token.trim());
    this.update({ needToken: false });
    void this.sync();
  }

  dismissNotice() {
    this.update({ notice: null });
  }

  private async request(
    path: string,
    body?: unknown,
  ): Promise<{ lists: Lists; projects?: Projects; results?: OpResult[] }> {
    const token = localStorage.getItem(TOKEN_KEY);
    const res = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
    if (res.status === 401) throw new NeedTokenError();
    if (res.status >= 400 && res.status < 500) throw new RejectedError(`${res.status}`);
    if (!res.ok) throw new Error(`Server error ${res.status}`);
    return res.json();
  }

  /** Send whatever is ready, or just refresh. Safe to call at any time. */
  async sync() {
    if (this.state.busy || this.state.needToken) return;

    // Ops are order-dependent, so send the run up to the first one still in its Undo
    // window and come back for the rest.
    const now = Date.now();
    const { outbox } = this.state;
    const held = outbox.findIndex((o) => (o.notBefore ?? 0) > now);
    const count = held < 0 ? outbox.length : held;
    const sent = outbox.slice(0, count);
    if (held >= 0) this.wakeAt(outbox[held].notBefore! - now + 50);

    this.update({ busy: true, inFlight: count });
    try {
      const body =
        count > 0
          ? await this.request("/api/actions/ops", { ops: sent.map(forWire) })
          : await this.request("/api/actions");
      const results = body.results ?? [];
      const skipped = results.filter((r) => r.status === "skipped").length;
      this.update({
        server: body.lists,
        ...(body.projects ? { projects: body.projects } : {}),
        outbox: retarget(this.state.outbox.slice(count), sent, results),
        offline: false,
        notice: skipped
          ? `${skipped} change${skipped > 1 ? "s" : ""} couldn't be applied — the action was changed elsewhere.`
          : this.state.notice,
      });
    } catch (err) {
      if (err instanceof NeedTokenError) {
        this.update({ needToken: true });
      } else if (err instanceof RejectedError) {
        // The server will never accept this batch; keeping it would block the queue.
        this.update({
          outbox: this.state.outbox.slice(count),
          notice: count ? "Some changes were rejected by the server and dropped." : this.state.notice,
        });
      } else {
        this.update({ offline: true });
        this.wakeAt(10_000);
      }
    } finally {
      this.update({ busy: false, inFlight: 0 });
    }
    // Anything queued while that request was out goes next.
    if (!this.state.offline && this.state.outbox.some((o) => (o.notBefore ?? 0) <= Date.now())) void this.sync();
    if (!this.state.offline) void this.syncVoice();
  }

  /** Upload queued recordings one at a time. Transcription takes a few seconds, so this
   *  runs alongside the ordinary sync rather than holding it up. */
  private async syncVoice() {
    if (this.voiceBusy || this.state.needToken) return;
    this.voiceBusy = true;
    try {
      while (this.state.voice.length > 0) {
        const note = this.state.voice[0];
        const drop = (notice?: string) =>
          this.update({
            voice: this.state.voice.filter((v) => v.id !== note.id),
            ...(notice ? { notice } : {}),
          });

        const form = new FormData();
        form.append("file", note.blob, "note");
        form.append("upload_id", note.id);
        form.append("captured_at", note.captured_at);
        const token = localStorage.getItem(TOKEN_KEY);
        let res: Response;
        try {
          res = await fetch("/api/actions/capture-audio", {
            method: "POST",
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            body: form,
          });
        } catch {
          this.update({ offline: true });
          this.wakeAt(10_000);
          return;
        }

        if (res.status === 401) {
          this.update({ needToken: true });
          return;
        }
        if (res.ok) {
          const body = await res.json();
          this.update({ server: body.lists, offline: false, ...(body.projects ? { projects: body.projects } : {}) });
          drop(body.status === "skipped" ? "Nothing could be heard in that recording, so it wasn't captured." : undefined);
        } else if (res.status === 409 || res.status >= 500) {
          // Still being transcribed from an earlier try, or the server/transcriber is
          // having a moment. Keep the recording and come back.
          this.wakeAt(15_000);
          return;
        } else {
          drop("A voice note couldn't be processed and was discarded.");
        }
      }
    } finally {
      this.voiceBusy = false;
    }
  }

  private wakeAt(ms: number) {
    if (this.wake) clearTimeout(this.wake);
    this.wake = setTimeout(() => void this.sync(), Math.max(ms, 0));
  }
}

export const store = new ActionsStore();

export function useStore() {
  const state = useSyncExternalStore(store.subscribe, store.getState);
  return state;
}

export function viewOf(state: State): Lists | null {
  if (!state.server) return null;
  const lists = applyOps(state.server, state.outbox);
  // Recordings still on the device get a stand-in so they are visibly not lost.
  for (const note of state.voice) {
    lists.captured.push({
      raw: `voice:${note.id}`,
      text: "Voice note",
      note: state.offline ? "Waiting to upload" : "Transcribing…",
      pending: true,
    });
  }
  return lists;
}

export const pendingCount = (state: State) => state.outbox.length + state.voice.length;

export function statusOf(state: State): SyncStatus {
  if (state.offline) return "offline";
  if (state.busy && state.inFlight > 0) return "syncing";
  if (state.outbox.length > 0 || state.voice.length > 0) return "pending";
  return "synced";
}
