import type { Lists, Op, OpResult } from "./types";

export const todayISO = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

/**
 * What the lists look like once the queued changes land: the last state from the
 * server with the outbox replayed on top. Mirrors backend/vault/actions.py closely
 * enough to draw the screen; the server's answer always replaces it.
 *
 * Items keep their original `raw` here even after an edit, because `raw` is how every
 * later queued op refers to them.
 */
export function applyOps(server: Lists, ops: Op[]): Lists {
  const lists: Lists = {
    next: [...server.next],
    waiting: [...server.waiting],
    someday: [...server.someday],
    captured: [...server.captured],
  };
  const today = todayISO();

  for (const op of ops) {
    const items = lists[op.list];
    if (op.type === "reorder") {
      const mine = items.filter((i) => i.category === op.category);
      const others = items.filter((i) => i.category !== op.category);
      const placed = [];
      for (const raw of op.order) {
        const at = mine.findIndex((i) => i.raw === raw);
        if (at >= 0) placed.push(...mine.splice(at, 1));
      }
      lists[op.list] = [...placed, ...mine, ...others];
      continue;
    }

    const at = items.findIndex((i) => i.raw === op.raw);
    if (at < 0) continue;
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
      next.deferred = !!next.tickler && next.tickler > today;
      if (f.category !== undefined && f.category !== item.category) {
        next.category = f.category;
        items.splice(at, 1);
        items.unshift(next);
      } else {
        items[at] = next;
      }
    } else {
      const moved = { ...item };
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
  }
  return lists;
}

/**
 * After a batch is acknowledged, ops queued while it was in flight still name lines by
 * their old text. Point them at the text the server just wrote.
 */
export function retarget(ops: Op[], sent: Op[], results: OpResult[]): Op[] {
  const renamed = new Map<string, string>();
  sent.forEach((op, i) => {
    const newRaw = results[i]?.new_raw;
    if (op.type !== "reorder" && newRaw) renamed.set(op.raw, newRaw);
  });
  if (renamed.size === 0) return ops;
  const fix = (raw: string) => renamed.get(raw) ?? raw;
  return ops.map((op) =>
    op.type === "reorder" ? { ...op, order: op.order.map(fix) } : { ...op, raw: fix(op.raw) },
  );
}
