"""
Plan Module - Stage 2 of the Proposer Agent.

Generates a plan (to-do list) based on the diagnosis from Stage 1.
The plan contains specific actionable items that will be applied in Stage 3.
"""

import json
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum


class TodoStatus(Enum):
    """Status of a to-do item."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TodoItem:
    """A single to-do item in the plan."""
    id: str
    action: str  # CREATE_TOOL, EVOLVE_TOOL, ADD_SKILL, ADD_KNOWLEDGE, INDUCE_WORKFLOW
    description: str
    priority: int  # 1 = highest
    status: TodoStatus = TodoStatus.PENDING
    spec: Dict[str, Any] = field(default_factory=dict)  # tool_spec or patch_spec
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Convert to dict for serialization."""
        d = asdict(self)
        d["status"] = self.status.value
        return d
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'TodoItem':
        """Create from dict."""
        d = dict(d)
        d["status"] = TodoStatus(d.get("status", "pending"))
        return cls(**d)


class PlanModule:
    """
    Stage 2: Plan Generation.
    
    This module:
    1. Takes diagnosis from Stage 1
    2. Generates a plan (to-do list) with specific actions
    3. Each to-do item has specs for tools or patches
    4. Maintains plan history for tracking
    """
    
    PLAN_SYSTEM_PROMPT = """You are an AI improvement agent in the PLANNING stage.

Based on the diagnosis, you must create a PLAN with specific, actionable to-do items.

## Action Types

### Tool Actions
1. **CREATE_TOOL** (or CREATE_FEATURE_TOOL): Create a new tool
   - Provide complete `spec` with name, description, parameters, code, usage_prompt
   - Code must be valid Python starting with `def function_name(...)`

2. **EVOLVE_TOOL** (or EVOLVE_FEATURE_TOOL): Improve an existing tool
   - Provide `spec` with tool_id/name, new_code, new_usage_prompt
   - Read the existing tool first using read_tool_code

### Skill Actions
3. **ADD_SKILL**: Add a reusable skill (e.g., API discovery, authentication patterns)
   - Skills are step-by-step instructions for common tasks
   - Provide `spec` with name, description, instructions, triggers

4. **EVOLVE_SKILL**: Improve an existing skill
   - Provide `spec` with skill_name and new_instructions

### Knowledge Actions
5. **ADD_KNOWLEDGE**: Add factual knowledge (e.g., API endpoints, data formats)
   - Knowledge is reference information the agent can look up
   - Provide `spec` with type, topic, content, examples

6. **EVOLVE_KNOWLEDGE**: Update existing knowledge
   - Provide `spec` with knowledge_id and new_content

### Workflow Memory Actions (AWM Pattern)
7. **INDUCE_WORKFLOW**: Extract reusable workflow from successful task patterns
   - Use when you see 3+ successful tasks with similar execution patterns
   - Provide `spec` with name, description, triggers, and source_task_ids
   - The workflow will be stored as a skill with scope="workflow_memory"

## Skill Scopes

Skills can have three scopes:
- **strategic_skill**: Long-term, reusable skills (default)
- **tactical_patch**: Short-term, narrow behavioral fixes
- **workflow_memory**: Induced sub-routines from successful executions

## ⚠️ JSON SERIALIZATION REQUIREMENT (CRITICAL!)

**ALL tool return values MUST be JSON-serializable!**

The system uses `json.dumps()` to serialize tool outputs. Non-serializable 
objects will cause runtime crashes!

**Allowed types:**
- ✅ Primitives: `int`, `float`, `str`, `bool`, `None`
- ✅ Containers: `list`, `dict` (with serializable contents only)

**Forbidden types (will crash!):**
- ❌ `datetime` objects → Use `.strftime('%Y-%m-%d %H:%M:%S')` or `.isoformat()`
- ❌ Custom classes → Convert to dict

## Response Format

```json
{
  "plan_summary": "Brief description of what this plan will accomplish",
  "todos": [
    {
      "id": "todo_1",
      "action": "CREATE_TOOL",
      "description": "Create an API discovery tool",
      "priority": 1,
      "spec": {
        "name": "discover_apis",
        "description": "Discovers available AppWorld APIs",
        "parameters": {...},
        "code": "def discover_apis(...) -> dict:\\n    # Implementation...",
        "usage_prompt": "Use this tool when you need to find available APIs",
        "code_example": "# Example usage:\\nresult = discover_apis(category='payments')\\nprint(result['endpoints'])"
      }
    },
    {
      "id": "todo_2",
      "action": "ADD_SKILL",
      "description": "Add API authentication skill",
      "priority": 2,
      "spec": {
        "name": "api_authentication",
        "scope": "strategic_skill",
        "description": "How to authenticate with AppWorld APIs",
        "instructions": "Step 1: Get user credentials...\\nStep 2: Call login API...",
        "triggers": ["when authentication is needed", "after 401 error"]
      }
    },
    {
      "id": "todo_3",
      "action": "ADD_KNOWLEDGE",
      "description": "Add Venmo API endpoint knowledge",
      "priority": 3,
      "spec": {
        "type": "api_info",
        "topic": "venmo_endpoints",
        "content": "Venmo API endpoints:\\n- GET /users/{id}: Get user info\\n- POST /payments: Send payment",
        "examples": [{"endpoint": "/users/me", "response": {...}}]
      }
    },
    {
      "id": "todo_4",
      "action": "ADD_SKILL",
      "description": "Add tactical patch for error handling",
      "priority": 4,
      "spec": {
        "name": "error_handling_retry_policy",
        "scope": "tactical_patch",
        "description": "Agent needs guidance on handling API errors",
        "instructions": "When you receive a 401 error, re-authenticate before retrying...",
        "triggers": ["401 error", "authentication failed"],
        "ttl_episodes": 100
      }
    },
    {
      "id": "todo_5",
      "action": "INDUCE_WORKFLOW",
      "description": "Extract login-paginate workflow from successful tasks",
      "priority": 5,
      "spec": {
        "name": "workflow_login_paginate_filter",
        "description": "Common pattern: login to app, paginate API results, filter by criteria",
        "triggers": ["need to login", "paginate", "filter items"],
        "source_task_ids": ["task_001", "task_005", "task_012"]
      }
    }
  ],
  "reasoning": "Why this plan will address the identified issues"
}
```
"""

    def __init__(self, verbose: bool = True):
        """
        Initialize the plan module.
        
        Args:
            verbose: Whether to print debug output
        """
        self.verbose = verbose
        self.current_plan: List[TodoItem] = []
        self.plan_history: List[Dict] = []
    
    def run(
        self,
        agent,
        diagnosis: Dict[str, Any],
        context: Dict[str, Any],
        observations: Optional[List[Dict]] = None
    ) -> List[TodoItem]:
        """
        Run Stage 2: Plan Generation.
        
        Args:
            agent: The LLM agent to use for reasoning
            diagnosis: Diagnosis report from Stage 1
            context: Shared context
            observations: Current batch observations (for examples)
            
        Returns:
            List of TodoItem objects
        """
        if self.verbose:
            print("\n[Plan] 📋 Stage 2: Generating Plan...")
        
        # Build planning prompt
        prompt = self._build_plan_prompt(diagnosis, context, observations)
        
        # Call LLM to generate plan
        response = agent.call(prompt)
        parsed = self._parse_response(response)
        
        # Convert to TodoItems
        todos = self._create_todos(parsed, diagnosis)
        
        # Store current plan
        self.current_plan = todos
        
        # Record in history
        self.plan_history.append({
            "timestamp": datetime.now().isoformat(),
            "diagnosis_summary": diagnosis.get("pattern_description", ""),
            "recommended_action": diagnosis.get("recommended_action", ""),
            "todos_created": len(todos),
            "plan_summary": parsed.get("plan_summary", "")
        })
        
        if self.verbose:
            print(f"[Plan]   Created {len(todos)} to-do items:")
            for todo in todos:
                print(f"[Plan]     - [{todo.priority}] {todo.action}: {todo.description[:60]}...")
        
        return todos
    
    def run_repair_plan(
        self,
        agent,
        repair_diagnosis: Dict[str, Any],
        original_todos: List[TodoItem],
        context: Dict[str, Any]
    ) -> List[TodoItem]:
        """
        Generate a repair plan after a failed verification.
        
        Args:
            agent: The LLM agent
            repair_diagnosis: Diagnosis of what went wrong
            original_todos: The original todos that failed
            context: Shared context
            
        Returns:
            List of repair TodoItems
        """
        if self.verbose:
            print("\n[Plan] 🔧 Generating repair plan...")
        
        # Format original todos for context
        original_specs = []
        for todo in original_todos:
            original_specs.append({
                "action": todo.action,
                "description": todo.description,
                "spec": todo.spec
            })
        
        # Detect specific errors for targeted guidance
        error_type = repair_diagnosis.get("error_type", "unknown")
        error_desc = repair_diagnosis.get("error_description", "N/A")
        
        # Provide specific fix guidance based on error type
        fix_guidance = ""
        if "skill" in error_desc.lower() or "patch" in error_desc.lower():
            fix_guidance = """
⚠️ **SPECIFIC FIX REQUIRED**: Use ADD_SKILL with scope field:
```json
{
  "action": "ADD_SKILL",
  "spec": {
    "name": "fix_name",
    "scope": "tactical_patch",
    "description": "Why this guidance will help",
    "instructions": "The actual guidance text",
    "triggers": ["error pattern"]
  }
}
```
"""
        elif "tool_id" in error_desc.lower() or "name" in error_desc.lower():
            fix_guidance = """
⚠️ **SPECIFIC FIX REQUIRED**: The EVOLVE_TOOL failed because of missing identifiers.
You MUST include both tool_id and name in the spec:
```json
{
  "action": "EVOLVE_TOOL",
  "spec": {
    "tool_id": "existing_tool_name",
    "name": "existing_tool_name",
    "new_code": "...",
    "new_usage_prompt": "..."
  }
}
```
"""
        
        prompt = f"""{self.PLAN_SYSTEM_PROMPT}

## Repair Context

This is a REPAIR plan. The original plan failed with this error:

**Error Type:** {error_type}
**Error Description:** {error_desc}
**Repair Strategy:** {repair_diagnosis.get("repair_strategy", "N/A")}
{fix_guidance}

## Original Failed Plan

```json
{json.dumps(original_specs, indent=2)[:2000]}
```

## Your Task

Create a REPAIR plan that fixes the issues identified above.
- If it was a syntax error, fix the code
- If it was an interface mismatch, update parameters/field names
- If you need tactical guidance, use ADD_SKILL with scope=tactical_patch
- If EVOLVE_TOOL failed, include both tool_id and name
- Keep the same general approach but fix the specific issues

Respond with JSON in the same format as a normal plan.
"""
        
        response = agent.call(prompt)
        parsed = self._parse_response(response)
        
        # Create repair todos with special IDs
        todos = self._create_todos(parsed, repair_diagnosis, prefix="repair")
        
        if self.verbose:
            print(f"[Plan]   Created {len(todos)} repair to-do items")
        
        return todos
    
    def _build_plan_prompt(
        self,
        diagnosis: Dict[str, Any],
        context: Dict[str, Any],
        observations: Optional[List[Dict]]
    ) -> str:
        """Build the planning prompt."""
        primary_cause = diagnosis.get("primary_cause", "unknown")
        tool_issue = diagnosis.get("diagnosis", {}).get("tool_issue", {})
        skill_issue = diagnosis.get("diagnosis", {}).get("skill_issue", {})
        knowledge_issue = diagnosis.get("diagnosis", {}).get("knowledge_issue", {})
        prompt_issue = diagnosis.get("diagnosis", {}).get("prompt_issue", {}) or diagnosis.get("diagnosis", {}).get("behavioral_issue", {})
        
        # Format recommended actions
        recommended_actions = diagnosis.get("recommended_actions", [])
        if recommended_actions:
            actions_str = ", ".join([a.get("action", "SKIP") for a in recommended_actions])
        else:
            actions_str = diagnosis.get("recommended_action", "SKIP")
        
        # Get sample input for tool design
        sample_input = ""
        if observations and len(observations) > 0:
            obs_input = observations[0].get("input", "")
            if isinstance(obs_input, str):
                sample_input = obs_input[:500]
            else:
                sample_input = json.dumps(obs_input, default=str)[:500]
        
        prompt = f"""{self.PLAN_SYSTEM_PROMPT}

## Diagnosis Summary

- **Error Frequency:** {diagnosis.get("error_frequency", "unknown")}
- **Problematic Fields:** {diagnosis.get("most_problematic_fields", [])}
- **Pattern:** {diagnosis.get("pattern_description", "N/A")}
- **Primary Cause:** {primary_cause}
- **Recommended Actions:** {actions_str}
- **Reasoning:** {diagnosis.get("reasoning", "N/A")}

## Issue Analysis

### Tool Issues
- Exists: {tool_issue.get("exists", False)}
- Description: {tool_issue.get("description", "None")}
- Tool Name: {tool_issue.get("tool_name", "None")}

### Skill Issues
- Exists: {skill_issue.get("exists", False)}
- Description: {skill_issue.get("description", "None")}

### Knowledge Issues
- Exists: {knowledge_issue.get("exists", False)}
- Description: {knowledge_issue.get("description", "None")}

### Behavioral Issues
- Exists: {prompt_issue.get("exists", False)}
- Description: {prompt_issue.get("description", "None")}

## Sample Input Data

```
{sample_input}
```

## Current Context

- Existing tools: {context.get("num_tools", 0)}
- Existing skills: {context.get("num_skills", 0)}
- Existing knowledge: {context.get("num_knowledge", 0)}
- Existing patches: {context.get("num_patches", 0)}

## Your Task

Generate a PLAN with specific to-do items to address the diagnosis.
{"⚠️ NO TOOLS EXIST - you must CREATE first, not EVOLVE!" if context.get("num_tools", 0) == 0 else ""}
{"⚠️ NO SKILLS EXIST - consider ADD_SKILL!" if context.get("num_skills", 0) == 0 else ""}

Each to-do MUST include complete specs that can be directly applied:
- For tools: Include full code, parameters, imports, usage_prompt, AND code_example
- For skills: Include name, description, instructions, triggers
- For knowledge: Include type, topic, content, examples
- For patches: Include 'hypothesis' and 'patch' fields in spec

Respond with JSON plan:
"""
        
        return prompt
    
    def _create_todos(
        self,
        parsed: Dict[str, Any],
        diagnosis: Dict[str, Any],
        prefix: str = "todo"
    ) -> List[TodoItem]:
        """Create TodoItem objects from parsed plan."""
        todos = []
        
        raw_todos = parsed.get("todos", [])
        
        # If no todos but we have recommended actions, create todos from them
        if not raw_todos:
            # First try new format: recommended_actions (list)
            recommended_actions = diagnosis.get("recommended_actions", [])
            if recommended_actions:
                for i, rec_action in enumerate(recommended_actions, 1):
                    action = rec_action.get("action", "SKIP")
                    if action == "SKIP":
                        continue
                    raw_todos.append({
                        "id": f"{prefix}_{i}",
                        "action": action,
                        "priority": rec_action.get("priority", i),
                        "description": rec_action.get("description", f"Apply {action} as recommended"),
                        "target": rec_action.get("target", ""),
                        "spec": {}
                    })
            # Fallback to legacy format: recommended_action (single)
            elif diagnosis.get("recommended_action") not in ["SKIP", None]:
                action = diagnosis.get("recommended_action", "SKIP")
                raw_todos = [{
                    "id": f"{prefix}_1",
                    "action": action,
                    "description": f"Apply {action} as recommended",
                    "priority": 1,
                    "spec": {}
                }]
        
        for i, raw in enumerate(raw_todos, 1):
            todo_id = raw.get("id", f"{prefix}_{i}")
            
            # Extract spec based on action type
            spec = raw.get("spec", {})
            if not spec:
                # Try alternative locations based on action type
                action = raw.get("action", "")
                # if action in ["CREATE_TOOL", "CREATE_FEATURE_TOOL", "EVOLVE_TOOL", "EVOLVE_FEATURE_TOOL"]:
                if action in ["CREATE_TOOL", "EVOLVE_TOOL"]:
                    spec = raw.get("tool_spec", {})
                elif action in ["ADD_SKILL", "EVOLVE_SKILL"]:
                    spec = raw.get("skill_spec", {})
                elif action in ["ADD_KNOWLEDGE", "EVOLVE_KNOWLEDGE"]:
                    spec = raw.get("knowledge_spec", {})
                elif action == "INDUCE_WORKFLOW":
                    spec = raw.get("workflow_spec", raw.get("spec", {}))
                    spec["scope"] = "workflow_memory"  # Ensure scope is set
                # Note: ADD_PATCH is deprecated - use ADD_SKILL with scope="tactical_patch"
                elif action == "ADD_PATCH":
                    # Backward compatibility: convert patch to skill
                    spec = raw.get("patch_spec", raw.get("spec", {}))
                    spec["scope"] = "tactical_patch"
            
            todo = TodoItem(
                id=todo_id,
                action=raw.get("action", "SKIP"),
                description=raw.get("description", ""),
                priority=raw.get("priority", i),
                status=TodoStatus.PENDING,
                spec=spec
            )
            todos.append(todo)
        
        # Sort by priority
        todos.sort(key=lambda t: t.priority)
        
        return todos
    
    def _parse_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM response."""
        # Try parsing as pure JSON
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        
        # Try extracting from code block
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # Try finding any JSON
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        return {"error": "Could not parse", "raw": response[:500], "todos": []}
    
    def get_current_plan(self) -> List[TodoItem]:
        """Get the current plan."""
        return self.current_plan
    
    def get_plan_summary(self) -> Dict[str, Any]:
        """Get summary of current plan status."""
        if not self.current_plan:
            return {"total": 0, "pending": 0, "completed": 0, "failed": 0}
        
        return {
            "total": len(self.current_plan),
            "pending": sum(1 for t in self.current_plan if t.status == TodoStatus.PENDING),
            "in_progress": sum(1 for t in self.current_plan if t.status == TodoStatus.IN_PROGRESS),
            "completed": sum(1 for t in self.current_plan if t.status == TodoStatus.COMPLETED),
            "failed": sum(1 for t in self.current_plan if t.status == TodoStatus.FAILED),
            "items": [t.to_dict() for t in self.current_plan]
        }
    
    def update_todo_status(
        self,
        todo_id: str,
        status: TodoStatus,
        error_message: Optional[str] = None
    ):
        """Update the status of a todo item."""
        for todo in self.current_plan:
            if todo.id == todo_id:
                todo.status = status
                if status == TodoStatus.COMPLETED:
                    todo.completed_at = datetime.now().isoformat()
                if error_message:
                    todo.error_message = error_message
                break
    
    def save_plan(self, filepath: str):
        """Save current plan to file."""
        import os
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        data = {
            "current_plan": [t.to_dict() for t in self.current_plan],
            "history": self.plan_history
        }
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
    
    def load_plan(self, filepath: str):
        """Load plan from file."""
        import os
        if not os.path.exists(filepath):
            return
        
        with open(filepath, 'r') as f:
            data = json.load(f)
        
        self.current_plan = [TodoItem.from_dict(t) for t in data.get("current_plan", [])]
        self.plan_history = data.get("history", [])

