import { Badge } from "@/components/ui/Badge";
import type { TaskProgress } from "@/hooks/useEvolveSession";
import { CheckCircle2, Loader2 } from "lucide-react";

export function TaskProgressBar({
  taskProgress,
  completedTasks,
  currentBatch,
  totalBatches
}: {
  taskProgress: TaskProgress | null;
  completedTasks: Array<{ task_id: string; score: number; steps: number }>;
  currentBatch: number;
  totalBatches: number;
}) {
  if (!taskProgress && completedTasks.length === 0) {
    return null;
  }

  const stepPercent =
    taskProgress?.type === "step" && taskProgress.total_steps
      ? Math.round((taskProgress.step_num! / taskProgress.total_steps) * 100)
      : 0;

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-2 text-sm font-semibold">Live Task Progress</div>

      {/* Current task/step */}
      {taskProgress ? (
        <div className="mb-2 space-y-1">
          {taskProgress.type === "task_start" ? (
            <div className="flex items-center gap-2 text-xs text-slate-200">
              <Loader2 size={12} className="stage-spinner text-indigo-300" />
              <span>
                Starting task {taskProgress.task_num}/{taskProgress.total_tasks}:{" "}
                <span className="font-mono text-indigo-300">{taskProgress.task_id}</span>
              </span>
            </div>
          ) : taskProgress.type === "step" ? (
            <div className="space-y-1">
              <div className="flex items-center justify-between text-xs text-slate-300">
                <span>
                  Step {taskProgress.step_num}/{taskProgress.total_steps}
                </span>
                <span className="text-slate-400">{stepPercent}%</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded bg-slate-800">
                <div
                  className="h-full bg-indigo-400 transition-all duration-300"
                  style={{ width: `${stepPercent}%` }}
                />
              </div>
            </div>
          ) : taskProgress.type === "task_done" ? (
            <div className="flex items-center gap-2 text-xs text-emerald-300">
              <CheckCircle2 size={12} />
              <span>
                Task {taskProgress.task_id} done — score{" "}
                {((taskProgress.score ?? 0) * 100).toFixed(0)}%, {taskProgress.steps} steps
              </span>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Completed tasks summary */}
      {completedTasks.length > 0 ? (
        <div className="space-y-1">
          <div className="text-[11px] text-slate-400">
            Completed: {completedTasks.length} task{completedTasks.length > 1 ? "s" : ""}
            {totalBatches > 0 ? ` (batch ${currentBatch}/${totalBatches})` : ""}
          </div>
          <div className="flex flex-wrap gap-1">
            {completedTasks.map((t) => (
              <Badge
                key={t.task_id}
                tone={t.score >= 0.8 ? "good" : t.score >= 0.4 ? "mid" : "bad"}
              >
                {t.task_id.slice(0, 9)}… {(t.score * 100).toFixed(0)}%
              </Badge>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
