import { BatchCard } from "@/components/batch/BatchCard";
import { LiveLog } from "@/components/evolve/LiveLog";
import { ProgressBar } from "@/components/evolve/ProgressBar";
import { RunStatus } from "@/components/evolve/RunStatus";
import { StageProgress } from "@/components/evolve/StageProgress";
import { TaskProgressBar } from "@/components/evolve/TaskProgressBar";
import { Badge } from "@/components/ui/Badge";
import type { EvolveState } from "@/hooks/useEvolveSession";
import type { BatchSummary } from "@/types/session";

export function EvolveLeftPanel({
  evolveState,
  batches,
  selectedBatchId,
  onSelectBatch,
  onStop
}: {
  evolveState: EvolveState;
  batches: BatchSummary[];
  selectedBatchId: string | null;
  onSelectBatch: (batchId: string) => void;
  onStop: () => void;
}) {
  return (
    <div className="h-full overflow-auto border-r border-slate-700 bg-slate-950 p-3">
      <RunStatus
        model={evolveState.config?.main_model ?? "N/A"}
        status={evolveState.status}
        elapsedSeconds={evolveState.elapsedSeconds}
        onStop={onStop}
        canStop={evolveState.status === "running"}
        error={evolveState.error}
      />

      <div className="mt-3">
        <StageProgress
          proposals={evolveState.proposals}
          activeStage={evolveState.activeStage}
          completedStages={evolveState.completedStages}
        />
      </div>

      <div className="mt-3">
        <ProgressBar
          currentBatch={evolveState.currentBatch}
          totalBatches={evolveState.totalBatches}
        />
      </div>

      <div className="mt-3">
        <TaskProgressBar
          taskProgress={evolveState.taskProgress}
          completedTasks={evolveState.completedTasks}
          currentBatch={evolveState.currentBatch}
          totalBatches={evolveState.totalBatches}
        />
      </div>

      <div className="mt-3 rounded-lg border border-slate-700 bg-slate-900/70 p-3">
        <div className="mb-2 text-sm font-semibold">Evolved Artifacts</div>
        <div className="flex flex-wrap gap-2">
          <Badge>Tools {evolveState.artifactCounts.tools}</Badge>
          <Badge>Skills {evolveState.artifactCounts.skills}</Badge>
          <Badge>Knowledge {evolveState.artifactCounts.knowledge}</Badge>
          <Badge>Patches {evolveState.artifactCounts.patches}</Badge>
        </div>
      </div>

      <div className="mt-3 rounded-lg border border-slate-700 bg-slate-900/70 p-3">
        <div className="mb-2 text-sm font-semibold">Completed Batches</div>
        <div className="space-y-2">
          {batches.map((batch) => (
            <BatchCard
              key={batch.id}
              batch={batch}
              active={selectedBatchId === batch.id}
              onClick={() => onSelectBatch(batch.id)}
            />
          ))}
          {batches.length === 0 ? (
            <div className="text-xs text-slate-400">No completed batch yet.</div>
          ) : null}
        </div>
      </div>

      <div className="mt-3">
        <LiveLog lines={evolveState.logs} />
      </div>
    </div>
  );
}
