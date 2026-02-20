export type StageStatus = "pending" | "active" | "completed" | "failed" | "skipped";

export type StageHistoryItem = {
  stage: number | string;
  name: string;
  result: Record<string, unknown>;
};

export type ProposalCycle = {
  batch_idx: number;
  batch_id?: string;
  timestamp: string;
  action: string;
  reasoning: string;
  validation_passed: boolean;
  committed?: boolean;
  stage_history: StageHistoryItem[];
};
