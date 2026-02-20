import { Badge } from "@/components/ui/Badge";
import type { AppWorldObservation } from "@/types/observation";
import { getObservationTaskTitle } from "@/utils/taskTitle";

export function BatchDetail({
  observations,
  selectedTaskId,
  onSelectTask
}: {
  observations: AppWorldObservation[];
  selectedTaskId: string | null;
  onSelectTask: (taskId: string) => void;
}) {
  const avg =
    observations.length > 0
      ? observations.reduce((acc, o) => acc + o.score, 0) / observations.length
      : 0;

  return (
    <div className="h-full overflow-auto p-3">
      <div className="mb-3 flex items-center gap-2">
        <div className="text-sm font-semibold">Batch Details</div>
        <Badge tone="info">Tasks {observations.length}</Badge>
        <Badge>{(avg * 100).toFixed(1)}%</Badge>
      </div>
      <div className="space-y-2">
        {observations.map((obs) => {
          const errorCount = obs.errors?.length ?? 0;
          const active = selectedTaskId === obs.task_id;
          const taskTitle = getObservationTaskTitle(obs);
          return (
            <button
              key={obs.task_id}
              type="button"
              onClick={() => onSelectTask(obs.task_id)}
              className={`w-full rounded-lg border p-3 text-left transition-colors duration-150 ${
                active
                  ? "border-indigo-500 bg-indigo-500/10"
                  : "border-slate-700 bg-slate-900/70 hover:border-slate-600 hover:bg-slate-800/70"
              }`}
            >
              <div className="mb-1 text-sm font-medium">{taskTitle}</div>
              <div className="mb-1 text-[11px] text-slate-400">{obs.task_id}</div>
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
                <Badge tone={obs.score >= 0.8 ? "good" : obs.score >= 0.4 ? "mid" : "bad"}>
                  score {(obs.score * 100).toFixed(1)}%
                </Badge>
                <Badge>steps {obs.num_steps}</Badge>
                <Badge tone={errorCount > 0 ? "bad" : "good"}>errors {errorCount}</Badge>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
