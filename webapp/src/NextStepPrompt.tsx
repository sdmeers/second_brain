import { useState } from "react";
import { CloseIcon } from "./icons";
import { store } from "./store";
import type { Project } from "./types";

/** After finishing a project's action: offer its roadmap, so the project is not left
 *  without a next action. The same question Telegram asks after /done. */
export function NextStepPrompt({ project, knownContexts, onClose }: { project: Project; knownContexts: string[]; onClose: () => void }) {
  const [step, setStep] = useState<string | null>(null);

  const promote = (context?: string) => {
    if (!step) return;
    store.send({ type: "step_promote", project: project.name, step, fields: { contexts: context ? [context] : [] } });
    onClose();
  };

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-36 z-30 flex justify-center px-4 sm:bottom-24">
      <div className="animate-rise pointer-events-auto w-full max-w-md rounded-2xl bg-white p-4 shadow-2xl ring-1 ring-slate-900/10 dark:bg-slate-800 dark:ring-white/10">
        <div className="mb-2.5 flex items-start justify-between gap-3">
          <p className="text-sm font-semibold">
            {step ? "Where will you do it?" : <>What&apos;s next for {project.name.replace(/_/g, " ")}?</>}
          </p>
          <button type="button" onClick={onClose} aria-label="Not now" className="-mt-1 -mr-1 rounded-full p-1 text-slate-400">
            <CloseIcon className="size-5" />
          </button>
        </div>
        {step ? (
          <div className="flex flex-wrap gap-2">
            {knownContexts.map((c) => (
              <button key={c} type="button" onClick={() => promote(c)} className="rounded-full px-3 py-1.5 text-sm font-medium text-slate-600 ring-1 ring-slate-200 hover:ring-(--accent) dark:text-slate-300 dark:ring-slate-600">
                {c}
              </button>
            ))}
            <button type="button" onClick={() => promote()} className="rounded-full px-3 py-1.5 text-sm font-medium text-slate-400">
              No context
            </button>
          </div>
        ) : (
          <ul className="space-y-1.5">
            {project.steps.slice(0, 4).map((s, i) => (
              <li key={`${i}:${s}`}>
                <button type="button" onClick={() => setStep(s)} className="w-full rounded-xl bg-slate-100 px-3 py-2.5 text-left text-sm hover:bg-(--accent-soft) dark:bg-slate-700/60">
                  {s}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
