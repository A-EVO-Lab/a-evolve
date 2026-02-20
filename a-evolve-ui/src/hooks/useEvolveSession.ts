import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiClient } from "@/api/client";
import type { EvolveConfig, EvolveStatus } from "@/types/evolve-config";
import type { ProposalCycle } from "@/types/proposal";

type EvolvePhase = "idle" | "configuring" | "running" | "completed" | "failed";

type EvolveEventCallbacks = {
  onBatchComplete?: (batchId: string) => void;
  onArtifactChange?: () => void;
  onDone?: (sessionId: string, status: "completed" | "failed") => void;
};

export type TaskProgress = {
  type: "task_start" | "step" | "task_done";
  task_id?: string;
  task_num?: number;
  total_tasks?: number;
  step_num?: number;
  total_steps?: number;
  score?: number;
  steps?: number;
};

export type EvolveState = {
  status: EvolvePhase;
  sessionId: string | null;
  config: EvolveConfig | null;
  logs: string[];
  currentBatch: number;
  totalBatches: number;
  elapsedSeconds: number;
  artifactCounts: {
    tools: number;
    skills: number;
    knowledge: number;
    patches: number;
  };
  pid: number | null;
  error: string | null;
  proposals: ProposalCycle[];
  activeStage: string | null;
  /** Stages that were previously active and are now considered done (within the current proposal cycle). */
  completedStages: string[];
  taskProgress: TaskProgress | null;
  completedTasks: Array<{ task_id: string; score: number; steps: number }>;
};

const MAX_LOG_LINES = 1000;

const INITIAL_STATE: EvolveState = {
  status: "idle",
  sessionId: null,
  config: null,
  logs: [],
  currentBatch: 0,
  totalBatches: 0,
  elapsedSeconds: 0,
  artifactCounts: {
    tools: 0,
    skills: 0,
    knowledge: 0,
    patches: 0
  },
  pid: null,
  error: null,
  proposals: [],
  activeStage: null,
  completedStages: [],
  taskProgress: null,
  completedTasks: []
};

function parseJsonData<T>(payload: MessageEvent): T | null {
  try {
    return JSON.parse(payload.data) as T;
  } catch {
    return null;
  }
}

export function useEvolveSession(callbacks: EvolveEventCallbacks = {}) {
  const [state, setState] = useState<EvolveState>(INITIAL_STATE);
  const sourceRef = useRef<EventSource | null>(null);

  const closeStream = useCallback(() => {
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
    }
  }, []);

  const appendLog = useCallback((line: string) => {
    setState((prev) => ({
      ...prev,
      logs: [...prev.logs, line].slice(-MAX_LOG_LINES)
    }));
  }, []);

  const connect = useCallback(
    (sessionId: string) => {
      closeStream();
      const source = new EventSource(apiClient.getEvolveStreamUrl(sessionId));
      sourceRef.current = source;

      source.addEventListener("log", (event) => {
        const data = parseJsonData<{ line?: string }>(event as MessageEvent);
        if (data?.line) {
          appendLog(data.line);
        }
      });

      source.addEventListener("batch_complete", (event) => {
        const data = parseJsonData<{ batch_id?: string }>(event as MessageEvent);
        if (data?.batch_id) {
          callbacks.onBatchComplete?.(data.batch_id);
        }
      });

      source.addEventListener("artifact_change", (event) => {
        const data = parseJsonData<{
          tools?: number;
          skills?: number;
          knowledge?: number;
          patches?: number;
        }>(event as MessageEvent);
        if (!data) {
          return;
        }
        setState((prev) => ({
          ...prev,
          artifactCounts: {
            tools: data.tools ?? prev.artifactCounts.tools,
            skills: data.skills ?? prev.artifactCounts.skills,
            knowledge: data.knowledge ?? prev.artifactCounts.knowledge,
            patches: data.patches ?? prev.artifactCounts.patches
          }
        }));
        callbacks.onArtifactChange?.();
      });

      source.addEventListener("status", (event) => {
        const data = parseJsonData<{
          status?: "running" | "completed" | "failed";
          current_batch?: number;
          total_batches?: number;
          elapsed_seconds?: number;
        }>(event as MessageEvent);
        if (!data) {
          return;
        }
        setState((prev) => ({
          ...prev,
          status:
            data.status === "completed"
              ? "completed"
              : data.status === "failed"
                ? "failed"
                : "running",
          currentBatch: data.current_batch ?? prev.currentBatch,
          totalBatches: data.total_batches ?? prev.totalBatches,
          elapsedSeconds: data.elapsed_seconds ?? prev.elapsedSeconds
        }));
      });

      source.addEventListener("stage", (event) => {
        const data = parseJsonData<{
          stage?: string;
          stage_name?: string;
          line?: string;
          status?: string;
        }>(
          event as MessageEvent
        );
        if (data?.stage_name) {
          setState((prev) => {
            const newStage = data.stage_name ?? null;
            // If the active stage is changing, move the old one to completedStages
            const prevActive = prev.activeStage;
            let newCompleted = prev.completedStages;
            if (prevActive && prevActive !== newStage && !prev.completedStages.includes(prevActive)) {
              newCompleted = [...prev.completedStages, prevActive];
            }
            return { ...prev, activeStage: newStage, completedStages: newCompleted };
          });
        }
        if (data?.line) {
          const stageLabel = data.stage_name ?? data.stage ?? "?";
          const suffix = data.status ? ` (${data.status})` : "";
          appendLog(`[Stage ${stageLabel}] ${data.line}${suffix}`);
        }
      });

      source.addEventListener("task_progress", (event) => {
        const data = parseJsonData<TaskProgress>(event as MessageEvent);
        if (!data) {
          return;
        }
        setState((prev) => {
          if (data.type === "task_done" && data.task_id) {
            const already = prev.completedTasks.some((t) => t.task_id === data.task_id);
            return {
              ...prev,
              taskProgress: data,
              completedTasks: already
                ? prev.completedTasks
                : [
                    ...prev.completedTasks,
                    { task_id: data.task_id, score: data.score ?? 0, steps: data.steps ?? 0 }
                  ]
            };
          }
          return { ...prev, taskProgress: data };
        });
      });

      source.addEventListener("proposal_update", (event) => {
        const data = parseJsonData<ProposalCycle>(event as MessageEvent);
        if (!data) {
          return;
        }
        setState((prev) => {
          const exists = prev.proposals.some(
            (proposal) =>
              proposal.timestamp === data.timestamp &&
              proposal.batch_idx === data.batch_idx &&
              proposal.action === data.action
          );
          if (exists) {
            return prev;
          }
          return {
            ...prev,
            proposals: [...prev.proposals, data],
            activeStage: null,
            completedStages: []
          };
        });
      });

      source.addEventListener("eval_result", (event) => {
        const data = parseJsonData<{ line?: string }>(event as MessageEvent);
        if (data?.line) {
          appendLog(`[Eval] ${data.line}`);
        }
      });

      source.addEventListener("done", (event) => {
        const data = parseJsonData<{
          session_id?: string;
          status?: "completed" | "failed";
          error?: string | null;
        }>(event as MessageEvent);
        const finalStatus = data?.status === "completed" ? "completed" : "failed";
        setState((prev) => ({
          ...prev,
          status: finalStatus,
          error: data?.error ?? null
        }));
        closeStream();
        if (data?.session_id) {
          callbacks.onDone?.(data.session_id, finalStatus);
        }
      });

      source.onerror = () => {
        appendLog("[stream] disconnected");
      };
    },
    [appendLog, callbacks, closeStream]
  );

  const startEvolve = useCallback(
    async (config: EvolveConfig) => {
      setState((prev) => ({ ...prev, status: "configuring", error: null, logs: [] }));
      try {
        const result = await apiClient.startEvolve(config);
        setState((prev) => ({
          ...prev,
          status: "running",
          sessionId: result.session_id,
          config,
          pid: result.pid,
          logs: [`Started evolve session ${result.session_id}`],
          currentBatch: 0,
          totalBatches: 0,
          elapsedSeconds: 0,
          error: null,
          proposals: [],
          activeStage: null,
          completedStages: [],
          taskProgress: null,
          completedTasks: []
        }));
        connect(result.session_id);
        return result.session_id;
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to start evolve";
        setState((prev) => ({ ...prev, status: "failed", error: message }));
        throw error;
      }
    },
    [connect]
  );

  const stopEvolve = useCallback(async () => {
    if (!state.sessionId) {
      return;
    }
    await apiClient.stopEvolve(state.sessionId);
    appendLog("Stop requested by user.");
  }, [appendLog, state.sessionId]);

  const loadStatus = useCallback(async (): Promise<EvolveStatus | null> => {
    if (!state.sessionId) {
      return null;
    }
    const status = await apiClient.getEvolveStatus(state.sessionId);
    setState((prev) => ({
      ...prev,
      status:
        status.status === "completed"
          ? "completed"
          : status.status === "failed"
            ? "failed"
            : "running",
      currentBatch: status.current_batch,
      totalBatches: status.total_batches,
      elapsedSeconds: status.elapsed_seconds,
      artifactCounts: status.artifact_counts ?? prev.artifactCounts,
      pid: status.pid,
      error: status.error ?? null
    }));
    return status;
  }, [state.sessionId]);

  const reset = useCallback(() => {
    closeStream();
    setState(INITIAL_STATE);
  }, [closeStream]);

  useEffect(() => () => closeStream(), [closeStream]);

  const isRunning = useMemo(() => state.status === "running", [state.status]);

  return {
    state,
    isRunning,
    startEvolve,
    stopEvolve,
    loadStatus,
    reset
  };
}
