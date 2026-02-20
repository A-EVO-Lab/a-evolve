import type { TrajectoryMessage } from "@/types/observation";

export function StepTimeline({ steps }: { steps: TrajectoryMessage[] }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-2">
      <div className="mb-2 text-xs font-semibold text-slate-300">Step Timeline</div>
      <div className="flex gap-1">
        {steps.map((step, idx) => (
          <div
            key={`${step.role}-${idx}`}
            className={`h-2 flex-1 rounded ${
              step.role === "assistant"
                ? "bg-indigo-400"
                : step.role === "tool"
                  ? "bg-orange-400"
                  : step.role === "user"
                    ? "bg-sky-400"
                    : "bg-slate-500"
            }`}
            title={`${idx + 1}: ${step.role}`}
          />
        ))}
      </div>
    </div>
  );
}
