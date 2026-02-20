import type { AppWorldObservation } from "@/types/observation";
import { getObservationTaskTitle } from "@/utils/taskTitle";

export function TaskDiffTable({
  observations
}: {
  observations: AppWorldObservation[];
}) {
  const sorted = [...observations].sort((a, b) => b.score - a.score);
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-2 text-sm font-semibold">Task-Level Scores</div>
      <div className="max-h-64 overflow-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="text-slate-400">
              <th className="py-1">Task</th>
              <th className="py-1">Score</th>
              <th className="py-1">Completed</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((obs) => (
              <tr key={obs.task_id} className="border-t border-slate-800">
                <td className="py-1">
                  <div className="font-medium text-slate-200">
                    {getObservationTaskTitle(obs, 60)}
                  </div>
                  <div className="text-[11px] text-slate-400">{obs.task_id}</div>
                </td>
                <td className="py-1">{(obs.score * 100).toFixed(1)}%</td>
                <td className="py-1">{obs.task_completed ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
