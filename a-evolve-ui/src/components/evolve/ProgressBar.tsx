export function ProgressBar({
  currentBatch,
  totalBatches
}: {
  currentBatch: number;
  totalBatches: number;
}) {
  const safeTotal = totalBatches <= 0 ? 1 : totalBatches;
  const percentage = Math.min(100, Math.round((currentBatch / safeTotal) * 100));

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-2 text-sm font-semibold">Progress</div>
      <div className="mb-2 text-xs text-slate-300">
        Batch {currentBatch}/{totalBatches || "?"}
      </div>
      <div className="h-2 overflow-hidden rounded bg-slate-800">
        <div
          className="h-full bg-indigo-400 transition-all"
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  );
}
