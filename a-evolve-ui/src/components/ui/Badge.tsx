import type { ReactNode } from "react";

type BadgeProps = {
  children: ReactNode;
  tone?: "default" | "good" | "mid" | "bad" | "info";
};

export function Badge({ children, tone = "default" }: BadgeProps) {
  const toneClass =
    tone === "good"
      ? "bg-emerald-600/20 text-emerald-300"
      : tone === "mid"
        ? "bg-amber-500/20 text-amber-300"
        : tone === "bad"
          ? "bg-rose-600/20 text-rose-300"
          : tone === "info"
            ? "bg-indigo-600/20 text-indigo-300"
            : "bg-slate-700/70 text-slate-200";

  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${toneClass}`}
    >
      {children}
    </span>
  );
}
