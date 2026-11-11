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
  /** Starred on Next: one of the few to work on next (a `#focus` tag in the vault). */
  focus?: boolean;
  /** Just filed from Captured and not yet confirmed by the server, so it has no real
   *  line to address yet: shown, but not editable until the next sync. */
  pending?: boolean;
}

export interface Project {
  /** The note's filename. It is the project's identity. */
  name: string;
  category: Category;
  outcome: string;
  due: string | null;
  /** Roadmap steps, in order. A step is identified by its exact text. */
  steps: string[];
}

export interface ArchivedProject {
  name: string;
  category: Category;
  outcome: string;
  completed: string | null;
}

export interface Projects {
  active: Project[];
  archived: ArchivedProject[];
}

export interface ProjectFields {
  name?: string;
  category?: Category;
  outcome?: string;
  due_date?: string | null;
  contexts?: string[];
}

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
  focus?: boolean;
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
    | { type: "project_create"; fields: ProjectFields }
    | { type: "project_update"; project: string; fields: ProjectFields }
    | { type: "project_archive"; project: string }
    | { type: "project_restore"; project: string; category: Category }
    | { type: "step_add"; project: string; text: string }
    | { type: "step_edit"; project: string; step: string; text: string }
    | { type: "step_remove"; project: string; step: string }
    | { type: "step_reorder"; project: string; order: string[] }
    | { type: "step_promote"; project: string; step: string; fields: ProjectFields }
  );

/** The ops that name an action line by its text. */
export type LineOp = Extract<Op, { raw: string }>;

/** An op as the UI hands it to the store, before it is given an id. */
export type NewOp = Op extends infer O ? (O extends Op ? Omit<O, "op_id" | "notBefore"> : never) : never;

/** A recording waiting to be uploaded and transcribed. */
export interface VoiceNote {
  id: string;
  blob: Blob;
  captured_at: string;
}

export interface OpResult {
  op_id: string;
  status: "applied" | "skipped";
  reason?: string;
  new_raw?: string | null;
  /** On a completed action: the project it belonged to, as a tag. */
  project?: string | null;
}
