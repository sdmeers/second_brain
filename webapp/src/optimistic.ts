import type { Item, LineOp, Lists, Op, OpResult, Project, Projects } from "./types";

const pad = (n: number) => String(n).padStart(2, "0");

export const todayISO = () => {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

/** Local time as `YYYY-MM-DD HH:MM`, the stamp written into a captured line. */
export const captureStamp = (d = new Date()) => `${todayISO()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;

export const tidy = (text: string) => text.replace(/\s+/g, " ").trim();

/** How a project name and a `#project/Tag` are compared (as `norm` in backend/vault/projects.py). */
export const projectKey = (name: string | null | undefined) => (name ?? "").trim().replace(/ /g, "_").toLowerCase();

/**
 * The exact line the server writes for a capture (`captured_line` in
 * backend/vault/clarify_ops.py; keep the two in step). Knowing it here is what lets an
 * item captured offline be clarified before it has ever reached the server.
 */
export const capturedLine = (text: string, stamp: string) => `- ${tidy(text)} (Captured on ${stamp} via GTD app)`;

const hasRaw = (op: Op): op is LineOp => "raw" in op;

/** Reorder `current` to follow `order`: names it doesn't know are ignored, and items it
 *  doesn't mention keep their relative order after the ones it placed. */
function reordered<T>(current: T[], order: string[], key: (t: T) => string): T[] {
  const rest = [...current];
  const placed: T[] = [];
  for (const k of order) {
    const at = rest.findIndex((t) => key(t) === k);
    if (at >= 0) placed.push(...rest.splice(at, 1));
  }
  return [...placed, ...rest];
}

export interface View {
  lists: Lists;
  projects: Projects;
}

/**
 * What everything looks like once the queued changes land: the last state from the
 * server with the outbox replayed on top. Mirrors the backend closely enough to draw
 * the screen; the server's answer always replaces it.
 *
 * Items keep their original `raw` here even after an edit, because `raw` is how every
 * later queued op refers to them.
 */
export function applyOps(server: View, ops: Op[]): View {
  const lists: Lists = {
    next: [...server.lists.next],
    waiting: [...server.lists.waiting],
    someday: [...server.lists.someday],
    captured: [...server.lists.captured],
  };
  let active: Project[] = server.projects.active.map((p) => ({ ...p, steps: [...p.steps] }));
  let archived = [...server.projects.archived];
  const today = todayISO();
  const project = (name: string) => active.find((p) => p.name === name);
  const filed = (op: Op, item: Partial<Item>): Item => ({
    raw: `pending:${op.op_id}`,
    text: "",
    category: "personal",
    contexts: [],
    pending: true,
    ...item,
  });

  for (const op of ops) {
    switch (op.type) {
      case "capture": {
        const raw = capturedLine(op.text, op.captured_at);
        if (!lists.captured.some((i) => i.raw === raw)) {
          lists.captured.push({ raw, text: tidy(op.text), note: `Captured on ${op.captured_at} via GTD app` });
        }
        break;
      }
      case "clarify": {
        const at = lists.captured.findIndex((i) => i.raw === op.raw);
        if (at < 0) break;
        const [item] = lists.captured.splice(at, 1);
        const f = op.fields;
        const text = f.text ?? item.text;
        const tag = f.project?.replace(/ /g, "_");
        const base = { text, category: f.category ?? "personal" };
        if (op.outcome === "next") {
          const tickler = f.tickler ?? null;
          lists.next.push(filed(op, { ...base, contexts: f.contexts ?? [], project: tag, tickler, deferred: !!tickler && tickler > today }));
        } else if (op.outcome === "waiting") {
          lists.waiting.push(filed(op, { ...base, waiting_on: f.waiting_on, project: tag, tickler: f.tickler ?? null }));
        } else if (op.outcome === "someday") {
          lists.someday.push(filed(op, base));
        } else if (op.outcome === "project_step" && f.project) {
          project(f.project)?.steps.push(text);
        } else if (op.outcome === "new_project" && f.name) {
          const name = tidy(f.name);
          if (!project(name)) active.push({ name, category: base.category, outcome: f.project_outcome ?? "", due: null, steps: [] });
          if (f.first_action) {
            lists.next.push(filed(op, { ...base, text: tidy(f.first_action), contexts: f.contexts ?? [], project: name.replace(/ /g, "_") }));
          }
        }
        break;
      }
      case "reorder": {
        const mine = lists[op.list].filter((i) => i.category === op.category);
        const others = lists[op.list].filter((i) => i.category !== op.category);
        lists[op.list] = [...reordered(mine, op.order, (i) => i.raw), ...others];
        break;
      }
      case "complete":
      case "edit":
      case "move": {
        const items = lists[op.list];
        const at = items.findIndex((i) => i.raw === op.raw);
        if (at < 0) break;
        const item = items[at];
        if (op.type === "complete") {
          items.splice(at, 1);
        } else if (op.type === "edit") {
          const f = op.fields;
          const next = { ...item };
          if (f.description !== undefined) next.text = f.description;
          if (f.contexts !== undefined) next.contexts = f.contexts.map((c) => (c.startsWith("@") ? c : `@${c}`));
          if (f.waiting_on !== undefined) next.waiting_on = f.waiting_on;
          if (f.due !== undefined) next.due = f.due;
          if (f.tickler !== undefined) next.tickler = f.tickler;
          if (f.focus !== undefined) next.focus = f.focus;
          next.deferred = !!next.tickler && next.tickler > today;
          if (f.category !== undefined && f.category !== item.category) {
            next.category = f.category;
            items.splice(at, 1);
            items.unshift(next);
          } else {
            items[at] = next;
          }
        } else {
          const moved = { ...item, focus: false };
          if (op.to === "waiting") {
            moved.waiting_on = op.waiting_on || null;
            if (op.follow_up) moved.tickler = op.follow_up;
          } else if (op.list === "waiting") {
            moved.waiting_on = null;
          }
          moved.deferred = !!moved.tickler && moved.tickler > today;
          items.splice(at, 1);
          lists[op.to].unshift(moved);
        }
        break;
      }
      case "project_create": {
        const name = tidy(op.fields.name ?? "");
        if (name && !active.some((p) => projectKey(p.name) === projectKey(name))) {
          active.push({ name, category: op.fields.category ?? "personal", outcome: op.fields.outcome ?? "", due: op.fields.due_date || null, steps: [] });
        }
        break;
      }
      case "project_update": {
        const p = project(op.project);
        if (!p) break;
        if (op.fields.outcome !== undefined) p.outcome = op.fields.outcome;
        if (op.fields.due_date !== undefined) p.due = op.fields.due_date || null;
        break;
      }
      case "project_archive": {
        const p = project(op.project);
        if (!p) break;
        active = active.filter((x) => x !== p);
        archived = [{ name: p.name, category: p.category, outcome: p.outcome, completed: today }, ...archived];
        break;
      }
      case "project_restore": {
        const a = archived.find((x) => x.name === op.project && x.category === op.category);
        if (!a) break;
        archived = archived.filter((x) => x !== a);
        active.push({ name: a.name, category: a.category, outcome: a.outcome, due: null, steps: [] });
        break;
      }
      case "step_add":
        project(op.project)?.steps.push(tidy(op.text));
        break;
      case "step_edit": {
        const p = project(op.project);
        const at = p?.steps.indexOf(op.step) ?? -1;
        if (p && at >= 0) p.steps[at] = tidy(op.text);
        break;
      }
      case "step_remove":
      case "step_promote": {
        const p = project(op.project);
        const at = p?.steps.indexOf(op.step) ?? -1;
        if (!p || at < 0) break;
        p.steps.splice(at, 1);
        if (op.type === "step_promote") {
          lists.next.push(filed(op, { text: op.step, category: p.category, contexts: op.fields.contexts ?? [], project: p.name.replace(/ /g, "_") }));
        }
        break;
      }
      case "step_reorder": {
        const p = project(op.project);
        if (p) p.steps = reordered(p.steps, op.order, (s) => s);
        break;
      }
    }
  }
  return { lists, projects: { active, archived } };
}

/**
 * After a batch is acknowledged, ops queued while it was in flight still name lines by
 * their old text. Point them at the text the server just wrote.
 */
export function retarget(ops: Op[], sent: Op[], results: OpResult[]): Op[] {
  const renamed = new Map<string, string>();
  sent.forEach((op, i) => {
    const newRaw = results[i]?.new_raw;
    if (hasRaw(op) && newRaw) renamed.set(op.raw, newRaw);
  });
  if (renamed.size === 0) return ops;
  const fix = (raw: string) => renamed.get(raw) ?? raw;
  return ops.map((op) => {
    if (op.type === "reorder") return { ...op, order: op.order.map(fix) };
    return hasRaw(op) ? { ...op, raw: fix(op.raw) } : op;
  });
}
