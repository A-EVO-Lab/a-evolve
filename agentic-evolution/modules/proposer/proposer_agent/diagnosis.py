"""
Diagnosis Module - Stage 1 of the Proposer Agent.

Analyzes errors in the current batch, uses analysis tools to understand
patterns, and determines the root cause of issues.
"""

import json
import re
from typing import List, Dict, Any, Optional
from ..analysis_tools import ProposerAnalysisTools


class DiagnosisModule:
    """
    Stage 1: Deep Error Analysis.
    
    This module:
    1. Analyzes current batch observations
    2. Uses analysis tools to search historical patterns
    3. Identifies root causes (TOOL issue vs PROMPT issue)
    4. Returns a diagnosis report for the Plan module
    """
    
    DIAGNOSIS_SYSTEM_PROMPT = """You are an AI improvement agent in the DIAGNOSIS stage.

Your role is to analyze errors and identify their root causes. You must determine:
1. Are these errors COMMON (persistent across batches) or RARE (new)?
2. Is this a TOOL issue (tools returning wrong data) or PROMPT issue (missing behavioral guidance)?
3. What specific patterns or fields are most problematic?

## Analysis Tools Available

You have tools to investigate the learning history:

### `analyze_error_patterns(min_occurrences=3, min_batches=2)`
Find recurring error patterns across multiple batches.

### `grep_observations(pattern, max_results=50)`
Search historical logs for specific patterns.

### `read_batch_file(batch_id)`
Read observations from a specific batch.

### `analyze_tool_usage_in_batch(batch_id)`
See if tools are returning useful data.

### `read_tool_code(tool_name)`
Read actual tool implementation.

## Response Format

You MUST respond with valid JSON in this format:
```json
{
  "tool_calls": [
    {"function": "analyze_error_patterns", "args": {"min_occurrences": 3, "min_batches": 2}}
  ]
}
```

After receiving tool results, provide your diagnosis:
```json
{
  "error_frequency": "common|rare|mixed",
  "most_problematic_fields": ["field1", "field2"],
  "pattern_description": "Description of the error pattern",
  "is_new_problem": true|false,
  "affected_batches": [1, 2, 3],
  "diagnosis": {
    "tool_issue": {
      "exists": true|false,
      "description": "What's wrong with the tool",
      "tool_name": "affected tool name or null",
      "severity": "critical|moderate|minor"
    },
    "skill_issue": {
      "exists": true|false,
      "description": "What skill is missing or broken"
    },
    "knowledge_issue": {
      "exists": true|false,
      "description": "What knowledge is missing"
    },
    "prompt_issue": {
      "exists": true|false,
      "description": "What guidance is missing",
      "missing_patterns": ["pattern1", "pattern2"]
    }
  },
  "primary_cause": "TOOL|PROMPT|BOTH|NEED_NEW_TOOL|MISSING_SKILL|MISSING_KNOWLEDGE",
  "recommended_actions": [
    {
      "action": "CREATE_TOOL|EVOLVE_TOOL|ADD_SKILL|EVOLVE_SKILL|ADD_KNOWLEDGE|EVOLVE_KNOWLEDGE|SKIP",
      "priority": 1,
      "target": "name of tool/skill/knowledge",
      "description": "What this action should accomplish"
    }
  ],
  "reasoning": "Why this is the root cause"
}
```
"""

    def __init__(self, workspace_dir: str, verbose: bool = True):
        """
        Initialize the diagnosis module.
        
        Args:
            workspace_dir: Directory where observations are stored
            verbose: Whether to print debug output
        """
        self.workspace_dir = workspace_dir
        self.verbose = verbose
        self.analysis_tools = ProposerAnalysisTools(workspace_dir)
    
    def run(
        self,
        agent,
        observations: List[Dict],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Run Stage 1: Diagnosis.
        
        Args:
            agent: The LLM agent to use for reasoning
            observations: Current batch observations
            context: Shared context including tool_registry, patches, etc.
            
        Returns:
            Diagnosis report dict with:
            - error_frequency
            - most_problematic_fields
            - diagnosis (tool_issue, prompt_issue)
            - primary_cause
            - recommended_action
            - tool_results
        """
        if self.verbose:
            print("\n[Diagnosis] 🔍 Stage 1: Deep Error Analysis...")
        
        # Build the analysis prompt
        prompt = self._build_analysis_prompt(observations, context)
        
        # Call LLM to request tool calls
        response1 = agent.call(prompt)
        parsed1 = self._parse_response(response1)
        
        # Execute tool calls
        tool_results = []
        if parsed1.get("tool_calls"):
            tool_results = self._execute_tool_calls(parsed1["tool_calls"])
            if self.verbose:
                print(f"[Diagnosis]   Executed {len(tool_results)} tool calls")
        
        # Second call - analyze tool results and make diagnosis
        diagnosis_prompt = self._build_diagnosis_prompt(
            observations, context, tool_results
        )
        response2 = agent.call(diagnosis_prompt)
        result = self._parse_response(response2)
        
        # Ensure required fields exist
        result = self._normalize_diagnosis(result)
        result["tool_results"] = tool_results
        result["_raw_responses"] = [response1, response2]
        
        if self.verbose:
            print(f"[Diagnosis]   Error frequency: {result.get('error_frequency', 'unknown')}")
            print(f"[Diagnosis]   Primary cause: {result.get('primary_cause', 'unknown')}")
            print(f"[Diagnosis]   Recommended: {result.get('recommended_action', 'unknown')}")
        
        return result
    
    def run_repair_diagnosis(
        self,
        agent,
        observations: List[Dict],
        context: Dict[str, Any],
        error_log: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Run repair diagnosis after a failed verification.
        
        Args:
            agent: The LLM agent to use
            observations: Current batch observations
            context: Shared context
            error_log: Error log from verifier
            
        Returns:
            Repair diagnosis report
        """
        if self.verbose:
            print("\n[Diagnosis] 🔧 Running repair diagnosis...")
        
        # Detect common error patterns for better guidance
        error_str = json.dumps(error_log, indent=2, default=str)[:3000]
        
        # Specific guidance for common errors
        common_error_hints = ""
        if "missing 'patch'" in error_str.lower() or "patch_text" in error_str.lower():
            common_error_hints = """
⚠️ **COMMON ERROR DETECTED: Skill/Patch field naming issue**
Use ADD_SKILL with scope=tactical_patch instead of legacy ADD_PATCH.
FIX: Use `"spec": {"name": "...", "scope": "tactical_patch", "instructions": "..."}`
"""
        elif "missing 'tool_id'" in error_str.lower() or "missing 'name'" in error_str.lower():
            common_error_hints = """
⚠️ **COMMON ERROR DETECTED: Tool identifier missing**
The EVOLVE_FEATURE_TOOL failed because tool_id or name was not provided.
FIX: Include both `"tool_id": "existing_tool_name"` and `"name": "existing_tool_name"` in spec.
"""
        
        prompt = f"""You are analyzing a FAILED attempt to improve an AI agent.

## Error from Previous Attempt

The previous improvement attempt failed with these errors:
```
{error_str}
```
{common_error_hints}

## Current Agent Configuration

Tools registered: {context.get('num_tools', 0)}
Patches applied: {context.get('num_patches', 0)}

## Your Task

Diagnose what went wrong and suggest a repair. Focus on:
1. Syntax errors in generated code
2. Tool interface mismatches (wrong field names in spec)
3. Missing imports or dependencies
4. Logic errors in the implementation

**IMPORTANT**: Use ADD_SKILL with scope="tactical_patch" for behavioral guidance.
If an EVOLVE_TOOL failed due to missing identifiers, include both tool_id and name.

Respond with JSON:
```json
{{
  "error_type": "syntax_error|interface_mismatch|missing_import|logic_error|other",
  "error_description": "What specifically went wrong",
  "repair_strategy": "How to fix it",
  "recommended_action": "CREATE_TOOL|EVOLVE_TOOL|ADD_SKILL|ADD_KNOWLEDGE|SKIP",
  "reasoning": "Why this repair should work"
}}
```
"""
        
        response = agent.call(prompt)
        result = self._parse_response(response)
        
        # Mark as repair diagnosis
        result["is_repair"] = True
        result["original_error"] = error_log
        
        return result
    
    def _build_analysis_prompt(
        self,
        observations: List[Dict],
        context: Dict[str, Any]
    ) -> str:
        """Build the initial analysis prompt."""
        batch_id = observations[0].get("batch_id", 0) if observations else 0
        accuracy = context.get("current_batch_accuracy", 0)
        num_correct = context.get("current_batch_correct", 0)
        num_tools = context.get("num_tools", 0)
        num_patches = context.get("num_patches", 0)
        
        # Format current batch summary
        batch_summary = self._format_batch_summary(observations)
        
        prompt = f"""{self.DIAGNOSIS_SYSTEM_PROMPT}

## Current Situation

- Batch {batch_id}: {accuracy:.1%} accuracy ({num_correct}/{len(observations)} correct)
- Total batches completed: {context.get('num_batches', 0)}
- Existing tools: {num_tools}, patches: {num_patches}

## Current Batch Error Summary

{batch_summary}

## Your Task

First, use tools to investigate:
1. Are these errors COMMON (appear across batches) or RARE (specific to this batch)?
2. What fields are most problematic?

Respond with JSON requesting tool calls:
```json
{{
  "tool_calls": [
    {{"function": "analyze_error_patterns", "args": {{"min_occurrences": 3, "min_batches": 2}}}}
  ]
}}
```
"""
        return prompt
    
    def _build_diagnosis_prompt(
        self,
        observations: List[Dict],
        context: Dict[str, Any],
        tool_results: List[Dict]
    ) -> str:
        """Build the diagnosis prompt with tool results."""
        batch_id = observations[0].get("batch_id", 0) if observations else 0
        num_tools = context.get("num_tools", 0)
        
        # Format tool execution feedback
        tool_feedback = self._format_tool_execution_feedback(observations)
        
        # Get list of agent's feature tools from registry (if available)
        tool_registry = context.get("tool_registry")
        agent_tools_list = []
        if tool_registry:
            for tool in tool_registry.list_tools():
                agent_tools_list.append({
                    "name": tool.get("name"),
                    "id": tool.get("id"),
                    "description": tool.get("description", "")[:100]
                })
        
        agent_tools_section = ""
        if agent_tools_list:
            agent_tools_section = f"""
## Agent's Feature Tools (These can be EVOLVED)

**⚠️ IMPORTANT: Only these tools can be evolved. Do NOT confuse with proposer analysis tools!**

{json.dumps(agent_tools_list, indent=2)}
"""
        else:
            agent_tools_section = """
## Agent's Feature Tools

**No feature tools registered yet. Use CREATE_FEATURE_TOOL to create one.**
"""
        
        prompt = f"""## Tool Investigation Results

{json.dumps(tool_results, indent=2, default=str)[:4000]}

## Tool Execution in Current Batch

{tool_feedback}
{agent_tools_section}
## Current Setup

- Agent Feature Tools registered: {num_tools}
- Behavioral Patches: {context.get('num_patches', 0)}

**Note:** The analysis tools you used above (analyze_error_patterns, grep_observations, etc.) are PROPOSER tools, NOT agent tools. When recommending EVOLVE_FEATURE_TOOL, specify a tool from the "Agent's Feature Tools" list above.

Based on these results, provide your DIAGNOSIS:

**⚠️ IMPORTANT ACTION RULES:**
- `CREATE_TOOL`: When NO tools exist OR need a completely new tool
- `EVOLVE_TOOL`: ONLY when tools already exist AND they have bugs to fix
- `ADD_SKILL`: Add skills (use scope=tactical_patch for behavioral guidance)
- `SKIP`: When accuracy is >80% and stable

{"⚠️ Currently NO tools exist - you must CREATE first, not EVOLVE!" if num_tools == 0 else "EVOLVE is available since tools exist."}

Respond with complete diagnosis JSON:
```json
{{
  "error_frequency": "common|rare|mixed",
  "most_problematic_fields": ["field1", "field2"],
  "pattern_description": "Description of error pattern",
  "is_new_problem": true|false,
  "diagnosis": {{
    "tool_issue": {{
      "exists": true|false,
      "description": "What's wrong",
      "tool_name": "name or null",
      "severity": "critical|moderate|minor"
    }},
    "prompt_issue": {{
      "exists": true|false,
      "description": "What guidance is missing",
      "missing_patterns": []
    }}
  }},
  "primary_cause": "TOOL|PROMPT|BOTH|NEED_NEW_TOOL|MISSING_SKILL|MISSING_KNOWLEDGE",
  "recommended_action": "CREATE_TOOL|EVOLVE_TOOL|ADD_SKILL|ADD_KNOWLEDGE|SKIP",
  "reasoning": "Why this action"
}}
```
"""
        return prompt
    
    def _format_batch_summary(self, observations: List[Dict]) -> str:
        """Format current batch observations summary."""
        lines = []
        
        # Show sample errors
        errors = [o for o in observations if not o.get("is_correct", True)]
        
        if errors:
            lines.append(f"**Errors: {len(errors)}/{len(observations)} samples incorrect**\n")
            
            for i, obs in enumerate(errors[:5], 1):
                pred = obs.get("prediction", {})
                truth = obs.get("ground_truth", {})
                lines.append(f"Error {i}:")
                lines.append(f"  Predicted: {json.dumps(pred, default=str)[:150]}")
                lines.append(f"  Expected:  {json.dumps(truth, default=str)[:150]}")
        
        # Field error summary
        field_errors = {}
        for obs in observations:
            fc = obs.get("field_correctness", {})
            for field, correct in fc.items():
                if not correct:
                    field_errors[field] = field_errors.get(field, 0) + 1
        
        if field_errors:
            lines.append("\n**Most problematic fields:**")
            sorted_errors = sorted(field_errors.items(), key=lambda x: x[1], reverse=True)
            for field, count in sorted_errors[:5]:
                lines.append(f"  - {field}: {count} errors ({count/len(observations)*100:.0f}%)")
        
        return "\n".join(lines) if lines else "No errors in this batch."
    
    def _format_tool_execution_feedback(self, observations: List[Dict]) -> str:
        """Format tool execution feedback from observations."""
        tool_stats = {}
        
        for obs in observations:
            exec_trace = obs.get("execution_trace", {})
            for tc in exec_trace.get("tool_calls", []):
                name = tc.get("tool_name", "unknown")
                result = tc.get("result", {})
                
                if name not in tool_stats:
                    tool_stats[name] = {"calls": 0, "empty": 0, "errors": 0}
                
                tool_stats[name]["calls"] += 1
                
                if not result or result == {} or (isinstance(result, dict) and "error" in result):
                    tool_stats[name]["empty"] += 1
                
                if isinstance(result, dict) and "error" in result:
                    tool_stats[name]["errors"] += 1
        
        if not tool_stats:
            return "No tools were called in this batch."
        
        lines = []
        for name, stats in tool_stats.items():
            empty_pct = stats["empty"] / stats["calls"] * 100 if stats["calls"] else 0
            status = "✅" if empty_pct < 20 else "⚠️" if empty_pct < 80 else "🔴"
            lines.append(f"{status} `{name}`: {stats['calls']} calls, {stats['empty']} empty ({empty_pct:.0f}%)")
        
        return "\n".join(lines)
    
    def _execute_tool_calls(self, tool_calls: List[Dict]) -> List[Dict]:
        """Execute tool calls using analysis tools."""
        results = []
        
        for tc in tool_calls:
            func_name = tc.get("function", "")
            args = tc.get("args", {})
            
            try:
                if func_name == "grep_observations":
                    result = self.analysis_tools.grep_observations(**args)
                elif func_name == "read_batch_file":
                    result = self.analysis_tools.read_batch_file(**args)
                elif func_name == "compute_statistics":
                    result = self.analysis_tools.compute_statistics(**args)
                elif func_name == "analyze_error_patterns":
                    result = self.analysis_tools.analyze_error_patterns(**args)
                elif func_name == "read_tool_code":
                    result = self.analysis_tools.read_tool_code(**args)
                elif func_name == "analyze_tool_usage_in_batch":
                    result = self.analysis_tools.analyze_tool_usage_in_batch(**args)
                elif func_name == "test_tool_execution":
                    result = self.analysis_tools.test_tool_execution(**args)
                else:
                    result = {"error": f"Unknown function: {func_name}"}
                
                results.append({"function": func_name, "args": args, "result": result})
                
            except Exception as e:
                results.append({"function": func_name, "args": args, "error": str(e)})
        
        return results
    
    def _parse_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM response into dict."""
        # Try parsing as pure JSON
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        
        # Try extracting from markdown code block
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # Try finding any JSON object
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        return {"error": "Could not parse response", "raw": response[:500]}
    
    def _normalize_diagnosis(self, result: Dict) -> Dict:
        """Ensure diagnosis has all required fields."""
        defaults = {
            "error_frequency": "unknown",
            "most_problematic_fields": [],
            "pattern_description": "",
            "is_new_problem": True,
            "diagnosis": {
                "tool_issue": {"exists": False, "description": "", "tool_name": None, "severity": "minor"},
                "prompt_issue": {"exists": False, "description": "", "missing_patterns": []}
            },
            "primary_cause": "NEED_NEW_TOOL",
            "recommended_action": "SKIP",
            "reasoning": ""
        }
        
        for key, default in defaults.items():
            if key not in result:
                result[key] = default
            elif isinstance(default, dict) and isinstance(result.get(key), dict):
                for subkey, subdefault in default.items():
                    if subkey not in result[key]:
                        result[key][subkey] = subdefault
        
        return result

