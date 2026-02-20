"""
Hybrid Tool-Patch Proposer Implementation.

This proposer combines the best of both approaches:
1. Tools provide feature extraction and statistical analysis (not end-to-end prediction)
2. Patches provide high-level behavioral guidance (soft rules)
3. LLM retains final decision-making authority

Key Design Principles:
- Tools are feature extractors, NOT predictors
- Patches guide behavior without hard constraints
- LLM synthesizes both for final prediction
"""

import json
import re
from typing import List, Dict, Any, Optional
from collections import deque
from ..proposer.proposer import Proposer


class HybridToolPatchProposer(Proposer):
    """
    Hybrid proposer that proposes both tools (for feature extraction) and patches (for guidance).
    
    This addresses the limitations observed in pure tool-based and pure patch-based approaches:
    
    Tool-based limitations:
    - Tools that make end-to-end predictions tend to overfit
    - Hard-coded logic overrides LLM's contextual understanding
    - Difficult to compose and maintain
    
    Patch-based limitations:
    - Cannot perform complex statistical computations
    - Limited to heuristic rules
    - May not capture precise patterns
    
    Hybrid approach:
    - Tools extract features (time patterns, category switches, etc.)
    - Patches provide behavioral guidance based on observations
    - LLM combines both for final decision
    """
    
    HYBRID_PROPOSAL_PROMPT = """You are an expert at improving agent performance through BOTH feature extraction tools AND behavioral guidance patches.

Current System Prompt:
{system_prompt}

Existing Tools ({num_tools} total):
{existing_tools}

Existing Patches ({num_patches} total):
{existing_patches}

Recent Observation History ({num_recent} observations):
{observation_history}

Your task is to analyze the observation history and decide on actions to improve prediction accuracy:

## Available Actions

1. **CREATE_FEATURE_TOOL**: Create a tool that EXTRACTS features or computes statistics
   - When: Need to calculate patterns, frequencies, time intervals, etc.
   - Tools should RETURN data, NOT make final predictions
   - Examples: "calculate_time_between_events", "count_category_switches", "extract_recent_search_queries"

2. **EVOLVE_FEATURE_TOOL**: Improve an existing feature extraction tool
   - When: Tool is used but produces incorrect/incomplete features
   - Focus on fixing feature extraction bugs, not prediction logic

3. **ADD_PATCH**: Add behavioral guidance based on error patterns
   - When: You see consistent prediction errors that need guidance
   - Patches should be heuristic rules, not hard constraints
   - Examples: "When X pattern occurs, consider Y behavior instead of Z"

4. **SKIP**: No action needed
   - When: Current tools/patches are working well (accuracy > 85%)
   - When: Not enough data to identify clear patterns

## Important Guidelines

**For Tools:**
- Tools should be FEATURE EXTRACTORS, not predictors
- Return structured data (dicts, lists, statistics)
- Focus on reusable computations
- Keep tools simple and composable
- DO NOT hard-code specific product names, ASINs, or search queries
- ⚠️  CRITICAL: Always include the "code" field with COMPLETE, EXECUTABLE Python code
- ⚠️  DO NOT use "implementation_focus", "implementation_notes", or other descriptive fields instead of actual code
- ⚠️  The code will be saved to a file and imported - it must be syntactically valid Python

**For Patches:**
- Focus on high-level behavioral patterns
- Use probabilistic language ("often", "typically", "consider")
- Avoid overfitting to specific values
- Each patch should address one error pattern

**When to prefer TOOL vs PATCH:**
- Tool: Need precise calculation (time intervals, counts, frequencies)
- Patch: Need behavioral guidance (event type transitions, category switches)

## Tools Currently in Use

{tools_with_code}

## Response Format

Respond with a JSON object:
{{
  "action": "CREATE_TOOL|EVOLVE_TOOL|ADD_SKILL|ADD_KNOWLEDGE|ADD_PATCH|SKIP",
  "reasoning": "Explain the error pattern and why this action addresses it",
  "confidence": "high|medium|low",
  
  // For CREATE_TOOL:
  "tool_spec": {{
    "name": "feature_function_name",
    "description": "What features this tool extracts",
    "code": "def feature_function_name(...):\\n    # REQUIRED: Full executable Python code\\n    # Return features, not predictions\\n    return {{'feature1': value1, 'feature2': value2}}",
    "parameters": {{...}},
    "return_type": "Dict[str, Any]",
    "imports": ["import module"],
    "rationale": "Why these features help prediction"
  }},
  
  // ⚠️  CRITICAL: The "code" field MUST contain COMPLETE, EXECUTABLE Python code
  // Do NOT use placeholders like "implementation_focus", "implementation_notes", or "..."
  // The code will be executed directly - it must be syntactically correct Python
  
  // For EVOLVE_TOOL:
  "tool_spec": {{
    "tool_id": "existing_tool_name",
    "improvements": "What features to fix/add",
    "new_code": "def improved_function(...):\\n    ...",
    "rationale": "Why these improvements fix prediction errors"
  }},
  
  // For ADD_PATCH:
  "patch_spec": {{
    "hypothesis": "What error pattern was observed",
    "patch": "Behavioral guidance to fix the pattern",
    "error_ids": ["sample_0", "sample_5"],
    "error_rate": "X/Y samples showed this pattern"
  }}
}}

**Example - Feature Tool Creation:**

Observation: Agent consistently predicts wrong timestamps, off by hours
Action: CREATE_TOOL
{{
  "action": "CREATE_TOOL",
  "reasoning": "Agent struggles with timestamp prediction. Need tool to extract temporal patterns from event sequences.",
  "confidence": "high",
  "tool_spec": {{
    "name": "extract_temporal_patterns",
    "description": "Extract time intervals and patterns from event sequences",
    "code": "def extract_temporal_patterns(events: list) -> dict:\\n    import datetime\\n    from statistics import mean, median\\n    \\n    timestamps = []\\n    for event in events:\\n        # Parse event format\\n        parts = event.strip('{{}}').split(',')\\n        ts_str = parts[0].strip()\\n        if ts_str != 'NULL':\\n            try:\\n                timestamps.append(datetime.datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S'))\\n            except:\\n                pass\\n    \\n    if len(timestamps) < 2:\\n        return {{'avg_gap_seconds': 0, 'last_timestamp': None}}\\n    \\n    # Calculate time gaps\\n    gaps = [(timestamps[i+1] - timestamps[i]).total_seconds() for i in range(len(timestamps)-1)]\\n    \\n    return {{\\n        'avg_gap_seconds': mean(gaps) if gaps else 0,\\n        'median_gap_seconds': median(gaps) if gaps else 0,\\n        'last_timestamp': timestamps[-1].strftime('%Y-%m-%d %H:%M:%S'),\\n        'num_events': len(timestamps),\\n        'time_acceleration': gaps[-1] / gaps[0] if len(gaps) > 1 and gaps[0] > 0 else 1.0\\n    }}",
    "parameters": {{
      "events": {{
        "type": "list",
        "description": "List of event strings to analyze",
        "required": true
      }}
    }},
    "return_type": "Dict[str, Any]",
    "imports": ["import datetime", "from statistics import mean, median"],
    "rationale": "This tool extracts temporal features that the LLM can use to make informed timestamp predictions. Returns statistics, not predictions."
  }}
}}

**Example - Patch Addition:**

Observation: Agent predicts 'order' after 'glance_view' but should predict 'add_to_cart'
Action: ADD_PATCH
{{
  "action": "ADD_PATCH",
  "reasoning": "8/10 samples show agent skipping add_to_cart step in purchase flow",
  "confidence": "high",
  "patch_spec": {{
    "hypothesis": "Agent assumes customers immediately order after viewing, missing the typical add_to_cart intermediate step",
    "patch": "When predicting after glance_view events, consider that customers typically add items to cart before ordering. Predict add_to_cart as an intermediate step rather than jumping directly to order.",
    "error_ids": ["sample_0", "sample_2", "sample_4", "sample_5", "sample_7", "sample_8", "sample_9", "sample_11"],
    "error_rate": "8/10 samples"
  }}
}}

Now analyze the observations and propose an action.
"""

    def __init__(
        self,
        observation_window: int = 10,
        min_pattern_occurrences: int = 3,
        enable_tool_evolution: bool = True,
        enable_patches: bool = True
    ):
        """
        Initialize the hybrid proposer.
        
        Args:
            observation_window: Number of recent observations to consider
            min_pattern_occurrences: Minimum times a pattern should appear
            enable_tool_evolution: Whether to propose tool evolution
            enable_patches: Whether to propose patches
        """
        self.observation_window = observation_window
        self.min_pattern_occurrences = min_pattern_occurrences
        self.enable_tool_evolution = enable_tool_evolution
        self.enable_patches = enable_patches
        self.observation_history = deque(maxlen=observation_window)
        
        # Track patches separately
        self.patches = []
    
    def propose(self, agent, obs, context=None):
        """
        Analyze observations and propose hybrid improvements.
        
        Args:
            agent: The agent being improved
            obs: Observation(s) - can be a single obs dict or list of obs dicts
            context: Optional context including:
                - tool_registry: ToolRegistry instance
                - tool_executor: ToolExecutor instance
                - patches: List of existing patches
                
        Returns:
            Proposal object with type 'hybrid' containing:
                - action: CREATE_FEATURE_TOOL, EVOLVE_FEATURE_TOOL, ADD_PATCH, or SKIP
                - tool_spec or patch_spec depending on action
        """
        # Normalize observations to list
        if not isinstance(obs, list):
            obs = [obs]
        
        # Add to observation history
        for observation in obs:
            self.observation_history.append(observation)
        
        # Get existing tools from context
        existing_tools = ""
        tools_with_code = ""
        num_tools = 0
        if context and "tool_registry" in context:
            registry = context["tool_registry"]
            existing_tools = registry.export_all_signatures()
            num_tools = len(registry.list_tools())
            
            # Get detailed info for evolution candidates
            evolution_candidates = registry.export_tools_for_evolution(
                include_code=True,
                min_usage=3,
                max_tools=5
            )
            tools_with_code = self._format_tools_with_code(evolution_candidates)
        
        # Get existing patches from context
        existing_patches = ""
        num_patches = 0
        if context and "patches" in context:
            self.patches = context["patches"]
            num_patches = len(self.patches)
            existing_patches = self._format_patches(self.patches)
        
        # Format observation history
        obs_history_str = self._format_observation_history()
        
        # Call LLM to analyze and propose
        prompt = self.HYBRID_PROPOSAL_PROMPT.format(
            system_prompt=agent.system_prompt[:1000] + "..." if len(agent.system_prompt) > 1000 else agent.system_prompt,
            existing_tools=existing_tools if existing_tools else "No feature extraction tools yet.",
            num_tools=num_tools,
            existing_patches=existing_patches if existing_patches else "No patches yet.",
            num_patches=num_patches,
            tools_with_code=tools_with_code if tools_with_code else "No tools available for evolution yet.",
            observation_history=obs_history_str,
            num_recent=len(self.observation_history)
        )
        
        response = agent.call(prompt)
        
        # Parse response
        proposal = self._parse_proposal(response)
        
        # Return proposal object
        return {
            "type": "hybrid",
            "action": proposal.get("action", "SKIP"),
            "confidence": proposal.get("confidence", "low"),
            "reasoning": proposal.get("reasoning", ""),
            "tool_spec": proposal.get("tool_spec"),
            "patch_spec": proposal.get("patch_spec")
        }
    
    def _format_observation_history(self) -> str:
        """Format observation history for prompt."""
        if not self.observation_history:
            return "No observations yet."
        
        formatted = []
        correct_count = 0
        incorrect_count = 0
        
        for i, obs in enumerate(self.observation_history, 1):
            is_correct = obs.get('is_correct', None)
            if is_correct is True:
                correct_count += 1
                correctness_marker = "✓ CORRECT"
            elif is_correct is False:
                incorrect_count += 1
                correctness_marker = "✗ INCORRECT"
            else:
                correctness_marker = "? UNKNOWN"
            
            formatted.append(f"[Observation {i}] {correctness_marker}")
            formatted.append(f"ID: {obs.get('id', 'N/A')}")
            formatted.append(f"Input: {obs.get('input', 'N/A')[:150]}...")
            formatted.append(f"Output: {obs.get('output', 'N/A')[:150]}...")
            if 'answer' in obs:
                formatted.append(f"Expected: {obs['answer'][:150]}...")
            formatted.append("")
        
        # Add summary statistics
        total = len(self.observation_history)
        if correct_count + incorrect_count > 0:
            accuracy = (correct_count / (correct_count + incorrect_count)) * 100
            formatted.insert(0, f"**Accuracy Summary:** {correct_count}/{correct_count + incorrect_count} correct ({accuracy:.1f}%)")
            formatted.insert(1, f"**Improvement Target:** {incorrect_count} incorrect predictions")
            formatted.insert(2, "")
        
        return "\n".join(formatted)
    
    def _format_tools_with_code(self, tools: List[Dict[str, Any]]) -> str:
        """Format tools with their implementation code."""
        if not tools:
            return "No tools available yet."
        
        formatted = []
        for i, tool in enumerate(tools, 1):
            usage_pct = tool.get('success_rate', 1.0) * 100
            formatted.append(f"### Tool {i}: {tool['name']}")
            formatted.append(f"**Description:** {tool['description']}")
            formatted.append(f"**Usage:** Called {tool.get('usage_count', 0)} times, Success: {usage_pct:.1f}%")
            
            # Add the implementation code
            code = tool.get('code', '')
            if code:
                formatted.append(f"\n**Current Implementation:**")
                formatted.append(f"```python")
                formatted.append(code)
                formatted.append(f"```")
            
            formatted.append("")
        
        return "\n".join(formatted)
    
    def _format_patches(self, patches: List[Dict[str, Any]]) -> str:
        """Format existing patches."""
        if not patches:
            return "No patches yet."
        
        formatted = []
        for i, patch in enumerate(patches[-10:], 1):  # Show last 10
            formatted.append(f"{i}. {patch.get('patch', 'N/A')}")
        
        if len(patches) > 10:
            formatted.insert(0, f"(Showing last 10 of {len(patches)} patches)")
        
        return "\n".join(formatted)
    
    def _parse_proposal(self, response: str) -> Dict[str, Any]:
        """Parse LLM response into proposal."""
        # Try parsing as pure JSON first
        try:
            proposal = json.loads(response)
            return proposal
        except json.JSONDecodeError:
            pass
        
        # Try to extract JSON from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            try:
                proposal = json.loads(json_match.group(1))
                return proposal
            except json.JSONDecodeError:
                pass
        
        # Try to find any JSON object in the response
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            try:
                proposal = json.loads(json_match.group())
                return proposal
            except json.JSONDecodeError as e:
                return {
                    "action": "SKIP",
                    "reasoning": f"JSON parsing error: {str(e)}. Response preview: {response[:200]}...",
                    "confidence": "low"
                }
        
        # No JSON found
        return {
            "action": "SKIP",
            "reasoning": f"No JSON found in response. Preview: {response[:200]}...",
            "confidence": "low"
        }
    
    def clear_history(self):
        """Clear observation history."""
        self.observation_history.clear()

