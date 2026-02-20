"""
AppWorld Diagnosis Module - Stage 1 of the Proposer Agent for AppWorld Tasks.

Analyzes API usage patterns, code execution failures, and task understanding issues
to determine root causes and recommend improvements.
"""

import json
import re
from typing import List, Dict, Any, Optional
from ..analysis_tools import ProposerAnalysisTools


class AppWorldDiagnosisModule:
    """
    Stage 1: Deep Error Analysis for AppWorld Tasks.
    
    This module analyzes:
    1. API selection and usage errors
    2. Code execution failures
    3. Task understanding issues
    4. Multi-step workflow problems
    
    Returns a diagnosis report for the Plan module.
    """
    
    DIAGNOSIS_SYSTEM_PROMPT = """You are an AI improvement agent in the DIAGNOSIS stage for AppWorld tasks.

Your role is to analyze AppWorld task execution failures and identify their root causes.

## Error Categories

### 1. API Issues
- Wrong API/endpoint selected
- Missing authentication
- Incorrect parameters
- Response parsing failures
- Pagination not handled

### 2. Code Execution Issues
- Python syntax errors
- Import failures
- Runtime exceptions
- Type errors
- API response handling errors

### 3. Task Understanding Issues
- Misunderstood requirements
- Incomplete task execution
- Wrong output format
- Missing steps in workflow

## Analysis Tools Available

You have tools to investigate the execution history:

### `analyze_error_patterns(min_occurrences=2, min_batches=1)`
Find recurring error patterns across multiple batches.
Returns: List of common errors, their frequency, and affected tasks.

### `grep_observations(pattern, max_results=50)`
Search historical logs for specific patterns like API names, errors, exceptions.
Use regex patterns for flexible matching.

### `read_batch_file(batch_id)`
Read all observations from a specific batch to understand the full context.

### `analyze_tool_usage_in_batch(batch_id)`
See which evolved tools were called and if they returned useful data.
Helps identify if tool issues are causing failures.

### `read_tool_code(tool_name)`
Read actual implementation of an evolved tool.
Use when you suspect a tool has bugs or needs improvement.

### `analyze_api_usage(batch_id)`
Analyze which AppWorld APIs were called, success rates, and common errors.
Returns: API call patterns, authentication status, response formats.

### `search_trajectories(keywords, max_tasks=10)` 
Search task trajectories for specific keywords like API names, error messages.
Returns: Matching trajectory segments with context.

## Response Format

First, request tool calls if needed:
```json
{
  "tool_calls": [
    {"function": "analyze_error_patterns", "args": {"min_occurrences": 2, "min_batches": 1}},
    {"function": "grep_observations", "args": {"pattern": "error|failed|exception", "max_results": 20}}
  ]
}
```

Then provide your diagnosis:
```json
{
  "error_category": "api_issue|code_execution|task_understanding|multi_step_failure",
  "error_frequency": "common|rare|mixed",
  "specific_issues": [
    {"type": "wrong_api", "description": "Used X API instead of Y", "severity": "high"},
    {"type": "auth_missing", "description": "No authentication provided", "severity": "critical"}
  ],
  "pattern_description": "Description of the error pattern",
  "affected_apis": ["spotify.get_playlists", "venmo.send_payment"],
  "diagnosis": {
    "api_issue": {
      "exists": true|false,
      "description": "What's wrong with API usage",
      "wrong_apis": ["api1", "api2"],
      "correct_apis": ["api3", "api4"]
    },
    "code_issue": {
      "exists": true|false,
      "description": "What's wrong with the code",
      "error_type": "syntax|runtime|import|logic"
    },
    "task_issue": {
      "exists": true|false,
      "description": "What was misunderstood",
      "missing_steps": ["step1", "step2"]
    },
    "tool_issue": {
      "exists": true|false,
      "description": "What's wrong with evolved tools",
      "tool_name": "affected_tool_name"
    }
  },
  "primary_cause": "API_SELECTION|CODE_ERROR|TASK_UNDERSTANDING|MULTI_STEP|AUTH|PARSING|TOOL_BUG|MISSING_SKILL|MISSING_KNOWLEDGE",
  "recommended_actions": [
    {
      "action": "CREATE_TOOL|EVOLVE_TOOL|ADD_SKILL|EVOLVE_SKILL|ADD_KNOWLEDGE|EVOLVE_KNOWLEDGE|SKIP",
      "priority": 1,
      "target": "name of the tool/skill/knowledge to create or evolve",
      "description": "What this action should accomplish"
    }
  ],
  "reasoning": "Why this is the root cause and how to fix it"
}
```
"""

    # AppWorld-specific error patterns
    ERROR_PATTERNS = {
        'auth_error': [
            r'401', r'unauthorized', r'authentication', r'token.*expired',
            r'invalid.*credentials', r'access.*denied'
        ],
        'api_not_found': [
            r'404', r'not.*found', r'endpoint.*not.*exist', r'invalid.*api'
        ],
        'rate_limit': [
            r'429', r'rate.*limit', r'too.*many.*requests', r'throttle'
        ],
        'parsing_error': [
            r'json.*decode', r'key.*error', r'attribute.*error', r'type.*error',
            r'index.*error', r'parsing.*failed'
        ],
        'syntax_error': [
            r'syntax.*error', r'indentation', r'unexpected.*token'
        ],
        'import_error': [
            r'import.*error', r'module.*not.*found', r'no.*module.*named'
        ],
        'api_usage_error': [
            r'missing.*parameter', r'required.*argument', r'invalid.*argument'
        ],
        'tool_call_error': [
            r'tool_call', r'nameerror.*not.*defined'
        ]
    }

    def __init__(self, workspace_dir: str = None, verbose: bool = True, enable_tools: bool = True):
        """
        Initialize the AppWorld diagnosis module.
        
        Args:
            workspace_dir: Directory where observations are stored
            verbose: Whether to print debug output
            enable_tools: Whether to enable analysis tools (for ablation studies)
        """
        self.workspace_dir = workspace_dir
        self.verbose = verbose
        self.enable_tools = enable_tools
        
        # Only initialize tools if enabled and workspace_dir provided
        if self.enable_tools and self.workspace_dir:
            self.analysis_tools = ProposerAnalysisTools(
                workspace_dir=self.workspace_dir
            )
            self.system_prompt = self.DIAGNOSIS_SYSTEM_PROMPT
        else:
            self.analysis_tools = None
            # Strip tool descriptions if tools are disabled
            self.system_prompt = self.DIAGNOSIS_SYSTEM_PROMPT.split("## Analysis Tools Available")[0]
    
    def run(
        self,
        agent,
        observations: List[Dict],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Run Stage 1: Diagnosis for AppWorld tasks.
        
        Args:
            agent: The LLM agent to use for reasoning
            observations: Current batch observations
            context: Shared context including tool_registry, patches, etc.
            
        Returns:
            Diagnosis report dict with:
            - error_category
            - specific_issues
            - diagnosis (api_issue, code_issue, task_issue)
            - primary_cause
            - recommended_action
        """
        if self.verbose:
            print("\n[AppWorld Diagnosis] 🔍 Stage 1: Deep Error Analysis...")
        
        # Quick pattern detection for common errors
        detected_patterns = self._detect_error_patterns(observations)
        if self.verbose and detected_patterns:
            print(f"[AppWorld Diagnosis]   Detected patterns: {list(detected_patterns.keys())}")
        
        # Build the analysis prompt
        prompt = self._build_analysis_prompt(observations, context, detected_patterns)
        
        tool_results = []
        response1 = None  # Initialize to ensure it's always defined
        
        # Only perform tool selection loop if tools are enabled
        if self.enable_tools:
            # Call LLM to request tool calls
            response1 = agent.call(prompt)
            
            if self.verbose:
                print(f"[AppWorld Diagnosis] 🧠 LLM Thinking (Tool Selection):")
                # Print first 800 chars of response
                response_preview = response1.replace('\n', '\n    ')
                print(f"    {response_preview}...")
            
            parsed1 = self._parse_response(response1)
            
            # Execute tool calls
            if parsed1.get("tool_calls"):
                tool_results = self._execute_tool_calls(parsed1["tool_calls"])
                if self.verbose:
                    print(f"[AppWorld Diagnosis]   Executed {len(tool_results)} tool calls")
        elif self.verbose:
            print("[AppWorld Diagnosis]   Tools disabled (Ablation: no_tools) - Skipping analysis tools")
        
        # Make diagnosis (with or without tool results)
        diagnosis_prompt = self._build_diagnosis_prompt(
            observations, context, tool_results, detected_patterns
        )
        response2 = agent.call(diagnosis_prompt)
        
        if self.verbose:
            print(f"\n[AppWorld Diagnosis] 🧠 LLM Thinking (Final Diagnosis):")
            # Print first 1000 chars of response
            response_preview = response2[:1000].replace('\n', '\n    ')
            print(f"    {response_preview}...")
        
        result = self._parse_response(response2)
        
        # Ensure required fields exist
        result = self._normalize_diagnosis(result, detected_patterns)
        result["tool_results"] = tool_results
        result["detected_patterns"] = detected_patterns
        result["_raw_responses"] = [response1, response2]
        
        if self.verbose:
            print(f"\n[AppWorld Diagnosis]   Error category: {result.get('error_category', 'unknown')}")
            print(f"[AppWorld Diagnosis]   Primary cause: {result.get('primary_cause', 'unknown')}")
            print(f"[AppWorld Diagnosis]   Recommended: {result.get('recommended_action', 'unknown')}")
        
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
            print("\n[AppWorld Diagnosis] 🔧 Running repair diagnosis...")
        
        error_str = json.dumps(error_log, indent=2, default=str)[:3000]
        
        # AppWorld-specific hints
        common_error_hints = ""
        if "api" in error_str.lower() and "not found" in error_str.lower():
            common_error_hints = """
⚠️ **COMMON ERROR: API endpoint not found**
The agent may be using incorrect API names. Suggest ADD_SKILL for API discovery.
"""
        elif "authentication" in error_str.lower() or "401" in error_str:
            common_error_hints = """
⚠️ **COMMON ERROR: Authentication failure**
The agent is not properly authenticating. Suggest ADD_KNOWLEDGE for auth handling.
"""
        elif "parsing" in error_str.lower() or "json" in error_str.lower():
            common_error_hints = """
⚠️ **COMMON ERROR: Response parsing failure**
The agent is not correctly parsing API responses. Suggest CREATE_TOOL for response parsing.
"""
        
        prompt = f"""You are analyzing a FAILED AppWorld task execution.

## Error from Previous Attempt

```
{error_str}
```
{common_error_hints}

## Current Agent Configuration

Tools registered: {context.get('num_tools', 0)}
Skills available: {context.get('num_skills', 0)}
Patches applied: {context.get('num_patches', 0)}

## Your Task

Diagnose what went wrong and suggest a repair. Focus on:
1. API selection and usage errors
2. Code execution failures
3. Response parsing issues
4. Task understanding gaps

Respond with JSON:
```json
{{
  "error_type": "api_error|code_error|parsing_error|task_error|other",
  "error_description": "What specifically went wrong",
  "repair_strategy": "How to fix it",
  "recommended_action": "ADD_SKILL|ADD_KNOWLEDGE|CREATE_TOOL|EVOLVE_TOOL|SKIP",
  "spec_hint": "Hint for what the skill/patch/tool should do",
  "reasoning": "Why this repair should work"
}}
```
"""
        
        response = agent.call(prompt)
        result = self._parse_response(response)
        
        result["is_repair"] = True
        result["original_error"] = error_log
        
        return result
    
    def _detect_error_patterns(self, observations: List[Dict]) -> Dict[str, List[str]]:
        """Detect common error patterns in observations."""
        detected = {}
        
        for obs in observations:
            # Prefer structured error details when available
            for detail in obs.get('error_details', []):
                detail_type = detail.get('type')
                if detail_type == "http_status":
                    code = detail.get('status_code')
                    if code == 401:
                        detected.setdefault('auth_error', []).append(f"401: {detail.get('message', '')}")
                    elif code == 404:
                        detected.setdefault('api_not_found', []).append(f"404: {detail.get('message', '')}")
                    elif code == 422:
                        detected.setdefault('api_usage_error', []).append(f"422: {detail.get('message', '')}")
                    elif code == 429:
                        detected.setdefault('rate_limit', []).append(f"429: {detail.get('message', '')}")
                elif detail_type in ("api_not_found", "app_not_found"):
                    detected.setdefault('api_not_found', []).append(detail.get('message', detail_type))
                elif detail_type in ("name_error", "tool_call_literal"):
                    detected.setdefault('tool_call_error', []).append(detail.get('message', detail_type))

            # Check trajectory for error patterns
            trajectory = obs.get('trajectory', [])
            for msg in trajectory:
                content = str(msg.get('content', '')).lower()
                
                for pattern_name, patterns in self.ERROR_PATTERNS.items():
                    for pattern in patterns:
                        if re.search(pattern, content, re.IGNORECASE):
                            if pattern_name not in detected:
                                detected[pattern_name] = []
                            # Store a sample of the match
                            match = re.search(f'.{{0,50}}{pattern}.{{0,50}}', content, re.IGNORECASE)
                            if match:
                                detected[pattern_name].append(match.group()[:100])
        
        return detected
    
    def _build_analysis_prompt(
        self,
        observations: List[Dict],
        context: Dict[str, Any],
        detected_patterns: Dict[str, List[str]]
    ) -> str:
        """Build the initial analysis prompt."""
        batch_id = observations[0].get("batch_id", 0) if observations else 0
        avg_score = sum(o.get('score', 0) for o in observations) / len(observations) if observations else 0
        completed = sum(1 for o in observations if o.get('task_completed', False))
        
        # Format current batch summary
        batch_summary = self._format_batch_summary(observations)
        print(f"batch summary: {batch_summary}")
        
        # Format detected patterns
        pattern_info = ""
        if detected_patterns:
            pattern_info = "\n### Detected Error Patterns (Auto-detected)\n"
            for name, samples in detected_patterns.items():
                pattern_info += f"\n**{name}**: {len(samples)} occurrences\n"
                for sample in samples[:2]:  # Show first 2 samples
                    pattern_info += f"  - `{sample[:80]}...`\n"
        
        prompt = f"""{self.system_prompt}

## Current Situation

- Batch {batch_id}: {avg_score:.1%} average score, {completed}/{len(observations)} tasks completed
- Total batches completed: {context.get('num_batches', 0)}
- Existing tools: {context.get('num_tools', 0)}, skills: {context.get('num_skills', 0)}, knowledge: {context.get('num_knowledge', 0)}, patches: {context.get('num_patches', 0)}

## Current Batch Error Summary

{batch_summary}
{pattern_info}

## Your Task

First, identify the primary error category and investigate further if needed.
"""
        
        if self.enable_tools:
            prompt += """
Request tool calls or provide your diagnosis directly if the pattern is clear.

Respond with JSON requesting tool calls:
```json
{
  "tool_calls": [
    {"function": "grep_observations", "args": {"pattern": "error", "max_results": 20}}
  ]
}
```
"""
        else:
            prompt += """
Provide your diagnosis based on the provided batch summary.
Do not request tool calls as analysis tools are disabled.

Respond with JSON:
```json
{
  "tool_calls": []
}
```
"""
        return prompt
    
    def _build_diagnosis_prompt(
        self,
        observations: List[Dict],
        context: Dict[str, Any],
        tool_results: List[Dict],
        detected_patterns: Dict[str, List[str]]
    ) -> str:
        """Build the diagnosis prompt with tool results."""
        
        # Format tool results
        tool_results_str = ""
        if tool_results:
            tool_results_str = "\n## Tool Investigation Results\n\n"
            for tr in tool_results:
                tool_results_str += f"### {tr['function']}\n"
                result_preview = str(tr.get('result', ''))[:500]
                tool_results_str += f"```\n{result_preview}\n```\n\n"
        
        # Format tool execution feedback from observations
        tool_feedback = self._format_tool_execution_feedback(observations)
        
        # Get agent's resources (tools, skills, knowledge)
        agent_resources_section = self._format_agent_resources(context)
        
        batch_summary = self._format_batch_summary(observations)
        
        num_tools = context.get("num_tools", 0)
        num_skills = context.get("num_skills", 0)
        num_knowledge = context.get("num_knowledge", 0)
        num_patches = context.get("num_patches", 0)
        
        prompt = f"""## Tool Investigation Results
{tool_results_str}

## Tool Execution in Current Batch
{tool_feedback}

{agent_resources_section}

## Current Agent Setup

- Evolved Tools: {num_tools}
- Skills: {num_skills}
- Knowledge Entries: {num_knowledge}
- Behavioral Patches: {num_patches}

## Batch Summary

{batch_summary}

## Previously Detected Patterns

{json.dumps(list(detected_patterns.keys()), indent=2) if detected_patterns else "None"}

## Your Task

Based on the analysis, provide your final diagnosis.

**⚠️ IMPORTANT ACTION RULES:**

### Tool Actions
- `CREATE_TOOL`: Create a NEW tool when no suitable tool exists
- `EVOLVE_TOOL`: Improve an EXISTING tool that has bugs or needs enhancement

### Skill Actions  
- `ADD_SKILL`: Add a new reusable skill (e.g., API discovery, authentication patterns)
- `EVOLVE_SKILL`: Improve an existing skill

### Knowledge Actions
- `ADD_KNOWLEDGE`: Add factual knowledge (e.g., API endpoints, data formats)
- `EVOLVE_KNOWLEDGE`: Update existing knowledge with new information

### Governance Actions
- `SKIP`: When performance is good and stable (>80% success rate)

**Note:** For behavioral guidance, use ADD_SKILL with scope="tactical_patch".

{"⚠️ NO tools exist yet - use CREATE_TOOL first!" if num_tools == 0 else "✓ Tools exist - EVOLVE_TOOL is available."}
{"⚠️ NO skills defined yet - consider ADD_SKILL!" if num_skills == 0 else f"✓ {num_skills} skills available."}

Respond with complete diagnosis JSON:
```json
{{
  "error_category": "api_issue|code_execution|task_understanding|multi_step_failure",
  "error_frequency": "common|rare|mixed",
  "specific_issues": [
    {{"type": "issue_type", "description": "...", "severity": "critical|high|medium|low"}}
  ],
  "pattern_description": "Description of the error pattern",
  "diagnosis": {{
    "tool_issue": {{
      "exists": true|false,
      "description": "What's wrong with tools",
      "tool_name": "name or null"
    }},
    "skill_issue": {{
      "exists": true|false,
      "description": "What skill is missing or broken",
      "skill_name": "name or null"
    }},
    "knowledge_issue": {{
      "exists": true|false,
      "description": "What knowledge is missing",
      "knowledge_type": "api_info|workflow|data_format"
    }},
    "behavioral_issue": {{
      "exists": true|false,
      "description": "What behavioral guidance is needed"
    }}
  }},
  "primary_cause": "API_SELECTION|CODE_ERROR|TASK_UNDERSTANDING|AUTH|PARSING|TOOL_BUG|MISSING_SKILL|MISSING_KNOWLEDGE",
  "recommended_actions": [
    {{
      "action": "CREATE_TOOL|EVOLVE_TOOL|ADD_SKILL|EVOLVE_SKILL|ADD_KNOWLEDGE|EVOLVE_KNOWLEDGE|SKIP",
      "priority": 1,
      "target": "name of tool/skill/knowledge to create or evolve",
      "description": "What this action should accomplish"
    }}
  ],
  "reasoning": "Why these actions will fix the issues"
}}
```

You can recommend multiple actions if necessary.
"""
        return prompt
    
    def _format_tool_execution_feedback(self, observations: List[Dict]) -> str:
        """Format tool execution feedback from observations."""
        lines = []
        
        for obs in observations[:5]:
            tool_log = obs.get("tool_execution_log", [])
            if tool_log:
                task_id = obs.get("task_id", "unknown")[:20]
                lines.append(f"**Task {task_id}:**")
                for tc in tool_log[:3]:  # Limit to 3 per task
                    tool_name = tc.get("tool_name", "unknown")
                    success = "✓" if tc.get("success") else "✗"
                    result_preview = str(tc.get("result", ""))[:100]
                    lines.append(f"  - {tool_name}: {success} {result_preview}...")
        
        if not lines:
            return "No tool executions recorded in this batch."
        
        return "\n".join(lines)
    
    def _format_agent_resources(self, context: Dict[str, Any]) -> str:
        """Format agent's available resources (tools, skills, knowledge)."""
        sections = []
        
        # Tools section
        tool_registry = context.get("tool_registry")
        if tool_registry:
            tools_list = []
            for tool in tool_registry.list_tools():
                tools_list.append({
                    "name": tool.get("name"),
                    "id": tool.get("id"),
                    "description": tool.get("description", "")[:80]
                })
            
            if tools_list:
                sections.append(f"""## Agent's Evolved Tools (EVOLVE_TOOL targets)

**These tools can be evolved:**
{json.dumps(tools_list, indent=2)}
""")
            else:
                sections.append("""## Agent's Evolved Tools

**No evolved tools yet. Use CREATE_TOOL to create one.**
""")
        
        # Skills section
        skills = context.get("skills", {})
        if skills:
            skills_list = [{"name": k, "description": v.get("description", "")[:80]} 
                          for k, v in skills.items()]
            sections.append(f"""## Agent's Skills (EVOLVE_SKILL targets)

**These skills can be evolved:**
{json.dumps(skills_list, indent=2)}
""")
        else:
            sections.append("""## Agent's Skills

**No skills defined yet. Use ADD_SKILL to add one.**
""")
        
        # Knowledge section
        knowledge = context.get("knowledge", [])
        if knowledge:
            knowledge_summary = [{"type": k.get("type", "general"), "topic": k.get("topic", "")[:50]} 
                                for k in knowledge[:5]]
            sections.append(f"""## Agent's Knowledge (EVOLVE_KNOWLEDGE targets)

**Knowledge entries ({len(knowledge)} total):**
{json.dumps(knowledge_summary, indent=2)}
""")
        else:
            sections.append("""## Agent's Knowledge

**No knowledge entries yet. Use ADD_KNOWLEDGE to add domain knowledge.**
""")
        
        return "\n".join(sections)
    
    def _format_batch_summary(self, observations: List[Dict]) -> str:
        """Format a summary of the current batch observations with full trajectory."""
        lines = []
        
        for i, obs in enumerate(observations[:5]):  # Limit to 5
            task_id = obs.get('task_id', 'unknown')[:30]
            score = obs.get('score', 0)
            completed = '✓' if obs.get('task_completed', False) else '✗'
            
            lines.append(f"**Task {i+1}**: {task_id}")
            lines.append(f"  - Score: {score:.2f}, Completed: {completed}")
            
            # Include full trajectory summary for analysis
            trajectory = obs.get('trajectory', [])
            if trajectory:
                lines.append(f"  - Trajectory ({len(trajectory)} messages):")
                
                # Summarize each step
                step_num = 0
                for msg in trajectory:
                    role = msg.get('role', 'unknown')
                    content = msg.get('content', '')
                    msg_type = msg.get('type', '')
                    
                    if role == 'assistant':
                        step_num += 1
                        # Show first 200 chars of assistant response
                        preview = content[:200].replace('\n', ' ')
                        lines.append(f"    Step {step_num} [AGENT]: {preview}...")
                    elif role == 'user' and 'Output' in content:
                        # Show output results (first 300 chars)
                        preview = content[:300].replace('\n', ' ')
                        lines.append(f"    Step {step_num} [OUTPUT]: {preview}...")
                    elif role == 'user' and msg_type == 'retrieval':
                        lines.append(f"    Step {step_num} [RETRIEVAL]: {content[:100]}...")
                    elif role == 'system' and msg_type == 'tool_execution':
                        preview = content[:150].replace('\n', ' ')
                        lines.append(f"    Step {step_num} [TOOL]: {preview}...")
            
            # Also show errors if any
            errors = obs.get('errors', [])
            if errors:
                lines.append(f"  - Errors ({len(errors)}): {errors[:3]}")
            
            error_details = obs.get('error_details', [])
            if error_details:
                preview = []
                for detail in error_details[:3]:
                    detail_type = detail.get('type', 'unknown')
                    if detail_type == "http_status":
                        preview.append(f"http_status:{detail.get('status_code', 'unknown')}")
                    elif detail_type == "api_not_found":
                        preview.append(f"api_not_found:{detail.get('app_name', 'unknown')}.{detail.get('api_name', 'unknown')}")
                    elif detail_type == "app_not_found":
                        preview.append(f"app_not_found:{detail.get('app_name', 'unknown')}")
                    elif detail_type == "name_error":
                        preview.append(f"name_error:{detail.get('name', 'unknown')}")
                    else:
                        preview.append(detail_type)
                lines.append(f"  - Error details: {preview}")
        
        if len(observations) > 5:
            lines.append(f"\n... and {len(observations) - 5} more tasks")
        
        return "\n".join(lines)
    
    def _execute_tool_calls(self, tool_calls: List[Dict]) -> List[Dict]:
        """Execute analysis tool calls."""
        results = []
        
        for tc in tool_calls:
            func_name = tc.get("function", "")
            args = tc.get("args", {})
            
            result = {
                "function": func_name,
                "args": args,
                "success": False,
                "result": None
            }
            
            if self.verbose:
                print(f"[AppWorld Diagnosis]     Executing tool: {func_name}({args})")
            
            try:
                if func_name == "analyze_error_patterns":
                    result["result"] = self.analysis_tools.analyze_error_patterns(
                        min_occurrences=args.get("min_occurrences", 2),
                        min_batches=args.get("min_batches", 1)
                    )
                    result["success"] = True
                    
                elif func_name == "grep_observations":
                    result["result"] = self.analysis_tools.grep_observations(
                        pattern=args.get("pattern", ""),
                        max_results=args.get("max_results", 50)
                    )
                    result["success"] = True
                    
                elif func_name == "read_batch_file":
                    result["result"] = self.analysis_tools.read_batch_file(
                        batch_id=args.get("batch_id", 1)
                    )
                    result["success"] = True
                    
                elif func_name == "analyze_tool_usage_in_batch":
                    result["result"] = self.analysis_tools.analyze_tool_usage_in_batch(
                        batch_id=args.get("batch_id", 1)
                    )
                    result["success"] = True
                    
                elif func_name == "read_tool_code":
                    result["result"] = self.analysis_tools.read_tool_code(
                        tool_name=args.get("tool_name", "")
                    )
                    result["success"] = True
                    
                # AppWorld-specific tools
                elif func_name == "analyze_api_usage":
                    result["result"] = self._analyze_api_usage(args.get("batch_id", 1))
                    result["success"] = True
                    
                elif func_name == "search_trajectories":
                    result["result"] = self._search_trajectories(
                        keywords=args.get("keywords", ""),
                        max_tasks=args.get("max_tasks", 10)
                    )
                    result["success"] = True
                    
                else:
                    result["result"] = f"Unknown function: {func_name}"
                
                # Print tool execution result for debugging (inside try block)
                if self.verbose and result["success"]:
                    result_preview = str(result["result"])[:500]
                    print(f"[AppWorld Diagnosis]     Tool result: {result_preview}...")
                    
            except Exception as e:
                result["result"] = f"Error: {str(e)}"
                if self.verbose:
                    print(f"[AppWorld Diagnosis]     Tool error: {e}")
            
            results.append(result)
        
        return results
    
    def _analyze_api_usage(self, batch_id: int) -> Dict[str, Any]:
        """
        Analyze which AppWorld APIs were called, success rates, and common errors.
        AppWorld-specific analysis tool.
        """
        api_stats = {
            "apis_called": {},
            "authentication_issues": 0,
            "error_patterns": [],
            "success_rate": 0.0
        }
        
        try:
            # Read batch observations
            batch_obs = self.analysis_tools.read_batch_file(batch_id)
            if not batch_obs:
                return {"error": f"No data found for batch {batch_id}"}
            
            total_calls = 0
            successful_calls = 0
            
            for obs in batch_obs if isinstance(batch_obs, list) else [batch_obs]:
                trajectory = obs.get("trajectory", []) if isinstance(obs, dict) else []
                
                for msg in trajectory:
                    content = str(msg.get("content", ""))
                    
                    # Detect API calls (pattern: apis.app_name.method)
                    api_matches = re.findall(r'apis\.(\w+)\.(\w+)', content)
                    for app, method in api_matches:
                        api_name = f"{app}.{method}"
                        if api_name not in api_stats["apis_called"]:
                            api_stats["apis_called"][api_name] = {"count": 0, "errors": 0}
                        api_stats["apis_called"][api_name]["count"] += 1
                        total_calls += 1
                        
                        # Check for errors related to this API
                        if "error" in content.lower() or "failed" in content.lower():
                            api_stats["apis_called"][api_name]["errors"] += 1
                        else:
                            successful_calls += 1
                    
                    # Detect auth issues
                    if re.search(r'401|unauthorized|authentication', content, re.I):
                        api_stats["authentication_issues"] += 1
            
            api_stats["success_rate"] = successful_calls / total_calls if total_calls > 0 else 0
            
        except Exception as e:
            api_stats["error"] = str(e)
        
        return api_stats
    
    def _search_trajectories(self, keywords: str, max_tasks: int = 10) -> List[Dict]:
        """
        Search task trajectories for specific keywords.
        AppWorld-specific search tool.
        """
        matches = []
        
        try:
            # Use grep to find matching observations
            grep_results = self.analysis_tools.grep_observations(
                pattern=keywords,
                max_results=max_tasks * 5  # Get more results since we'll group by task
            )
            
            if isinstance(grep_results, list):
                seen_tasks = set()
                for result in grep_results:
                    task_id = result.get("task_id", "unknown")
                    if task_id not in seen_tasks and len(matches) < max_tasks:
                        seen_tasks.add(task_id)
                        matches.append({
                            "task_id": task_id,
                            "matching_content": result.get("content", "")[:500],
                            "context": result.get("context", "")[:200]
                        })
                        
        except Exception as e:
            return [{"error": str(e)}]
        
        return matches
    
    def _parse_response(self, response: str) -> Dict[str, Any]:
        """Parse JSON from LLM response."""
        # Try to extract JSON from response
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # Try parsing whole response as JSON
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        
        # Return raw response
        return {"raw_response": response}
    
    def _normalize_diagnosis(
        self,
        result: Dict[str, Any],
        detected_patterns: Dict[str, List[str]]
    ) -> Dict[str, Any]:
        """Ensure all required fields exist in diagnosis."""
        
        # Default error category based on detected patterns
        if not result.get("error_category"):
            if "auth_error" in detected_patterns:
                result["error_category"] = "api_issue"
            elif "syntax_error" in detected_patterns or "import_error" in detected_patterns:
                result["error_category"] = "code_execution"
            elif "parsing_error" in detected_patterns:
                result["error_category"] = "api_issue"
            else:
                result["error_category"] = "unknown"
        
        # Default primary cause
        if not result.get("primary_cause"):
            pattern_to_cause = {
                "auth_error": "AUTH",
                "api_not_found": "API_SELECTION",
                "parsing_error": "PARSING",
                "syntax_error": "CODE_ERROR",
                "import_error": "CODE_ERROR",
                "api_usage_error": "API_SELECTION"
            }
            for pattern in detected_patterns:
                if pattern in pattern_to_cause:
                    result["primary_cause"] = pattern_to_cause[pattern]
                    break
            if not result.get("primary_cause"):
                result["primary_cause"] = "UNKNOWN"
        
        # Default recommended actions based on primary cause
        if not result.get("recommended_actions"):
            cause_to_action = {
                "AUTH": "ADD_SKILL",  # Use tactical_patch scope
                "API_SELECTION": "ADD_SKILL",
                "PARSING": "CREATE_TOOL",
                "CODE_ERROR": "ADD_SKILL",  # Use tactical_patch scope
                "TASK_UNDERSTANDING": "ADD_SKILL",
                "MULTI_STEP": "ADD_SKILL",
                "MISSING_SKILL": "ADD_SKILL",
                "MISSING_KNOWLEDGE": "ADD_KNOWLEDGE",
                "TOOL_BUG": "EVOLVE_TOOL"
            }
            action = cause_to_action.get(
                result.get("primary_cause", ""),
                "SKIP"
            )
            result["recommended_actions"] = [{
                "action": action,
                "priority": 1,
                "target": "",
                "description": f"Auto-generated action for {result.get('primary_cause', 'unknown')} issue"
            }]
        
        # Also set legacy recommended_action for backward compatibility
        if result.get("recommended_actions") and not result.get("recommended_action"):
            result["recommended_action"] = result["recommended_actions"][0].get("action", "SKIP")
        
        # Ensure diagnosis structure exists
        if not result.get("diagnosis"):
            result["diagnosis"] = {
                "api_issue": {"exists": False},
                "code_issue": {"exists": False},
                "task_issue": {"exists": False},
                "skill_issue": {"exists": False},
                "knowledge_issue": {"exists": False},
                "tool_issue": {"exists": False}
            }
        
        return result
