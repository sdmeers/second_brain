import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useState } from "react";
import { describeDue, shortDate, type DueTone } from "./dates";
import { BellIcon, CalendarIcon, CheckIcon, GripIcon, HourglassIcon } from "./icons";
import type { Item } from "./types";

const dueTones: Record<DueTone, string> = {
  overdue: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
  today: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  soon: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  later: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
};

const chip = "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium";

interface Props {
  id: string;
  item: Item;
  /** Captured items and deferred ones can't be ticked off or dragged. */
  canComplete: boolean;
  canDrag: boolean;
  onOpen: () => void;
  onComplete: () => void;
}

export function ActionCard({ id, item, canComplete, canDrag, onOpen, onComplete }: Props) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({
    id,
    disabled: !canDrag,
  });
  const [leaving, setLeaving] = useState(false);
  const due = item.due ? describeDue(item.due) : null;

  const complete = () => {
    if (leaving) return;
    setLeaving(true);
    // Let the tick land before the card goes.
    setTimeout(onComplete, 320);
  };

  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className={`relative ${isDragging ? "z-10" : ""}`}
    >
      <div
        className={`flex items-stretch rounded-2xl bg-white ring-1 ring-slate-900/5 transition-all duration-300 dark:bg-slate-900 dark:ring-white/10 ${
          isDragging ? "scale-[1.02] shadow-xl ring-(--accent)/40" : "shadow-sm"
        } ${leaving ? "translate-x-4 opacity-0" : ""}`}
      >
        {canComplete && (
          <button
            type="button"
            onClick={complete}
            aria-label={`Complete: ${item.text}`}
            className="group flex shrink-0 items-start py-3.5 pr-1 pl-3.5"
          >
            <span
              className={`flex size-6 items-center justify-center rounded-full border-2 transition-colors ${
                leaving
                  ? "animate-pop border-(--accent) bg-(--accent) text-white dark:text-slate-950"
                  : "border-slate-300 text-transparent group-hover:border-(--accent) group-hover:text-(--accent)/50 dark:border-slate-600"
              }`}
            >
              <CheckIcon className="size-3.5" />
            </span>
          </button>
        )}

        <button
          type="button"
          onClick={onOpen}
          className={`min-w-0 flex-1 py-3.5 text-left ${canComplete ? "pl-2" : "pl-4"} ${canDrag ? "pr-1" : "pr-4"}`}
        >
          <p className={`text-[15px] leading-snug break-words ${leaving ? "text-slate-400 line-through" : ""}`}>
            {item.text}
          </p>
          {(due || item.waiting_on || item.tickler || item.project || item.contexts?.length || item.note) && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {due && (
                <span className={`${chip} ${dueTones[due.tone]}`}>
                  <CalendarIcon className="size-3" />
                  {due.label}
                </span>
              )}
              {item.waiting_on && (
                <span className={`${chip} bg-(--accent-soft) text-(--accent-ink)`}>
                  <HourglassIcon className="size-3" />
                  {item.waiting_on}
                </span>
              )}
              {item.tickler && (
                <span className={`${chip} bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400`}>
                  <BellIcon className="size-3" />
                  {shortDate(item.tickler)}
                </span>
              )}
              {item.project && (
                <span className={`${chip} bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300`}>
                  {item.project.replace(/_/g, " ")}
                </span>
              )}
              {item.contexts?.map((c) => (
                <span key={c} className={`${chip} text-slate-500 ring-1 ring-slate-200 dark:text-slate-400 dark:ring-slate-700`}>
                  {c}
                </span>
              ))}
              {item.note && <span className="text-xs text-slate-400 dark:text-slate-500">{item.note}</span>}
            </div>
          )}
        </button>

        {canDrag && (
          <button
            type="button"
            ref={setActivatorNodeRef}
            {...attributes}
            {...listeners}
            aria-label={`Reorder: ${item.text}`}
            className="flex shrink-0 cursor-grab touch-none items-center rounded-r-2xl px-3 text-slate-300 hover:text-slate-500 active:cursor-grabbing dark:text-slate-600 dark:hover:text-slate-400"
          >
            <GripIcon className="size-5" />
          </button>
        )}
      </div>
    </li>
  );
}
