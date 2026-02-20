import { useMemo, useState } from "react";
import { StepCard } from "@/components/trajectory/StepCard";
import { StepDetails } from "@/components/trajectory/StepDetails";
import { StepTimeline } from "@/components/trajectory/StepTimeline";
import type { AppWorldObservation } from "@/types/observation";

export function TrajectoryViewer({
  observation,
  evaluationReport
}: {
  observation: AppWorldObservation | null;
  evaluationReport?: string | null;
}) {
  const [selectedStepIndex, setSelectedStepIndex] = useState(0);
  const steps = observation?.trajectory ?? [];
  const selectedStep = steps[selectedStepIndex] ?? null;

  const stepStats = useMemo(
    () => ({
      user: steps.filter((s) => s.role === "user").length,
      assistant: steps.filter((s) => s.role === "assistant").length,
      tool: steps.filter((s) => s.role === "tool").length
    }),
    [steps]
  );

  if (!observation) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3 text-sm text-slate-300">
        Select a task from the batch list to inspect trajectory.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3 text-xs text-slate-300">
        steps: {steps.length} | user: {stepStats.user} | assistant: {stepStats.assistant}
        {" | "}tool: {stepStats.tool}
      </div>
      <StepTimeline steps={steps} />
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <div className="max-h-[440px] space-y-2 overflow-auto pr-1">
          {steps.map((step, index) => (
            <StepCard
              key={`${step.role}-${index}`}
              step={step}
              index={index}
              active={selectedStepIndex === index}
              errorDetails={observation.error_details}
              onSelect={() => setSelectedStepIndex(index)}
            />
          ))}
        </div>
        <div className="space-y-3">
          <StepDetails step={selectedStep} />
          <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
            <div className="mb-2 text-sm font-semibold">Integration Test Report</div>
            <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs text-slate-200">
              {evaluationReport ?? "No evaluation report found for this task."}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
}
