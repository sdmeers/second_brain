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
  );

export interface OpResult {
  op_id: string;
  status: "applied" | "skipped";
  reason?: string;
  new_raw?: string | null;
}
