import { Badge } from "@/components/ui/Badge";
import { StatusIndicator } from "@/components/ui/StatusIndicator";
import type { ErrorDetail, TrajectoryMessage } from "@/types/observation";

function hasError(step: TrajectoryMessage, errorDetails: ErrorDetail[] | undefined) {
  if (step.role !== "user") {
    return false;
  }
  if (!step.content.toLowerCase().includes("error")) {
    return false;
  }
  return (errorDetails?.length ?? 0) > 0;
}

export function StepCard({
  step,
  index,
  active,
  errorDetails,
  onSelect
}: {
  step: TrajectoryMessage;
  index: number;
  active: boolean;
  errorDetails?: ErrorDetail[];
  onSelect: () => void;
}) {
  const isError = hasError(step, errorDetails);
  const preview = step.content.replace(/\n/g, " ").slice(0, 160);

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full rounded-lg border p-3 text-left transition-colors duration-150 ${
        active
          ? "border-indigo-500 bg-indigo-500/10"
          : "border-slate-700 bg-slate-900/70 hover:border-slate-600 hover:bg-slate-800/70"
      }`}
    >
      <div className="mb-1 flex items-center justify-between">
        <div className="text-sm font-medium">
          Step {index + 1} - {step.role}
        </div>
        {isError ? (
          <StatusIndicator status="error" label="error" />
        ) : (
          <StatusIndicator status="success" label="ok" />
        )}
      </div>
      <div className="mb-2 text-xs text-slate-300">{preview}</div>
      <div className="flex items-center gap-2">
        <Badge tone="info">{step.role}</Badge>
        {step.role === "assistant" && (
          <Badge tone={step.content.includes("```python") ? "good" : "mid"}>
            {step.content.includes("```python") ? "code" : "text"}
          </Badge>
        )}
      </div>
    </button>
  );
}
