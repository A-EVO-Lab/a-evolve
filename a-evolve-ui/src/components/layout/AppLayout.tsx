import { useEffect } from "react";
import type { ReactNode } from "react";
import { SessionPicker } from "@/components/session/SessionPicker";
import type { SessionSummary } from "@/types/session";

export function AppLayout({
  sessions,
  selectedSessionId,
  onSelectSession,
  mode,
  onModeChange,
  evolveHeaderContent,
  children
}: {
  sessions: SessionSummary[];
  selectedSessionId: string | null;
  onSelectSession: (id: string) => void;
  mode: "browse" | "evolve";
  onModeChange: (mode: "browse" | "evolve") => void;
  evolveHeaderContent?: ReactNode;
  children: ReactNode;
}) {
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add("dark");
  }, []);

  return (
    <div className="flex h-full w-full flex-col">
      <header className="flex items-center justify-between border-b border-slate-700 bg-slate-900 px-4 py-3">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="text-sm font-semibold text-slate-100">
            A-Evolve Visual Frontend
          </div>
          <div className="inline-flex rounded-md border border-slate-700 bg-slate-800 p-0.5">
            <button
              type="button"
              className={`rounded px-2 py-1 text-xs transition-colors duration-150 ${
                mode === "browse"
                  ? "bg-slate-700 text-slate-100"
                  : "text-slate-300 hover:bg-slate-600/50"
              }`}
              onClick={() => onModeChange("browse")}
            >
              Browse Sessions
            </button>
            <button
              type="button"
              className={`rounded px-2 py-1 text-xs transition-colors duration-150 ${
                mode === "evolve"
                  ? "bg-slate-700 text-slate-100"
                  : "text-slate-300 hover:bg-slate-600/50"
              }`}
              onClick={() => onModeChange("evolve")}
            >
              Start Evolve
            </button>
          </div>
          {mode === "browse" ? (
            <SessionPicker
              sessions={sessions}
              selectedSessionId={selectedSessionId}
              onSelect={onSelectSession}
            />
          ) : null}
        </div>
      </header>
      {mode === "evolve" && evolveHeaderContent ? (
        <div className="border-b border-slate-700 bg-slate-900 px-4 py-3">
          {evolveHeaderContent}
        </div>
      ) : null}
      <main className="min-h-0 flex-1">{children}</main>
    </div>
  );
}
