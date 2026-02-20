import type { SessionSummary } from "@/types/session";

export function SessionPicker({
  sessions,
  selectedSessionId,
  onSelect
}: {
  sessions: SessionSummary[];
  selectedSessionId: string | null;
  onSelect: (sessionId: string) => void;
}) {
  return (
    <select
      className="rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm"
      value={selectedSessionId ?? ""}
      onChange={(event) => onSelect(event.target.value)}
    >
      {sessions.length === 0 && <option value="">No sessions</option>}
      {sessions.map((session) => (
        <option key={session.id} value={session.id}>
          {session.id}
        </option>
      ))}
    </select>
  );
}
