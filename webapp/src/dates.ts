import { todayISO } from "./optimistic";

const parse = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
};

export function shortDate(iso: string): string {
  const date = parse(iso);
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

export type DueTone = "overdue" | "today" | "soon" | "later";

export function describeDue(iso: string): { label: string; tone: DueTone } {
  const days = Math.round((parse(iso).getTime() - parse(todayISO()).getTime()) / 86_400_000);
  if (days < 0) return { label: `Overdue · ${shortDate(iso)}`, tone: "overdue" };
  if (days === 0) return { label: "Today", tone: "today" };
  if (days === 1) return { label: "Tomorrow", tone: "soon" };
  return { label: shortDate(iso), tone: days <= 7 ? "soon" : "later" };
}
