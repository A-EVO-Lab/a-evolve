import { Badge } from "@/components/ui/Badge";
import type { BatchSummary } from "@/types/session";

function scoreTone(score: number): "good" | "mid" | "bad" {
  if (score >= 0.8) {
    return "good";
  }
  if (score >= 0.4) {
    return "mid";
  }
  return "bad";
}

export function BatchCard({
  batch,
  active,
  onClick
}: {
  batch: BatchSummary;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`w-full rounded-lg border p-3 text-left transition-colors duration-150 ${
        active
          ? "border-indigo-500 bg-indigo-500/10"
          : "border-slate-700 bg-slate-900/70 hover:border-slate-600 hover:bg-slate-800/70"
      }`}
      onClick={onClick}
    >
      <div className="mb-1 text-sm font-medium">{batch.id}</div>
      <div className="flex items-center gap-2">
        <Badge tone={scoreTone(batch.average_score)}>
          {(batch.average_score * 100).toFixed(1)}%
        </Badge>
        <Badge>{batch.num_tasks} tasks</Badge>
      </div>
    </button>
  );
}
