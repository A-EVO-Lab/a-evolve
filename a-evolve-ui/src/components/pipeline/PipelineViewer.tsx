import { Badge } from "@/components/ui/Badge";
import { StageProgress } from "@/components/evolve/StageProgress";
import { StageCard } from "@/components/pipeline/StageCard";
import type { PipelineData } from "@/types/evolve";
import type { AppWorldObservation } from "@/types/observation";
import { getObservationTaskTitle } from "@/utils/taskTitle";

export function PipelineViewer({
  pipeline,
  observations,
  activeStage
}: {
  pipeline: PipelineData | null;
  observations?: AppWorldObservation[];
  activeStage?: string | null;
}) {
  if (!pipeline) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3 text-sm text-slate-300">
        Select a batch to inspect pipeline stage history.
      </div>
    );
  }

  const obs = observations ?? [];

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
        <div className="text-sm font-semibold">4-Stage Workflow</div>
        <div className="mt-2 text-xs text-slate-300">
          Diagnosis {"->"} Plan {"->"} Apply {"->"} Verify (with repair loop when
          verification fails)
        </div>
      </div>

      {(pipeline.proposal_cycles?.length ?? 0) > 0 ? (
        <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
          <div className="mb-2 text-sm font-semibold">Proposal Cycle Replay</div>
          <div className="text-xs text-slate-400">
            Replay from structured proposer cycles (`proposals.jsonl`) for this batch.
          </div>
          <StageProgress proposals={pipeline.proposal_cycles ?? []} activeStage={activeStage ?? null} />
        </div>
      ) : (
        <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3 text-xs text-slate-400">
          No proposal generated for this batch. The batch score may have exceeded the improvement threshold, or the proposer was not triggered.
        </div>
      )}

      {pipeline.has_explicit_stage_history ? (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {pipeline.stage_history.map((item, index) => (
            <StageCard key={`${item.stage}-${index}`} item={item} />
          ))}
        </div>
      ) : obs.length > 0 ? (
        <div className="space-y-2">
          <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3 text-xs text-slate-400">
            Per-task execution summary for this batch.
          </div>
          <div className="grid grid-cols-1 gap-2 xl:grid-cols-2">
            {obs.map((o) => {
              const errorCount = o.errors?.length ?? 0;
              return (
                <div
                  key={o.task_id}
                  className="rounded-lg border border-slate-700 bg-slate-900/70 p-3"
                >
                  <div className="mb-1 flex items-center gap-2">
                    <div>
                      <div className="text-sm font-medium">{getObservationTaskTitle(o)}</div>
                      <div className="text-[11px] text-slate-400">{o.task_id}</div>
                    </div>
                    <Badge tone={o.score >= 0.8 ? "good" : o.score >= 0.4 ? "mid" : "bad"}>
                      {(o.score * 100).toFixed(1)}%
                    </Badge>
                  </div>
                  <div className="flex flex-wrap gap-2 text-xs text-slate-400">
                    <span>{o.num_steps} steps</span>
                    <span>{o.trajectory?.length ?? 0} messages</span>
                    {errorCount > 0 ? (
                      <span className="text-rose-400">{errorCount} errors</span>
                    ) : (
                      <span className="text-emerald-400">no errors</span>
                    )}
                    {o.api_calls && o.api_calls.length > 0 ? (
                      <span>{o.api_calls.length} API calls</span>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3 text-sm text-slate-300">
          No stage history or task observations available.
        </div>
      )}

      {pipeline.version_snapshots.length > 0 ? (
        <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
          <div className="mb-2 text-sm font-semibold">Version Snapshots</div>
          <div className="space-y-2">
            {pipeline.version_snapshots.map((snapshot) => (
              <div
                key={snapshot.version_id}
                className="rounded-md border border-slate-700 bg-slate-950 p-2 text-xs"
              >
                <div>{snapshot.version_id}</div>
                <div className="text-slate-400">
                  {snapshot.description ?? "snapshot"} | {snapshot.timestamp ?? "n/a"}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
