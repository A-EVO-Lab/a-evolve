import { Badge } from "@/components/ui/Badge";
import type { BatchSummary, SessionSummary } from "@/types/session";

function num(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function SummaryCards({
  session,
  batches
}: {
  session: SessionSummary | null;
  batches?: BatchSummary[];
}) {
  if (!session) {
    return null;
  }

  const stats = session.summary_stats;
  const results = session.results;
  const training = (results?.training_stats ?? {}) as Record<string, unknown>;
  const evolution = (results?.evolution_stats ?? {}) as Record<string, unknown>;
  const vanilla = (results?.vanilla_results ?? {}) as Record<string, unknown>;
  const evolved = (results?.evolved_results ?? {}) as Record<string, unknown>;
  const improvement = (results?.improvement ?? {}) as Record<string, unknown>;

  const totalTasks = num(training.total_tasks) || stats.total_samples || 0;
  const totalBatches = session.counts.batches || stats.total_batches || 0;

  // Average Score: prefer results.json, then compute from batch data, then fall back to summary_stats
  const avgScoreFromBatches =
    batches && batches.length > 0
      ? batches.reduce((acc, b) => acc + b.average_score * b.num_tasks, 0) /
        batches.reduce((acc, b) => acc + b.num_tasks, 0)
      : 0;
  const avgScore = num(training.average_score) || avgScoreFromBatches || 0;

  const completionRate = num(training.completion_rate);
  const completedTasks = num(training.completed_tasks);
  const avgSteps = num(training.average_steps);

  const hasComparison = Object.keys(vanilla).length > 0 && Object.keys(evolved).length > 0;
  const scoreDelta = num(improvement.score_delta);
  const tgcDelta = num(improvement.tgc_delta);

  return (
    <div className="space-y-3">
      {/* Row 1: Vanilla vs Evolved comparison + artifacts */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        {hasComparison ? (
          <>
            <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
              <div className="text-xs text-slate-400">Vanilla Agent</div>
              <div className="text-xl font-semibold">TGC {pct(num(vanilla.task_goal_completion))}</div>
              <div className="text-xs text-slate-500">
                average score {pct(num(vanilla.average_score))}
              </div>
              <div className="mt-1 text-xs text-slate-500">
                {num(vanilla.tasks_completed)}/{num(vanilla.total_tasks)} tasks | completion {pct(num(vanilla.completion_rate))}
              </div>
            </div>
            <div className="rounded-lg border border-indigo-500/50 bg-indigo-950/40 p-3 shadow-md shadow-indigo-500/10">
              <div className="text-xs font-medium text-indigo-300">Evolved Agent</div>
              <div className="text-xl font-semibold text-indigo-100">TGC {pct(num(evolved.task_goal_completion))}</div>
              <div className="text-xs text-indigo-300/80">
                average score {pct(num(evolved.average_score))}
              </div>
              <div className="mt-1 text-xs text-indigo-400/80">
                {num(evolved.tasks_completed)}/{num(evolved.total_tasks)} tasks | completion {pct(num(evolved.completion_rate))}
              </div>
              {scoreDelta !== 0 ? (
                <div className={`mt-1 text-xs font-medium ${scoreDelta > 0 ? "text-emerald-400" : "text-rose-400"}`}>
                  {scoreDelta > 0 ? "+" : ""}{pct(scoreDelta)} score
                  {tgcDelta !== 0 ? ` | ${tgcDelta > 0 ? "+" : ""}${pct(tgcDelta)} TGC` : ""}
                </div>
              ) : null}
            </div>
          </>
        ) : (
          <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3 lg:col-span-2">
            <div className="text-xs text-slate-400">Evaluation</div>
            <div className="text-sm text-slate-300">No vanilla/evolved comparison available</div>
          </div>
        )}
        <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
          <div className="text-xs text-slate-400">Artifacts</div>
          <div className="mt-1 flex flex-wrap gap-1">
            <Badge>Tools {num(evolution.total_tools) || session.counts.tools}</Badge>
            <Badge>Skills {session.counts.skills}</Badge>
            <Badge>Knowledge {session.counts.knowledge}</Badge>
            <Badge>Patches {num(evolution.total_patches) || session.counts.patches}</Badge>
          </div>
          {num(evolution.tools_created) > 0 || num(evolution.patches_added) > 0 ? (
            <div className="mt-1 text-xs text-slate-500">
              created {num(evolution.tools_created)} tools, {num(evolution.patches_added)} patches
            </div>
          ) : null}
        </div>
      </div>

      {/* Row 2: Training stats */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
          <div className="text-xs text-slate-400">Training average score</div>
          <div className="text-xl font-semibold">{pct(avgScore)}</div>
          {completionRate > 0 ? (
            <div className="text-xs text-slate-500">
              completion {pct(completionRate)}
            </div>
          ) : null}
        </div>
        <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
          <div className="text-xs text-slate-400">Training total tasks</div>
          <div className="text-xl font-semibold">{totalTasks}</div>
          <div className="text-xs text-slate-500">
            {completedTasks > 0 ? `${completedTasks} completed` : ""}
          </div>
        </div>
        <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
          <div className="text-xs text-slate-400">Training total batches</div>
          <div className="text-xl font-semibold">{totalBatches}</div>
        </div>
        <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
          <div className="text-xs text-slate-400">Training avg steps / task</div>
          <div className="text-xl font-semibold">
            {avgSteps > 0 ? avgSteps.toFixed(1) : "—"}
          </div>
        </div>
      </div>
    </div>
  );
}
