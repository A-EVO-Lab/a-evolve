import { Badge } from "@/components/ui/Badge";
import { StatusIndicator } from "@/components/ui/StatusIndicator";

type RunStatusProps = {
  model: string;
  status: "running" | "completed" | "failed" | "idle" | "configuring";
  elapsedSeconds: number;
  onStop?: () => void;
  canStop?: boolean;
  error?: string | null;
};

function toClock(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

export function RunStatus({
  model,
  status,
  elapsedSeconds,
  onStop,
  canStop,
  error
}: RunStatusProps) {
  const indicator =
    status === "running"
      ? "pending"
      : status === "completed"
        ? "success"
        : status === "failed"
          ? "error"
          : "warning";

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-2 text-sm font-semibold">Run Status</div>
      <div className="text-xs text-slate-300">Model: {model}</div>
      <div className="mt-1 flex items-center gap-2">
        <StatusIndicator status={indicator} label={status} />
        <Badge>{toClock(elapsedSeconds)}</Badge>
      </div>
      {error ? (
        <div className="mt-2 rounded-md border border-rose-600/50 bg-rose-900/20 px-2 py-1.5 text-xs text-rose-300">
          {error}
        </div>
      ) : null}
      {canStop ? (
        <button
          type="button"
          onClick={onStop}
          className="mt-3 rounded-md border border-rose-600 bg-rose-600/10 px-2 py-1 text-xs text-rose-300"
        >
          Stop
        </button>
      ) : null}
    </div>
  );
}
