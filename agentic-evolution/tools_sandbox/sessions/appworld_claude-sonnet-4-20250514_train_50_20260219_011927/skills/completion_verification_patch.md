# completion_verification_patch

**Scope:** 🎯 Tactical Patch

**Description:** Immediate behavioral correction: Never mark a task complete without running the pre-completion audit and score prediction. This patch enforces the new verification workflow.

**When to use:** about to mark complete, thinking task is done, preparing final answer, ready to finish

## Instructions

MANDATORY PRE-COMPLETION PROTOCOL:

When you think a task is complete, you MUST:

1. STOP - Do not mark complete yet
2. RUN pre_completion_audit skill
3. RUN predict_task_score skill
4. VERIFY score prediction is 1.0 with evidence
5. ONLY THEN mark task complete

This is not optional. This is a mandatory workflow.

**Specific Behavior Change:**
- OLD: "I've followed the artists, task complete"
- NEW: "I've followed the artists. Before marking complete, let me run the pre-completion audit..."

**Enforcement:**
If you catch yourself about to mark a task complete without running these checks, immediately:
- Pause
- Acknowledge: "I almost marked this complete without proper verification"
- Run the audit and prediction
- Then proceed only if justified

This patch remains active until the behavior becomes automatic.

---
*Created: 2026-02-19T02:25:36.725604*
*Version: 1*
*Scope: tactical_patch*
*TTL: 100 episodes*
