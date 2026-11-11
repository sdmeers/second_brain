export type ListName = "next" | "waiting" | "someday" | "captured";
export type Category = "personal" | "professional";

export interface Item {
  /** The exact Markdown line. It is the item's identity: the vault has no IDs. */
  raw: string;
  text: string;
  note?: string | null;
  category?: Category;
  contexts?: string[];
  project?: string | null;
  waiting_on?: string | null;
  due?: string | null;
  tickler?: string | null;
  deferred?: boolean;
  /** Just filed from Captured and not yet confirmed by the server, so it has no real
   *  line to address yet: shown, but not editable until the next sync. */
  pending?: boolean;
}

export type Projects = Record<Category, string[]>;

export type Outcome = "next" | "waiting" | "someday" | "project_step" | "new_project" | "done" | "reference" | "trash";

/** What the clarify sheet collected; only the keys the chosen outcome needs are set. */
export interface ClarifyFields {
  text?: string;
  category?: Category;
  contexts?: string[];
  project?: string;
  waiting_on?: string;
  tickler?: string;
  name?: string;
  project_outcome?: string;
  first_action?: string;
}

export type Lists = Record<ListName, Item[]>;

export interface EditFields {
  description?: string;
  contexts?: string[];
  category?: Category;
  waiting_on?: string | null;
  due?: string | null;
  tickler?: string | null;
}

interface OpBase {
  op_id: string;
  /** Client-only: do not send before this time (the Undo window for a completion). */
  notBefore?: number;
}

export type Op = OpBase &
  (
    | { type: "complete"; list: ListName; raw: string }
    | { type: "edit"; list: ListName; raw: string; fields: EditFields }
    | { type: "move"; list: ListName; raw: string; to: ListName; waiting_on?: string; follow_up?: string }
    | { type: "reorder"; list: ListName; category: Category; order: string[] }
    | { type: "capture"; text: string; captured_at: string }
    | { type: "clarify"; list: "captured"; raw: string; outcome: Outcome; fields: ClarifyFields }
  );

export interface OpResult {
  op_id: string;
  status: "applied" | "skipped";
  reason?: string;
  new_raw?: string | null;
}
