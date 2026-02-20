import type { PipelineData } from "@/types/evolve";
import type { EvolveConfig, EvolveStatus } from "@/types/evolve-config";
import type { AppWorldObservation } from "@/types/observation";
import type {
  SessionEvaluationsResponse,
  SessionBatchResponse,
  SessionBatchesResponse,
  SessionListResponse,
  SessionSummary
} from "@/types/session";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000/api";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`API error (${response.status}): ${path}`);
  }
  return (await response.json()) as T;
}

function extractDetail(text: string): string {
  try {
    const json = JSON.parse(text) as { detail?: string };
    if (typeof json.detail === "string") {
      return json.detail;
    }
  } catch {
    // not JSON, use raw text
  }
  return text;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: body !== undefined ? JSON.stringify(body) : undefined
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(extractDetail(text) || `API error (${response.status}): ${path}`);
  }
  return (await response.json()) as T;
}

export const apiClient = {
  async getSessions(): Promise<SessionSummary[]> {
    const data = await get<SessionListResponse>("/sessions");
    return data.sessions;
  },

  async getSession(sessionId: string): Promise<SessionSummary> {
    return get<SessionSummary>(`/sessions/${sessionId}`);
  },

  async getBatches(sessionId: string) {
    return get<SessionBatchesResponse>(`/sessions/${sessionId}/batches`);
  },

  async getBatch(sessionId: string, batchId: string) {
    return get<SessionBatchResponse>(`/sessions/${sessionId}/batches/${batchId}`);
  },

  async getTask(sessionId: string, taskId: string) {
    return get<{
      session_id: string;
      task_id: string;
      batch_id: string;
      observation: AppWorldObservation;
      artifacts: {
        task_dir: string;
        evaluation_report: string | null;
        environment_io: string | null;
        api_calls_jsonl: string | null;
      };
    }>(`/sessions/${sessionId}/tasks/${taskId}`);
  },

  async getPipeline(sessionId: string, batchId: string) {
    return get<PipelineData>(`/sessions/${sessionId}/pipeline/${batchId}`);
  },

  async getEvaluations(sessionId: string) {
    return get<SessionEvaluationsResponse>(`/sessions/${sessionId}/evaluations`);
  },

  async getTools(sessionId: string) {
    return get<{ tools: Array<Record<string, unknown>> }>(
      `/sessions/${sessionId}/tools`
    );
  },

  async getSkills(sessionId: string) {
    return get<{ skills: Array<{ name: string; path: string; content: string }> }>(
      `/sessions/${sessionId}/skills`
    );
  },

  async getKnowledge(sessionId: string) {
    return get<{ knowledge: Array<Record<string, unknown>> }>(
      `/sessions/${sessionId}/knowledge`
    );
  },

  async startEvolve(config: EvolveConfig) {
    return post<{ session_id: string; status: string; pid: number; log_path: string }>(
      "/evolve/start",
      config
    );
  },

  async getEvolveStatus(sessionId: string) {
    return get<EvolveStatus>(`/evolve/${sessionId}/status`);
  },

  async stopEvolve(sessionId: string) {
    return post<{ session_id: string; status: string; message?: string }>(
      `/evolve/${sessionId}/stop`
    );
  },

  getEvolveStreamUrl(sessionId: string) {
    return `${API_BASE}/evolve/${sessionId}/stream`;
  }
};
