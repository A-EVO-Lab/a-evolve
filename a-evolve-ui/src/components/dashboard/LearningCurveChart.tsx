import type { BatchSummary, SessionSummary } from "@/types/session";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

export function LearningCurveChart({
  session,
  batches
}: {
  session: SessionSummary | null;
  batches?: BatchSummary[];
}) {
  // Prefer actual batch data from the API; fall back to summary_stats
  const fromBatches = (batches ?? []).map((b, index) => ({
    batch: b.id.replace("batch_", "B"),
    score: b.average_score,
    tasks: b.num_tasks,
    idx: index
  }));

  const fromStats =
    session?.summary_stats.batch_accuracies?.map((item, index) => ({
      batch: `B${item.batch_id ?? index}`,
      score: Number(item.accuracy ?? 0),
      tasks: item.num_samples ?? 0,
      idx: index
    })) ?? [];

  const points = fromBatches.length > 0 ? fromBatches : fromStats;

  if (points.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-slate-700 bg-slate-900/70 p-3 text-sm text-slate-400">
        No batch data for learning curve
      </div>
    );
  }

  return (
    <div className="h-64 rounded-lg border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-2 text-sm font-semibold">Learning Curve (per batch)</div>
      <ResponsiveContainer width="100%" height="90%">
        <LineChart data={points}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
          <XAxis dataKey="batch" stroke="#94a3b8" />
          <YAxis domain={[0, 1]} stroke="#94a3b8" tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} />
          <Tooltip formatter={(v: number) => `${(v * 100).toFixed(1)}%`} />
          <Line type="monotone" dataKey="score" stroke="#6366f1" strokeWidth={2} dot />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
