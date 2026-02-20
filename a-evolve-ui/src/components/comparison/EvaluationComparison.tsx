import { Badge } from "@/components/ui/Badge";
import type { AppWorldObservation } from "@/types/observation";
import type {
  EvaluationTaskResult,
  SessionEvaluationsResponse
} from "@/types/session";
import { getObservationTaskTitle } from "@/utils/taskTitle";

function scoreToPct(score: number | null | undefined): string {
  if (typeof score !== "number" || Number.isNaN(score)) {
    return "-";
  }
  return `${(score * 100).toFixed(1)}%`;
}

function deltaTone(delta: number | null): "default" | "good" | "bad" {
  if (delta === null) {
    return "default";
  }
  if (delta > 0) {
    return "good";
  }
  if (delta < 0) {
    return "bad";
  }
  return "default";
}

function EvaluationTaskList({
  title,
  tasks,
  taskTitleById
}: {
  title: string;
  tasks: EvaluationTaskResult[];
  taskTitleById: Record<string, string>;
}) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-2 text-sm font-semibold">{title}</div>
      {tasks.length === 0 ? (
        <div className="text-xs text-slate-400">No evaluation task data.</div>
      ) : (
        <div className="max-h-72 space-y-2 overflow-auto pr-1">
          {tasks.map((task) => (
            <details
              key={task.task_id}
              className="rounded border border-slate-700/80 bg-slate-950/50 p-2"
            >
              <summary className="cursor-pointer text-xs text-slate-200">
                {taskTitleById[task.task_id] ?? task.task_id} | score {scoreToPct(task.score)} |
                tests {task.num_passed_tests}/{task.num_total_tests}
              </summary>
              <div className="mt-1 text-[11px] text-slate-500">{task.task_id}</div>
              <div className="mt-2 text-[11px] text-slate-400">
                <div className="mb-2">
                  passed {task.num_passed_tests}, failed {task.num_failed_tests}, total{" "}
                  {task.num_total_tests}
                </div>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded border border-slate-700 bg-slate-900 p-2 text-[11px]">
                  {task.report ?? "No report"}
                </pre>
              </div>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}

export function EvaluationComparison({
  trainingObservations,
  evaluations,
  loading,
  error,
  onOpenBatchTab
}: {
  trainingObservations: AppWorldObservation[];
  evaluations: SessionEvaluationsResponse | null;
  loading: boolean;
  error: string | null;
  onOpenBatchTab: () => void;
}) {
  const taskTitleById = Object.fromEntries(
    trainingObservations.map((obs) => [obs.task_id, getObservationTaskTitle(obs, 60)])
  );
  const avgTrainingScore =
    trainingObservations.length > 0
      ? trainingObservations.reduce((acc, obs) => acc + obs.score, 0) / trainingObservations.length
      : 0;
  const vanillaAvg = evaluations?.vanilla.summary.avg_score ?? 0;
  const evolvedAvg = evaluations?.evolved.summary.avg_score ?? 0;
  const improvement = evolvedAvg - vanillaAvg;

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
        <div className="mb-2 text-sm font-semibold">Training Results</div>
        <div className="mb-2 flex flex-wrap gap-2 text-xs">
          <Badge tone="info">Tasks {trainingObservations.length}</Badge>
          <Badge tone="mid">Avg score {scoreToPct(avgTrainingScore)}</Badge>
          <button
            type="button"
            className="rounded border border-slate-700 bg-slate-800 px-2 py-0.5 text-xs text-slate-200 transition-colors duration-150 hover:bg-slate-700/80"
            onClick={onOpenBatchTab}
          >
            Open batch tab
          </button>
        </div>
        <div className="max-h-52 overflow-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="text-slate-400">
                <th className="py-1">Task</th>
                <th className="py-1">Score</th>
                <th className="py-1">Steps</th>
                <th className="py-1">Errors</th>
              </tr>
            </thead>
            <tbody>
              {trainingObservations.map((obs) => (
                <tr key={obs.task_id} className="border-t border-slate-800">
                  <td className="py-1">
                    <div className="font-medium text-slate-200">
                      {getObservationTaskTitle(obs, 60)}
                    </div>
                    <div className="text-[11px] text-slate-400">{obs.task_id}</div>
                  </td>
                  <td className="py-1">{scoreToPct(obs.score)}</td>
                  <td className="py-1">{obs.num_steps}</td>
                  <td className="py-1">{obs.errors?.length ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {trainingObservations.length === 0 ? (
            <div className="py-2 text-xs text-slate-400">No training observations loaded.</div>
          ) : null}
        </div>
      </div>

      {loading ? (
        <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3 text-sm text-slate-300">
          Loading evaluation data...
        </div>
      ) : error ? (
        <div className="rounded-lg border border-rose-700/70 bg-rose-900/20 p-3 text-sm text-rose-200">
          {error}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <EvaluationTaskList
              title={`Vanilla Evaluation (${evaluations?.vanilla.summary.num_tasks ?? 0})`}
              tasks={evaluations?.vanilla.tasks ?? []}
              taskTitleById={taskTitleById}
            />
            <EvaluationTaskList
              title={`Evolved Evaluation (${evaluations?.evolved.summary.num_tasks ?? 0})`}
              tasks={evaluations?.evolved.tasks ?? []}
              taskTitleById={taskTitleById}
            />
          </div>

          <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
            <div className="mb-2 text-sm font-semibold">Vanilla vs Evolved by Task</div>
            <div className="mb-2 flex flex-wrap gap-2 text-xs">
              <Badge tone="mid">Vanilla avg {scoreToPct(vanillaAvg)}</Badge>
              <Badge tone="info">Evolved avg {scoreToPct(evolvedAvg)}</Badge>
              <Badge tone={improvement >= 0 ? "good" : "bad"}>
                Delta {improvement >= 0 ? "+" : ""}
                {scoreToPct(improvement)}
              </Badge>
            </div>
            <div className="max-h-72 overflow-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="text-slate-400">
                    <th className="py-1">Task</th>
                    <th className="py-1">Vanilla</th>
                    <th className="py-1">Evolved</th>
                    <th className="py-1">Delta</th>
                  </tr>
                </thead>
                <tbody>
                  {(evaluations?.comparison ?? []).map((row) => (
                    <tr key={row.task_id} className="border-t border-slate-800">
                      <td className="py-1">
                        <div className="font-medium text-slate-200">
                          {taskTitleById[row.task_id] ?? row.task_id}
                        </div>
                        <div className="text-[11px] text-slate-400">{row.task_id}</div>
                      </td>
                      <td className="py-1">{scoreToPct(row.vanilla_score)}</td>
                      <td className="py-1">{scoreToPct(row.evolved_score)}</td>
                      <td className="py-1">
                        <Badge tone={deltaTone(row.delta)}>
                          {row.delta === null ? "-" : `${row.delta >= 0 ? "+" : ""}${scoreToPct(row.delta)}`}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {(evaluations?.comparison.length ?? 0) === 0 ? (
                <div className="py-2 text-xs text-slate-400">No comparison rows.</div>
              ) : null}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
