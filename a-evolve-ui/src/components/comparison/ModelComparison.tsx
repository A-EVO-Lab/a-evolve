import { TaskDiffTable } from "@/components/comparison/TaskDiffTable";
import type { AppWorldObservation } from "@/types/observation";
import type { SessionSummary } from "@/types/session";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export function ModelComparison({
  session,
  observations
}: {
  session: SessionSummary | null;
  observations: AppWorldObservation[];
}) {
  const vanillaScore = Number(
    session?.results?.vanilla_results?.average_score ?? 0
  );
  const evolvedScore = Number(
    session?.results?.evolved_results?.average_score ?? 0
  );
  const vanillaCompletion = Number(
    session?.results?.vanilla_results?.completion_rate ?? 0
  );
  const evolvedCompletion = Number(
    session?.results?.evolved_results?.completion_rate ?? 0
  );

  const data = [
    { metric: "avg_score", vanilla: vanillaScore, evolved: evolvedScore },
    {
      metric: "completion_rate",
      vanilla: vanillaCompletion,
      evolved: evolvedCompletion
    }
  ];

  return (
    <div className="space-y-3">
      <div className="h-72 rounded-lg border border-slate-700 bg-slate-900/70 p-3">
        <div className="mb-2 text-sm font-semibold">Vanilla vs Evolved</div>
        <ResponsiveContainer width="100%" height="90%">
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
            <XAxis dataKey="metric" stroke="#94a3b8" />
            <YAxis domain={[0, 1]} stroke="#94a3b8" />
            <Tooltip />
            <Bar dataKey="vanilla" fill="#94a3b8" />
            <Bar dataKey="evolved" fill="#6366f1" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <TaskDiffTable observations={observations} />
    </div>
  );
}
