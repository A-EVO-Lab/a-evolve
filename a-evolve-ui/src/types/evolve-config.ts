export type EvolveEnvironment = "appworld";

export type EvolveConfig = {
  environment: EvolveEnvironment;
  main_model: string;
  proposer_model: string;
  num_tasks: number;
  batch_size: number;
  num_test_tasks: number;
  on_policy: boolean;
  eval_vanilla: boolean;
  eval_evolved: boolean;
  api_key?: string;
};

export type EvolveStatus = {
  session_id: string;
  status: "running" | "completed" | "failed";
  pid: number | null;
  elapsed_seconds: number;
  current_batch: number;
  total_batches: number;
  return_code?: number | null;
  error?: string | null;
  artifact_counts?: {
    tools: number;
    skills: number;
    knowledge: number;
    patches: number;
  };
};

export const EVOLVE_MODEL_OPTIONS = [
  "claude-haiku-4-5-20251001",
  "claude-sonnet-4-20250514",
  "claude-sonnet-4-5-20250929",
  "gpt-5",
  "gpt-5-mini",
  "gemini-3-flash-preview"
] as const;
