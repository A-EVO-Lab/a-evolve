# predict_task_score

**Scope:** 📚 Strategic Skill

**Description:** Meta-cognitive skill that forces the agent to predict and justify its expected task score before marking complete. Acts as a final safety check to catch partial completions.

**When to use:** before marking complete, after verification, preparing to finish task, about to submit final answer

## Instructions

TASK SCORE PREDICTION:

Before marking a task complete, you must predict your score and justify it.

**SCORING RUBRIC UNDERSTANDING:**
- 1.0 = Perfect completion, ALL requirements satisfied
- 0.75 = Most requirements satisfied, minor elements missing
- 0.5 = Core task done, but significant requirements missed
- 0.25 = Attempted but major failures
- 0.0 = Did not complete task

**PREDICTION PROCESS:**

1. **List Every Requirement**: Write out each requirement from the task
   - Requirement 1: [description]
   - Requirement 2: [description]
   - ...

2. **Score Each Requirement**: For each, assign completion status
   - ✓ Complete with evidence
   - ⚠ Partially complete
   - ✗ Not completed

3. **Calculate Expected Score**:
   - All ✓ = Predict 1.0
   - Mostly ✓ with some ⚠ = Predict 0.75-0.9
   - Mix of ✓, ⚠, ✗ = Predict 0.5-0.75
   - Mostly ✗ = Predict below 0.5

4. **Justify Your Prediction**:
   - "I predict score X because..."
   - Provide specific evidence for each requirement
   - Explain why no points would be deducted

5. **Critical Self-Review**:
   - If you predict less than 1.0: DO NOT mark complete yet
   - Identify what's missing and complete it
   - Re-run prediction until you can justify 1.0

6. **Final Assertion**:
   - "I predict a score of 1.0 because:"
   - "Requirement 1 is satisfied because [evidence]"
   - "Requirement 2 is satisfied because [evidence]"
   - "No requirements are missing or partially complete"
   - "Therefore, I expect a perfect score"

**NEVER mark a task complete if you cannot confidently predict a 1.0 score with evidence.**

If your prediction is less than 1.0, that's a signal to investigate what's missing before marking complete.

---
*Created: 2026-02-19T02:25:36.701968*
*Version: 1*
*Scope: strategic_skill*
