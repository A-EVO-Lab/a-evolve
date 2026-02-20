import type { AppWorldObservation } from "./observation";

export type BatchAccuracy = {
  batch_id: number;
  accuracy: number;
  num_samples?: number;
  num_correct?: number;
};

export type SessionStats = {
  total_samples?: number;
  total_batches?: number;
  batch_accuracies?: BatchAccuracy[];
  field_accuracies_over_time?: Record<string, Record<string, number>>;
  error_type_counts?: Record<string, number>;
  created_at?: string;
};

export type SessionResults = {
  session_id?: string;
  model?: string;
  training_stats?: Record<string, unknown>;
  evolution_stats?: Record<string, unknown>;
  vanilla_results?: Record<string, unknown>;
  evolved_results?: Record<string, unknown>;
  improvement?: Record<string, unknown>;
  timestamp?: string;
};

export type SessionSummary = {
  id: string;
  path: string;
  created_at: string;
  updated_at: string;
  summary_stats: SessionStats;
  results: SessionResults | null;
  counts: {
    batches: number;
    tools: number;
    skills: number;
    knowledge: number;
    patches: number;
  };
};

export type BatchSummary = {
  id: string;
  file: string;
  num_tasks: number;
  average_score: number;
  updated_at: string;
};

export type BatchData = {
  session_id: string;
  batch_id: string;
  observations: AppWorldObservation[];
};

export type SessionListResponse = {
  sessions: SessionSummary[];
};

export type SessionBatchesResponse = {
  session_id: string;
  batches: BatchSummary[];
};

export type SessionBatchResponse = {
  session_id: string;
  batch_id: string;
  observations: AppWorldObservation[];
};

export type EvaluationTaskResult = {
  task_id: string;
  score: number;
  num_passed_tests: number;
  num_failed_tests: number;
  num_total_tests: number;
  report: string | null;
};

export type EvaluationVariant = {
  name: "vanilla" | "evolved" | string;
  session_id: string;
  path: string | null;
  tasks: EvaluationTaskResult[];
  summary: {
    num_tasks: number;
    avg_score: number;
    total_passed_tests: number;
    total_failed_tests: number;
    total_tests: number;
  };
};

export type EvaluationComparisonItem = {
  task_id: string;
  vanilla_score: number | null;
  evolved_score: number | null;
  delta: number | null;
};

export type SessionEvaluationsResponse = {
  session_id: string;
  vanilla: EvaluationVariant;
  evolved: EvaluationVariant;
  comparison: EvaluationComparisonItem[];
};
