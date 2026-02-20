import { useCallback, useEffect, useMemo, useState } from "react";
import { apiClient } from "@/api/client";
import type { SessionSummary } from "@/types/session";

export function useSessionData() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reloadSessions = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await apiClient.getSessions();
      setSessions(response);
      setSelectedSessionId((prev) => {
        if (prev && response.some((item) => item.id === prev)) {
          return prev;
        }
        return response[0]?.id ?? null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reloadSessions();
  }, [reloadSessions]);

  const selectedSession = useMemo(
    () => sessions.find((s) => s.id === selectedSessionId) ?? null,
    [sessions, selectedSessionId]
  );

  return {
    sessions,
    selectedSessionId,
    selectedSession,
    setSelectedSessionId,
    reloadSessions,
    loading,
    error
  };
}
