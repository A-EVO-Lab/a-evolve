import { useCallback, useEffect, useState } from "react";
import { apiClient } from "@/api/client";
import type { BatchSummary } from "@/types/session";
import type { AppWorldObservation } from "@/types/observation";

export function useBatchData(sessionId: string | null) {
  const [batches, setBatches] = useState<BatchSummary[]>([]);
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);
  const [batchObservations, setBatchObservations] = useState<AppWorldObservation[]>(
    []
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reloadBatches = useCallback(async () => {
    if (!sessionId) {
      setBatches([]);
      setSelectedBatchId(null);
      return;
    }
    try {
      setLoading(true);
      setError(null);
      const response = await apiClient.getBatches(sessionId);
      setBatches(response.batches);
      setSelectedBatchId((prev) => {
        if (prev && response.batches.some((item) => item.id === prev)) {
          return prev;
        }
        return response.batches[0]?.id ?? null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load batches");
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    void reloadBatches();
  }, [reloadBatches]);

  const reloadSelectedBatch = useCallback(async () => {
    if (!sessionId || !selectedBatchId) {
      setBatchObservations([]);
      return;
    }
    try {
      const response = await apiClient.getBatch(sessionId, selectedBatchId);
      setBatchObservations(response.observations);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load batch");
    }
  }, [selectedBatchId, sessionId]);

  useEffect(() => {
    void reloadSelectedBatch();
  }, [reloadSelectedBatch]);

  return {
    batches,
    selectedBatchId,
    setSelectedBatchId,
    batchObservations,
    reloadBatches,
    reloadSelectedBatch,
    loading,
    error
  };
}
