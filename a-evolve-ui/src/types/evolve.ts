import type { ProposalCycle } from "@/types/proposal";

export type StageHistoryItem = {
  stage: number | string;
  name: string;
  result: Record<string, unknown>;
};

export type PipelineData = {
  session_id: string;
  batch_id: string;
  stage_history: StageHistoryItem[];
  proposal_cycles?: ProposalCycle[];
  version_snapshots: Array<{
    version_id: string;
    timestamp?: string;
    description?: string;
    is_committed?: boolean;
    is_reverted?: boolean;
  }>;
  has_explicit_stage_history: boolean;
};
