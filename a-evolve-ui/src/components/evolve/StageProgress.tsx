import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import type { ProposalCycle, StageHistoryItem, StageStatus } from "@/types/proposal";

const STAGES = [
  { key: "diagnosis", label: "Diagnosis" },
  { key: "plan", label: "Plan" },
  { key: "apply", label: "Apply" },
  { key: "verify", label: "Verify" }
] as const;

function getStageItems(
  proposal: ProposalCycle | undefined,
  stageName: string
): StageHistoryItem[] {
  if (!proposal) {
    return [];
  }
  if (stageName === "verify") {
    return proposal.stage_history.filter(
      (item) => item.name === "verify" || item.name.startsWith("repair_")
    );
  }
  return proposal.stage_history.filter((item) => item.name === stageName);
}

function getStageStatus(
  stageName: string,
  proposal: ProposalCycle | undefined,
  activeStage: string | null,
  doneStages?: string[]
): StageStatus {
  if (!proposal) {
    if (activeStage === stageName) {
      return "active";
    }
    // If this stage was previously active, it's now completed
    if (doneStages?.includes(stageName)) {
      return "completed";
    }
    return "pending";
  }
  if (activeStage === stageName) {
    return "active";
  }
  const stageItems = getStageItems(proposal, stageName);
  if (stageItems.length === 0) {
    // If the proposal action is SKIP and diagnosis is done, later stages are skipped, not pending
    if (proposal.action === "SKIP" || proposal.action === "skip") {
      const hasDiagnosis = proposal.stage_history.some((item) => item.name === "diagnosis");
      if (hasDiagnosis && stageName !== "diagnosis") {
        return "skipped";
      }
    }
    return "pending";
  }
  if (stageName === "verify" && !proposal.validation_passed) {
    return "failed";
  }
  return "completed";
}

function statusBadgeTone(status: StageStatus): "default" | "good" | "mid" | "bad" | "info" {
  if (status === "completed") {
    return "good";
  }
  if (status === "failed") {
    return "bad";
  }
  if (status === "active") {
    return "info";
  }
  if (status === "skipped") {
    return "mid";
  }
  return "default";
}

function renderValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function StageIcons({ status }: { status: StageStatus }) {
  if (status === "completed") {
    return <CheckCircle2 size={14} className="text-emerald-300" />;
  }
  if (status === "failed") {
    return <XCircle size={14} className="text-rose-300" />;
  }
  if (status === "active") {
    return <Loader2 size={14} className="stage-spinner text-indigo-300" />;
  }
  if (status === "skipped") {
    return <Circle size={14} className="text-amber-400" />;
  }
  return <Circle size={14} className="text-slate-500" />;
}

function StageGrid({
  cycle,
  activeStage,
  isLatest,
  doneStages
}: {
  cycle: ProposalCycle | undefined;
  activeStage: string | null;
  isLatest: boolean;
  doneStages?: string[];
}) {
  return (
    <div className="mb-2 grid grid-cols-4 gap-1">
      {STAGES.map((stage) => {
        const status = getStageStatus(
          stage.key,
          cycle,
          isLatest ? activeStage : null,
          doneStages
        );
        return (
          <div
            key={stage.key}
            className={`rounded-md border px-1.5 py-1 text-center ${
              status === "active"
                ? "stage-active border-indigo-500/70 bg-indigo-500/10"
                : "border-slate-700 bg-slate-900/70"
            }`}
          >
            <div className="mb-1 flex items-center justify-center">
              <StageIcons status={status} />
            </div>
            <div className="text-[10px] font-medium text-slate-200">{stage.label}</div>
            <div className="mt-1">
              <Badge tone={statusBadgeTone(status)}>{status}</Badge>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function CycleDetails({
  cycle,
  cycleKey
}: {
  cycle: ProposalCycle;
  cycleKey: string;
}) {
  return (
    <>
      <div className="space-y-2">
        {STAGES.map((stage) => {
          const stageItems = getStageItems(cycle, stage.key);
          if (stageItems.length === 0) {
            return null;
          }
          return (
            <details
              key={`${cycleKey}-${stage.key}`}
              className="stage-result-enter rounded-md border border-slate-700/80 bg-slate-900/40 p-2"
            >
              <summary className="cursor-pointer text-xs font-medium text-slate-200">
                {stage.label} details ({stageItems.length})
              </summary>
              <div className="mt-2 space-y-2">
                {stageItems.map((item, idx) => (
                  <div
                    key={`${cycleKey}-${stage.key}-${idx}`}
                    className="rounded border border-slate-700/80 bg-slate-950/60 p-2"
                  >
                    <div className="mb-1 text-[11px] font-medium text-slate-300">
                      {String(item.name)} ({String(item.stage)})
                    </div>
                    <pre className="overflow-auto whitespace-pre-wrap text-[11px] text-slate-400">
                      {renderValue(item.result)}
                    </pre>
                  </div>
                ))}
              </div>
            </details>
          );
        })}
      </div>

      {cycle.reasoning ? (
        <div className="mt-2 rounded-md border border-slate-700/80 bg-slate-950/60 p-2 text-[11px] text-slate-400">
          {cycle.reasoning}
        </div>
      ) : null}
    </>
  );
}

export function StageProgress({
  proposals,
  activeStage,
  completedStages
}: {
  proposals: ProposalCycle[];
  activeStage: string | null;
  completedStages?: string[];
}) {
  const cycles = useMemo(
    () =>
      [...proposals].sort((a, b) => {
        if (a.timestamp === b.timestamp) {
          return b.batch_idx - a.batch_idx;
        }
        return a.timestamp < b.timestamp ? 1 : -1;
      }),
    [proposals]
  );
  const [expandedCycleKey, setExpandedCycleKey] = useState<string | null>(null);

  useEffect(() => {
    if (!cycles.length) {
      setExpandedCycleKey(null);
      return;
    }
    const latestKey = `${cycles[0].timestamp}-${cycles[0].batch_idx}-${cycles[0].action}`;
    setExpandedCycleKey(latestKey);
  }, [cycles]);

  const hasActiveStage = activeStage !== null;
  const noCycles = cycles.length === 0;

  return (
    <div className="mt-3 rounded-lg border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-2 text-sm font-semibold">4-Stage Workflow</div>

      {/* Show live stage indicator when we have an active stage but no proposals yet */}
      {noCycles && hasActiveStage ? (
        <div className="space-y-2">
          <div className="rounded-md border border-indigo-500/30 bg-indigo-500/5 p-2 text-xs text-indigo-300">
            <Loader2 size={12} className="mr-1 inline-block stage-spinner" />
            Proposer running...
          </div>
          <StageGrid cycle={undefined} activeStage={activeStage} isLatest={true} doneStages={completedStages} />
        </div>
      ) : noCycles ? (
        <div className="rounded-md border border-slate-700/80 bg-slate-900/60 p-2 text-xs text-slate-400">
          Idle. Waiting for first proposal cycle...
        </div>
      ) : (
        <div className="space-y-2">
          {/* If active stage is set, show a live "in progress" bar above completed cycles */}
          {hasActiveStage && cycles.length > 0 ? (
            <div className="rounded-md border border-indigo-500/30 bg-indigo-500/5 p-2">
              <div className="mb-1 text-xs font-medium text-indigo-300">
                <Loader2 size={12} className="mr-1 inline-block stage-spinner" />
                Next cycle in progress...
              </div>
              <StageGrid cycle={undefined} activeStage={activeStage} isLatest={true} doneStages={completedStages} />
            </div>
          ) : null}

          {cycles.map((cycle) => {
            const cycleKey = `${cycle.timestamp}-${cycle.batch_idx}-${cycle.action}`;
            const isExpanded = expandedCycleKey === cycleKey;
            const isLatest = cycles[0] === cycle;
            return (
              <div
                key={cycleKey}
                className="rounded-md border border-slate-700/80 bg-slate-950/40"
              >
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-2 p-2 text-left"
                  onClick={() => setExpandedCycleKey((prev) => (prev === cycleKey ? null : cycleKey))}
                >
                  <div className="min-w-0">
                    <div className="truncate text-xs font-medium text-slate-200">
                      {isLatest ? "Latest cycle" : "Previous cycle"} - {cycle.batch_id ?? `batch_${String(cycle.batch_idx).padStart(3, "0")}`}
                    </div>
                    <div className="truncate text-[11px] text-slate-400">
                      action: {cycle.action} | validation: {cycle.validation_passed ? "pass" : "fail"}
                    </div>
                  </div>
                  <Badge tone={cycle.validation_passed ? "good" : "bad"}>
                    {cycle.validation_passed ? "Committed" : "Reverted"}
                  </Badge>
                </button>
                {isExpanded ? (
                  <div className="border-t border-slate-700/80 p-2">
                    <StageGrid
                      cycle={cycle}
                      activeStage={isLatest && !hasActiveStage ? activeStage : null}
                      isLatest={isLatest}
                    />
                    <CycleDetails cycle={cycle} cycleKey={cycleKey} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
