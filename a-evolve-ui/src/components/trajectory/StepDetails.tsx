import type { TrajectoryMessage } from "@/types/observation";

export function StepDetails({ step }: { step: TrajectoryMessage | null }) {
  if (!step) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3 text-sm text-slate-300">
        Select a step to inspect full content.
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-2 text-sm font-semibold">Step Details ({step.role})</div>
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs text-slate-200">
        {step.content}
      </pre>
    </div>
  );
}
