type Status = "success" | "error" | "warning" | "pending";

const statusClasses: Record<Status, string> = {
  success: "bg-emerald-400",
  error: "bg-rose-400",
  warning: "bg-amber-300",
  pending: "bg-violet-400"
};

export function StatusIndicator({
  status,
  label
}: {
  status: Status;
  label?: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-slate-300">
      <span className={`h-2 w-2 rounded-full ${statusClasses[status]}`} />
      {label ?? status}
    </span>
  );
}
