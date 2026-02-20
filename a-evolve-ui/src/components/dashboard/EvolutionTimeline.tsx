import type { BatchSummary } from "@/types/session";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

export function EvolutionTimeline({ batches }: { batches: BatchSummary[] }) {
  const data = batches.map((batch, index) => ({
    batch: batch.id.replace("batch_", ""),
    tasks: batch.num_tasks,
    score: Number(batch.average_score.toFixed(3)),
    idx: index
  }));

  return (
    <div className="h-64 rounded-lg border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-2 text-sm font-semibold">Evolution Timeline</div>
      <ResponsiveContainer width="100%" height="90%">
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
          <XAxis dataKey="batch" stroke="#94a3b8" />
          <YAxis stroke="#94a3b8" />
          <Tooltip />
          <Bar dataKey="tasks" fill="#22d3ee" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
