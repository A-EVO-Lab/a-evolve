export type TrajectoryRole = "user" | "assistant" | "tool" | "system";

export type TrajectoryMessage = {
  role: TrajectoryRole;
  content: string;
  type?: string;
};

export type ErrorDetail = {
  type?: string;
  status_code?: number;
  app_name?: string;
  api_name?: string;
  name?: string;
  message?: string;
};

export type AppWorldObservation = {
  task_id: string;
  timestamp: string;
  score: number;
  task_completed: boolean;
  num_steps: number;
  trajectory: TrajectoryMessage[];
  api_calls?: string[];
  errors?: string[];
  error_details?: ErrorDetail[];
  experiment_name?: string;
  run_id?: number;
};
