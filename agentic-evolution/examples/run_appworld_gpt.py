"""
AppWorld Learning System (Multi-Provider) - Aligned with Hybrid Architecture

This script runs the meta-evolver learning system on AppWorld benchmark
using the same architecture as run_hybrid_claude.py:
- ToolWorkspace for tool isolation
- Multi-provider agent support (Claude, GPT, Gemini)
- ToolEnabledProposer for analysis-driven proposals
- HybridToolPatchUpdater for applying updates

Supported providers (auto-detected from model name):
- OpenAI: gpt-5, gpt-5-mini, gpt-4o, gpt-4-turbo
- Google: gemini-3-flash-preview, gemini-2.0-flash-exp, gemini-1.5-pro
- Anthropic: claude-* (default)
"""

import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import (
    ClaudeAgent, ToolEnabledClaudeAgent, 
    GPTAgent, ToolEnabledGPTAgent,
    GeminiAgent, ToolEnabledGeminiAgent
)
from modules.observer import PersistentObserver
from modules.observer.appworld_observer import AppWorldObserver
from modules.proposer.hybrid_tool_patch_proposer import HybridToolPatchProposer
from modules.proposer.tool_enabled_proposer import ToolEnabledProposer
from modules.updater.hybrid_tool_patch_updater import HybridToolPatchUpdater
from modules.tools.tool_workspace import ToolWorkspace
from modules.skills import SkillLoader

from examples.appworld_agent_system_prompt import (
    APPWORLD_SYSTEM_PROMPT,
    TASK_INSTRUCTION_TEMPLATE,
    build_prompt_with_state,
    build_full_prompt,
    build_system_prompt,
    build_user_task_prompt,
)


EXAMPLES_DIR = os.path.dirname(os.path.abspath(__file__))


def detect_provider(model_name: str) -> str:
    """Detect LLM provider from model name.
    
    Args:
        model_name: Model name string
        
    Returns:
        Provider name: 'openai', 'google', or 'anthropic'
    """
    model_lower = model_name.lower()
    if model_lower.startswith("gpt-") or model_lower.startswith("o1") or model_lower.startswith("o3"):
        return "openai"
    elif model_lower.startswith("gemini-"):
        return "google"
    else:
        return "anthropic"  # Default to Claude


def create_agent(
    model_name: str,
    api_key: str,
    system_prompt: str = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    provider: str = None
):
    """Create an agent for the specified model (auto-detects provider).
    
    Args:
        model_name: Model name (e.g., 'gpt-5', 'gemini-3-flash-preview', 'claude-haiku-4-5-20251001')
        api_key: API key for the provider
        system_prompt: System prompt (defaults to APPWORLD_SYSTEM_PROMPT)
        max_tokens: Maximum tokens in response
        temperature: Temperature for generation
        provider: Override provider detection ('openai', 'google', 'anthropic')
        
    Returns:
        Agent instance (GPTAgent, GeminiAgent, or ClaudeAgent)
    """
    detected_provider = provider or detect_provider(model_name)
    prompt = system_prompt or APPWORLD_SYSTEM_PROMPT
    
    if detected_provider == "openai":
        return GPTAgent(
            model_name=model_name,
            api_key=api_key,
            system_prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature
        )
    elif detected_provider == "google":
        return GeminiAgent(
            model_name=model_name,
            api_key=api_key,
            system_prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature
        )
    else:
        return ClaudeAgent(
            model_name=model_name,
            api_key=api_key,
            system_prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature
        )


def create_tool_enabled_agent(
    model_name: str,
    api_key: str,
    system_prompt: str = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    tool_executor=None,
    max_tool_iterations: int = 3,
    provider: str = None
):
    """Create a tool-enabled agent for the specified model (auto-detects provider).
    
    Args:
        model_name: Model name (e.g., 'gpt-5', 'gemini-3-flash-preview', 'claude-haiku-4-5-20251001')
        api_key: API key for the provider
        system_prompt: System prompt (defaults to APPWORLD_SYSTEM_PROMPT)
        max_tokens: Maximum tokens in response
        temperature: Temperature for generation
        tool_executor: ToolExecutor instance for executing tools
        max_tool_iterations: Maximum number of tool call iterations
        provider: Override provider detection ('openai', 'google', 'anthropic')
        
    Returns:
        ToolEnabled agent instance
    """
    detected_provider = provider or detect_provider(model_name)
    prompt = system_prompt or APPWORLD_SYSTEM_PROMPT
    
    if detected_provider == "openai":
        return ToolEnabledGPTAgent(
            model_name=model_name,
            api_key=api_key,
            system_prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            tool_executor=tool_executor,
            max_tool_iterations=max_tool_iterations
        )
    elif detected_provider == "google":
        return ToolEnabledGeminiAgent(
            model_name=model_name,
            api_key=api_key,
            system_prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            tool_executor=tool_executor,
            max_tool_iterations=max_tool_iterations
        )
    else:
        return ToolEnabledClaudeAgent(
            model_name=model_name,
            api_key=api_key,
            system_prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            tool_executor=tool_executor,
            max_tool_iterations=max_tool_iterations
        )


def create_claude_agent(
    model_name: str,
    api_key: str,
    system_prompt: str = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    tool_executor=None,
    max_tool_iterations: int = 3
):
    """Create a Claude agent, optionally with tool execution support.
    
    Args:
        model_name: Claude model name
        api_key: Anthropic API key
        system_prompt: System prompt (defaults to APPWORLD_SYSTEM_PROMPT)
        max_tokens: Maximum tokens in response
        temperature: Temperature for generation
        tool_executor: Optional ToolExecutor for tool execution support
        max_tool_iterations: Maximum tool call iterations (default: 3)
        
    Returns:
        ClaudeAgent or ToolEnabledClaudeAgent (if tool_executor provided)
    """
    if tool_executor is not None:
        return ToolEnabledClaudeAgent(
            model_name=model_name,
            api_key=api_key,
            system_prompt=system_prompt or APPWORLD_SYSTEM_PROMPT,
            max_tokens=max_tokens,
            temperature=temperature,
            tool_executor=tool_executor,
            max_tool_iterations=max_tool_iterations
        )
    else:
        return ClaudeAgent(
            model_name=model_name,
            api_key=api_key,
            system_prompt=system_prompt or APPWORLD_SYSTEM_PROMPT,
            max_tokens=max_tokens,
            temperature=temperature
        )


def build_tool_signatures_section(workspace) -> str:
    """Build tool signatures section for system prompt using workspace registry.
    
    This follows the pattern from run_tool_building_example.py for consistent
    tool documentation and TOOL_CALL: format.
    
    Args:
        workspace: ToolWorkspace instance with registry
        
    Returns:
        Formatted tool signatures section for injection into prompt
    """
    if workspace is None or not hasattr(workspace, 'registry'):
        return ""
    
    tool_signatures = workspace.registry.export_all_signatures()
    if not tool_signatures:
        return ""
    
    return f"""
# Available Tools

You have access to the following specialized tools to help with this task:

{tool_signatures}

## How to Use Tools

**IMPORTANT:** Use the TOOL_CALL format to invoke tools:
- Format: TOOL_CALL: tool_name(arg1=value1, arg2=value2)
- Tool results will be provided back to you
- After using tools, continue with your task

**⚠️ CRITICAL: Do NOT use XML tool-use markup!** Never output <function_calls>, <invoke>, <parameter>, or similar XML tags. Use only the TOOL_CALL: format shown above.
"""


def load_skills_from_workspace(workspace_dir: str) -> Dict[str, Dict]:
    """
    Load skills from workspace directory using SkillLoader.
    
    Uses proper YAML frontmatter parsing with progressive disclosure
    (learn-claude-code pattern).
    
    Returns:
        Dict mapping skill name to skill metadata
    """
    skills_dir = os.path.join(workspace_dir, "skills")
    skill_loader = SkillLoader(skills_dir, verbose=False)
    return skill_loader.to_dict()


def run_skill(workspace_dir: str, skill_name: str) -> str:
    """
    Load a skill on-demand (cache-preserving pattern).
    
    This is the key mechanism from learn-claude-code:
    1. Get skill content (SKILL.md body + resource hints)
    2. Return it wrapped in <skill-loaded> tags
    3. Model receives this as tool_result (user message)
    4. Model now "knows" how to do the task
    
    Why tool_result instead of system prompt?
    - System prompt changes invalidate cache (20-50x cost increase)
    - Tool results append to end (prefix unchanged, cache hit)
    
    Args:
        workspace_dir: Path to workspace containing skills/
        skill_name: Name of the skill to load
        
    Returns:
        Skill content wrapped in <skill-loaded> tags
    """
    skills_dir = os.path.join(workspace_dir, "skills")
    skill_loader = SkillLoader(skills_dir, verbose=False)
    
    content = skill_loader.get_skill_content(skill_name)
    
    if content is None:
        available = ", ".join(skill_loader.list_skills()) or "none"
        return f"Error: Unknown skill '{skill_name}'. Available: {available}"
    
    # Wrap in tags so model knows it's skill content (learn-claude-code pattern)
    return f"""<skill-loaded name="{skill_name}">
{content}
</skill-loaded>

Follow the instructions in the skill above to complete the user's task."""


def get_skill_descriptions(workspace_dir: str) -> str:
    """
    Get skill descriptions for system prompt injection (Layer 1 metadata).
    
    This provides a minimal list of available skills so the agent knows
    what expertise is available without loading full content.
    
    Args:
        workspace_dir: Path to workspace containing skills/
        
    Returns:
        Formatted string of skill names and descriptions
    """
    skills_dir = os.path.join(workspace_dir, "skills")
    skill_loader = SkillLoader(skills_dir, verbose=False)
    return skill_loader.get_descriptions()


def load_knowledge_from_workspace(workspace_dir: str) -> List[Dict]:
    """
    Load knowledge entries from workspace directory.
    
    Knowledge is stored as a JSON file with entries containing
    'when_to_use' and 'content' fields.
    
    Returns:
        List of knowledge entries
    """
    knowledge = []
    knowledge_file = os.path.join(workspace_dir, "knowledge.json")
    
    if os.path.exists(knowledge_file):
        with open(knowledge_file, 'r') as f:
            knowledge = json.load(f)
    
    return knowledge


def format_tools_for_prompt(tools: List[Dict]) -> Dict[str, Dict]:
    """
    Format tool registry list into prompt-friendly format.
    
    Args:
        tools: List of tool dicts from workspace.registry.list_tools()
    
    Returns:
        Dict mapping tool name to tool info
    """
    formatted = {}
    for tool in tools:
        name = tool.get('name', 'unknown')
        formatted[name] = {
            'description': tool.get('description', ''),
            'usage': tool.get('usage', f'{name}(...)'),
            'path': tool.get('path', '')
        }
    return formatted


def convert_tool_to_claude_format(tool: Dict) -> Dict:
    """
    Convert AgentFactory evolved tool to Claude native format.
    
    Claude's tool format requires:
    - name: Tool name
    - description: What the tool does
    - input_schema: JSON Schema for parameters
    
    Args:
        tool: AgentFactory tool dict with name, description, parameters
        
    Returns:
        Claude-compatible tool definition
    """
    # Get parameters and build input_schema
    parameters = tool.get('parameters', {})
    required = tool.get('required', [])
    
    # If parameters is already in JSON Schema format, use it
    if isinstance(parameters, dict) and 'type' in parameters:
        input_schema = parameters
    else:
        # Convert simple parameter dict to JSON Schema
        properties = {}
        for param_name, param_info in parameters.items():
            if isinstance(param_info, dict):
                properties[param_name] = param_info
            else:
                # Simple string description
                properties[param_name] = {
                    "type": "string",
                    "description": str(param_info)
                }
        
        input_schema = {
            "type": "object",
            "properties": properties,
            "required": required
        }
    
    return {
        "name": tool.get('name', 'unknown_tool'),
        "description": tool.get('description', 'No description'),
        "input_schema": input_schema
    }


def convert_evolved_tools_to_native(tools: Dict[str, Dict]) -> List[Dict]:
    """
    Convert all evolved tools to Claude native format.
    
    Args:
        tools: Dict mapping tool name to tool info
        
    Returns:
        List of Claude-compatible tool definitions
    """
    native_tools = []
    
    for name, info in tools.items():
        # Ensure name is in the tool info
        tool_with_name = {"name": name, **info}
        native_tool = convert_tool_to_claude_format(tool_with_name)
        native_tools.append(native_tool)
    
    return native_tools


def execute_native_tool(
    tool_name: str,
    tool_input: Dict,
    evolved_tools: Dict,
    workspace_dir: str,
    tool_executor=None
) -> str:
    """
    Execute a native tool call and return result.
    
    Handles:
    - Skill tool (load skill content)
    - Evolved tools (execute via tool_executor)
    
    Args:
        tool_name: Name of the tool to execute
        tool_input: Input arguments for the tool
        evolved_tools: Dict of evolved tools
        workspace_dir: Workspace directory path
        tool_executor: Optional ToolExecutor for evolved tools
        
    Returns:
        Tool execution result as string
    """
    # Handle Skill tool
    if tool_name == "Skill":
        skill_name = tool_input.get("skill", "")
        return handle_skill_tool_call(workspace_dir, skill_name)
    
    # Handle evolved tools
    if tool_name in evolved_tools and tool_executor:
        try:
            result = tool_executor.execute(tool_name, tool_input)
            return str(result)
        except Exception as e:
            return f"Error executing {tool_name}: {str(e)}"
    
    return f"Unknown tool: {tool_name}"


def build_skill_tool(workspace_dir: str) -> Dict:
    """
    Build Skill tool definition for Claude native tool calling (learn-claude-code pattern).
    
    This enables on-demand skill loading via tool_result for cache-preserving
    injection. The model can call Skill(skill="name") to load domain knowledge.
    
    Args:
        workspace_dir: Path to workspace containing skills/
        
    Returns:
        Tool definition dict in Claude API format
    """
    skill_descriptions = get_skill_descriptions(workspace_dir)
    
    return {
        "name": "Skill",
        "description": f"""Load a skill to gain specialized knowledge for a task.

Available skills:
{skill_descriptions}

When to use:
- IMMEDIATELY when user task matches a skill description
- Before attempting domain-specific work (API patterns, authentication, etc.)

The skill content will be injected into the conversation, giving you
detailed instructions and best practices.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "Name of the skill to load"
                }
            },
            "required": ["skill"],
        },
    }


def handle_skill_tool_call(workspace_dir: str, skill_name: str) -> str:
    """
    Handle a Skill tool call by loading skill content (learn-claude-code pattern).
    
    Returns skill content wrapped in <skill-loaded> tags for cache-preserving injection.
    This goes into tool_result, not system prompt modification.
    
    Args:
        workspace_dir: Path to workspace containing skills/
        skill_name: Name of skill to load
        
    Returns:
        Skill content wrapped in tags
    """
    return run_skill(workspace_dir, skill_name)


def call_with_skill_tool(
    agent,
    messages: List[Dict],
    workspace_dir: str,
    max_skill_iterations: int = 3
) -> tuple:
    """
    Call agent with native Skill tool support (learn-claude-code pattern).
    
    Enables on-demand skill loading via tool_result for cache-preserving injection.
    The agent can call Skill(skill="name") and receive skill content as tool_result.
    
    Args:
        agent: ClaudeAgent instance with call_with_native_tools method
        messages: Current conversation messages
        workspace_dir: Path to workspace containing skills/
        max_skill_iterations: Max skill tool calls per turn
        
    Returns:
        Tuple of (final_response_text, skill_calls_made)
    """
    if not hasattr(agent, 'call_with_native_tools'):
        # Fallback: agent doesn't support native tools
        if messages:
            last_msg = messages[-1].get('content', '')
            return agent.call(last_msg), []
    
    # Build Skill tool
    skill_tool = build_skill_tool(workspace_dir)
    tools = [skill_tool]
    
    skill_calls = []
    current_messages = list(messages)
    
    for iteration in range(max_skill_iterations + 1):
        # Call Claude with native tools
        response = agent.call_with_native_tools(current_messages, tools=tools)
        
        # Check for tool use
        if response.stop_reason != "tool_use":
            # No more tool calls, extract final text
            final_text = ""
            for block in response.content:
                if hasattr(block, 'text'):
                    final_text = block.text
                    break
                elif hasattr(block, 'type') and block.type == 'text':
                    final_text = block.text
                    break
            return final_text, skill_calls
        
        # Process tool calls
        tool_results = []
        assistant_content = list(response.content)
        
        for block in response.content:
            if hasattr(block, 'type') and block.type == "tool_use":
                if block.name == "Skill":
                    skill_name = block.input.get("skill", "")
                    print(f"     🎯 Native Skill tool called: {skill_name}")
                    
                    # Load skill content
                    skill_content = handle_skill_tool_call(workspace_dir, skill_name)
                    skill_calls.append({
                        "skill": skill_name,
                        "content_length": len(skill_content)
                    })
                    
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": skill_content
                    })
        
        if not tool_results:
            break
            
        # Append assistant response and tool results
        current_messages.append({"role": "assistant", "content": assistant_content})
        current_messages.append({"role": "user", "content": tool_results})
    
    # Fallback if max iterations reached
    return "", skill_calls


def call_with_native_tools_loop(
    agent,
    messages: List[Dict],
    evolved_tools: Dict[str, Dict],
    workspace_dir: str,
    tool_executor=None,
    max_iterations: int = 10
) -> tuple:
    """
    Call agent with native tool loop for ALL tools (learn-claude-code pattern).
    
    This is the main function for running tasks with native Claude tool calling.
    It handles:
    - Skill tool (on-demand skill loading)
    - Evolved tools (tools created by proposer)
    
    Unlike call_with_skill_tool, this handles all evolved tools, not just Skill.
    
    Args:
        agent: ClaudeAgent instance with call_with_native_tools method
        messages: Current conversation messages
        evolved_tools: Dict of evolved tools from proposer
        workspace_dir: Path to workspace
        tool_executor: ToolExecutor for running evolved tools
        max_iterations: Max tool call iterations
        
    Returns:
        Tuple of (final_response_text, tool_calls_log)
    """
    if not hasattr(agent, 'call_with_native_tools'):
        # Fallback: agent doesn't support native tools
        if messages:
            last_msg = messages[-1].get('content', '')
            return agent.call(last_msg), []
    
    # Build native tools list
    native_tools = []
    
    # Add Skill tool
    native_tools.append(build_skill_tool(workspace_dir))
    
    # Add evolved tools in Claude format
    if evolved_tools:
        native_tools.extend(convert_evolved_tools_to_native(evolved_tools))
    
    tool_calls_log = []
    current_messages = list(messages)
    
    for iteration in range(max_iterations):
        # Call Claude with native tools
        response = agent.call_with_native_tools(current_messages, tools=native_tools)
        
        # Check for end of tool use
        if response.stop_reason != "tool_use":
            # Extract final text
            final_text = ""
            for block in response.content:
                if hasattr(block, 'text'):
                    final_text = block.text
                    break
                elif hasattr(block, 'type') and block.type == 'text':
                    final_text = block.text
                    break
            return final_text, tool_calls_log
        
        # Process tool calls
        tool_results = []
        assistant_content = list(response.content)
        
        for block in response.content:
            if not (hasattr(block, 'type') and block.type == "tool_use"):
                continue
            
            tool_name = block.name
            tool_input = block.input if hasattr(block, 'input') else {}
            
            print(f"     🔧 Native tool called: {tool_name}({tool_input})")
            
            # Execute tool
            result = execute_native_tool(
                tool_name=tool_name,
                tool_input=tool_input,
                evolved_tools=evolved_tools,
                workspace_dir=workspace_dir,
                tool_executor=tool_executor
            )
            
            tool_calls_log.append({
                "tool_name": tool_name,
                "tool_input": tool_input,
                "result": result[:500] if len(result) > 500 else result,
                "success": not result.startswith("Error")
            })
            
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result
            })
        
        if not tool_results:
            break
        
        # Append assistant response and tool results
        current_messages.append({"role": "assistant", "content": assistant_content})
        current_messages.append({"role": "user", "content": tool_results})
    
    # Max iterations reached
    return "", tool_calls_log


def load_appworld_data(dataset: str = 'train', num_tasks: int = None, from_end: bool = False) -> List[str]:
    """
    Load AppWorld task IDs.
    
    Args:
        dataset: Dataset split ('train', 'dev', 'test_normal', 'test_challenge')
        num_tasks: Number of tasks to load (None for all)
        from_end: If True, load tasks from the end of the dataset instead of the beginning.
                  This is useful to avoid overlap when test_challenge is used for both
                  training (first N tasks) and testing (last M tasks).
    
    Returns:
        List of task IDs
    """
    try:
        # # Set APPWORLD_ROOT if not set
        # if 'APPWORLD_ROOT' not in os.environ:
        #     os.environ['APPWORLD_ROOT'] = os.path.dirname(os.path.dirname(EXAMPLES_DIR))
        
        from appworld import load_task_ids
        task_ids = load_task_ids(dataset)
        
        if num_tasks:
            if from_end:
                task_ids = task_ids[-num_tasks:]
            else:
                task_ids = task_ids[:num_tasks]
        
        direction = "(from end)" if from_end else ""
        print(f"✓ Loaded {len(task_ids)} tasks from AppWorld {dataset} split {direction}")
        return task_ids
        
    except ImportError:
        print("⚠️ AppWorld package not available, using sample tasks")
        return None
    except Exception as e:
        print(f"⚠️ Error loading AppWorld data: {e}")
        return None


def load_combined_training_data(num_tasks: int, test_split_size: int = 50) -> List[str]:
    """
    Load combined training task IDs from multiple AppWorld datasets.
    
    Combines tasks in order to support training set scaling from 10-300:
    1. train (90 tasks)
    2. dev (57 tasks)  
    3. test_normal[50:] - excluding first 50 reserved for test (118 tasks)
    4. test_challenge (up to 35 tasks if needed to reach 300)
    
    Total available: 90 + 57 + 118 + 35 = 300 tasks
    
    Args:
        num_tasks: Number of training tasks to load (max 300)
        test_split_size: Number of test_normal tasks to reserve for testing (default 50)
        
    Returns:
        List of task IDs for training
    """
    try:
        from appworld import load_task_ids
        
        combined = []
        remaining = num_tasks
        
        # 1. Load from train (90 tasks)
        train_ids = load_task_ids('train')
        take = min(remaining, len(train_ids))
        combined.extend(train_ids[:take])
        remaining -= take
        print(f"  📦 train: {take} tasks (total: {len(combined)})")
        
        if remaining <= 0:
            print(f"✓ Loaded {len(combined)} combined training tasks")
            return combined
        
        # 2. Load from dev (57 tasks)
        dev_ids = load_task_ids('dev')
        take = min(remaining, len(dev_ids))
        combined.extend(dev_ids[:take])
        remaining -= take
        print(f"  📦 dev: {take} tasks (total: {len(combined)})")
        
        if remaining <= 0:
            print(f"✓ Loaded {len(combined)} combined training tasks")
            return combined
        
        # 3. Load from test_normal[50:] (118 tasks, excluding test set)
        test_normal_ids = load_task_ids('test_normal')
        available = test_normal_ids[test_split_size:]  # Skip first 50 (reserved for test)
        take = min(remaining, len(available))
        combined.extend(available[:take])
        remaining -= take
        print(f"  📦 test_normal[{test_split_size}:]: {take} tasks (total: {len(combined)})")
        
        if remaining <= 0:
            print(f"✓ Loaded {len(combined)} combined training tasks")
            return combined
        
        # 4. Load from test_challenge if still need more (up to 35 tasks to reach 300)
        test_challenge_ids = load_task_ids('test_challenge')
        take = min(remaining, 35)  # Cap at 35 to reach 300 total max
        combined.extend(test_challenge_ids[:take])
        print(f"  📦 test_challenge: {take} tasks (total: {len(combined)})")
        
        print(f"✓ Loaded {len(combined)} combined training tasks")
        return combined
        
    except ImportError:
        print("⚠️ AppWorld package not available")
        return []
    except Exception as e:
        print(f"⚠️ Error loading combined training data: {e}")
        return []


def evaluate_appworld_agent(
    agent,
    task_ids: List[str],
    experiment_name: str,
    max_interactions: int = 30,
    current_tools: Dict = None,
    current_skills: Dict = None,
    current_knowledge: List[Dict] = None
) -> Dict:
    """
    Evaluate an agent on AppWorld tasks.
    
    Args:
        agent: Agent to evaluate
        task_ids: List of task IDs to evaluate on
        experiment_name: Experiment name for logging
        max_interactions: Max interactions per task
        current_tools: Available tools for state injection
        current_skills: Available skills for state injection
        current_knowledge: Available knowledge for state injection
    
    Returns:
        Dict with evaluation metrics (total_score, completion_rate, results)
    """
    results = []
    total_score = 0.0
    tasks_completed = 0
    
    for i, task_id in enumerate(task_ids):
        print(f"  Evaluating task {i+1}/{len(task_ids)}: {task_id[:40]}...")
        
        # result = run_appworld_task(
        #     agent,
        #     task_id,
        #     experiment_name=experiment_name,
        #     max_interactions=max_interactions,
        #     use_real_appworld=True,
        #     current_tools=current_tools or {},
        #     current_skills=current_skills or {},
        #     current_knowledge=current_knowledge or []
        # )
        result = run_appworld_task(
            agent,
            task_id,
            experiment_name=experiment_name,
            max_interactions=max_interactions,
            use_real_appworld=True,
            current_tools=current_tools,
            current_skills=current_skills,
            current_knowledge=current_knowledge
        )
        
        results.append(result)
        total_score += result.get('score', 0)
        if result.get('task_completed', False):
            tasks_completed += 1
        
        print(f"    Score: {result.get('score', 0):.2f}, Completed: {result.get('task_completed', False)}")
    
    avg_score = total_score / len(task_ids) if task_ids else 0
    completion_rate = tasks_completed / len(task_ids) if task_ids else 0
    # TGC: Task Goal Completion - ratio of tasks with all unit tests passing (score >= 1.0)
    task_goal_completion = sum(1 for r in results if r.get('score', 0) >= 1.0) / len(task_ids) if task_ids else 0
    
    return {
        'total_tasks': len(task_ids),
        'tasks_completed': tasks_completed,
        'average_score': avg_score,
        'completion_rate': completion_rate,
        'task_goal_completion': task_goal_completion,
        'results': results
    }


def run_appworld_task(
    agent,
    task_id: str,
    experiment_name: str = "agentfactory",
    max_interactions: int = 30,
    use_real_appworld: bool = True,
    current_tools: Dict[str, Dict] = None,
    current_skills: Dict[str, Dict] = None,
    current_knowledge: List[Dict] = None,
    workspace = None  # NEW: Optional ToolWorkspace for tool execution
) -> Dict:
    """
    Execute an AppWorld task with real or simulated environment.
    
    The agent uses tools, skills, and knowledge from current state to solve tasks.
    Supports multi-turn conversation with proper context management.
    
    Args:
        agent: The agent to use for task execution
        task_id: AppWorld task ID
        experiment_name: Experiment name for logging
        max_interactions: Maximum number of interactions
        use_real_appworld: Whether to use real AppWorld environment
        current_tools: Dict of available tools {name: {description, usage}}
        current_skills: Dict of available skills {name: {description, content}}
        current_knowledge: List of knowledge entries [{when_to_use, content}]
        workspace: Optional ToolWorkspace for tool execution (preferred over current_tools)
    
    Returns:
        Dict with task_id, trajectory, score, task_completed, tool_execution_log
    """
    import re
    
    trajectory = []
    
    def extract_code(text: str) -> tuple:
        """Extract Python code from markdown code blocks, hallucinated formats, or raw code.
        
        Returns:
            tuple: (extracted_code, fixed_text) where:
                - extracted_code: The extracted Python code
                - fixed_text: The text with code properly wrapped in ```python\n...\n```
        
        Handles Claude's hallucinated XML tool-use markup:
        - <function_calls><invoke name="python"><parameter name="code">...</parameter></invoke></function_calls>
        - <invoke name="...">...</invoke>
        - <parameter name="code">...</parameter>
        """
        import re
        
        original_text = text
        
        # Step 0: Strip hallucinated XML wrapper tags FIRST before other extraction
        # This handles: <function_calls>...<parameter name="code">ACTUAL_CODE</parameter>...</function_calls>
        cleaned_text = text
        
        # Extract code from <parameter name="code">...</parameter> if present
        param_code_regex = r'<parameter name="code">\s*(.*?)\s*</parameter>'
        param_matches = re.findall(param_code_regex, text, flags=re.DOTALL)
        if param_matches:
            # Use the extracted code content instead
            cleaned_text = "\n\n".join(m.strip() for m in param_matches)
            # If the extracted content looks like valid code, return it
            if cleaned_text and not cleaned_text.startswith('<'):
                # Remove any remaining XML artifacts
                cleaned_text = re.sub(r'</?(?:function_calls|invoke|parameter)[^>]*>', '', cleaned_text)
                code = cleaned_text.strip()
                # Wrap in proper markdown format
                fixed_text = f"```python\n{code}\n```"
                return (code, fixed_text)
        
        # Step 1: Try standard markdown format: ```python ... ```
        full_code_regex = r"```python\n(.*?)```"
        output_code = ""
        match_end = 0
        fixed_text = original_text
        
        # Handle multiple code blocks (like extract_code_and_fix_content)
        for re_match in re.finditer(full_code_regex, cleaned_text, flags=re.DOTALL):
            code = re_match.group(1).strip()
            # Return first match only (similar to ignore_multiple_calls=True)
            fixed_text = cleaned_text[:re_match.end()]
            return (code, fixed_text)
        
        # Check for partial code block at end (no terminating ```)
        partial_code_regex = r".*```python\n(.*)"
        partial_match = re.match(partial_code_regex, cleaned_text, flags=re.DOTALL)
        if partial_match:
            code = partial_match.group(1).strip()
            # Fix the text by adding closing ```
            fixed_text = cleaned_text
            if not fixed_text.endswith("\n"):
                fixed_text = fixed_text + "\n"
            fixed_text = fixed_text + "```"
            return (code, fixed_text)
        
        # Step 2: Try plain code blocks (```\n...\n```)
        plain_regex = r"```\n(.*?)```"
        for re_match in re.finditer(plain_regex, cleaned_text, flags=re.DOTALL):
            code = re_match.group(1).strip()
            fixed_text = cleaned_text[:re_match.end()]
            return (code, fixed_text)
        
        # Step 3: Try <action> format: <action>```python ... ```</action>
        action_regex = r'<action>.*?```python\n(.*?)```.*?</action>'
        matches = re.findall(action_regex, cleaned_text, flags=re.DOTALL)
        if matches:
            code = matches[0].strip()
            fixed_text = f"```python\n{code}\n```"
            return (code, fixed_text)
        
        # Step 4: Try simple <action> format without markdown
        simple_action_regex = r'<action>\s*(.*?)\s*</action>'
        matches = re.findall(simple_action_regex, cleaned_text, flags=re.DOTALL)
        if matches:
            code = matches[0].strip()
            # Strip any remaining XML tags from the code
            code = re.sub(r'</?(?:function_calls|invoke|parameter)[^>]*>', '', code)
            code = code.strip()
            fixed_text = f"```python\n{code}\n```"
            return (code, fixed_text)
        
        # Step 5: Strip ALL XML-like tags as a last resort before AWM approach
        # This catches any remaining <invoke>, <function_calls>, etc.
        stripped_text = re.sub(r'</?(?:function_calls|invoke|parameter|action)[^>]*>', '', cleaned_text)
        stripped_text = stripped_text.strip()
        
        # Step 6: AWM approach - detect code-like content
        text_to_scan = stripped_text if stripped_text else cleaned_text
        lines = text_to_scan.strip().split('\n')
        code_lines = []
        in_code = False
        for line in lines:
            stripped = line.strip()
            # Skip lines that look like XML artifacts
            if stripped.startswith('<') and '>' in stripped:
                continue
            # Detect code start
            if stripped.startswith('#') or stripped.startswith('print(') or stripped.startswith('apis.') or ('=' in stripped and not stripped.startswith('**')):
                in_code = True
            if in_code:
                # Stop at narrative text (no assignment, no function call, no comment)
                if stripped and not stripped.startswith('#') and '(' not in stripped and '=' not in stripped and not stripped.startswith('import'):
                    if len(code_lines) > 0:
                        break
                code_lines.append(line)
        
        if code_lines:
            code = '\n'.join(code_lines).strip()
            fixed_text = f"```python\n{code}\n```"
            return (code, fixed_text)
        
        # No code found - return empty code and original text
        return ("", original_text)
    
    def get_reward(world) -> float:
        """Calculate task completion score."""
        tracker = world.evaluate()
        num_passes = len(tracker.passes)
        num_failures = len(tracker.failures)
        return num_passes / (num_passes + num_failures) if (num_passes + num_failures) > 0 else 0.0
    
    def build_task_prompt(task_instruction: str, tools: Dict, skills: Dict, knowledge: List[Dict], workspace=None) -> str:
        """Build initial task prompt with state injection.
        
        Args:
            task_instruction: The task instruction text
            tools: Dict of tools (fallback if workspace not provided)
            skills: Dict of skills
            knowledge: List of knowledge entries
            workspace: Optional ToolWorkspace for consistent tool signature format
        """
        prompt_parts = [f"# Task\n\n{task_instruction}"]
        
        # Inject available tools with usage patterns
        # Prefer workspace-based tool signatures (consistent TOOL_CALL: format)
        if workspace is not None:
            tools_section = build_tool_signatures_section(workspace)
            if tools_section:
                prompt_parts.append(tools_section)
        elif tools:
            tools_section = "\n## Available Tools\n\n"
            tools_section += "You have specialized tools to help with this task. Use them when relevant.\n\n"
            
            for name, info in tools.items():
                print(f"build_task_prompt -> tool_selection: {name} {info}")
                desc = info.get('description', 'No description')
                usage = info.get('usage', f'{name}(...)')
                code_example = info.get('code_example', '')
                
                tools_section += f"### {name}\n"
                tools_section += f"**Purpose:** {desc}\n"
                tools_section += f"**Usage:** `TOOL_CALL: {usage}`\n"
                if code_example:
                    tools_section += f"**Example:**\n```python\n{code_example}\n```\n"
                tools_section += "\n"
            
            prompt_parts.append(tools_section)
        
        # Separate skills by scope (tactical patches vs strategic skills)
        if skills:
            tactical_skills = []
            strategic_skills = []
            
            for name, skill in skills.items():
                scope = skill.get('scope', 'strategic_skill')
                if scope == 'tactical_patch':
                    tactical_skills.append((name, skill))
                else:
                    strategic_skills.append((name, skill))
            
            # Tactical patches (behavioral guidance) - show prominently
            if tactical_skills:
                patches_section = "\n## ⚠️ Behavioral Guidance (Tactical Patches)\n\n"
                patches_section += "**Apply these patterns when encountering matching situations:**\n\n"
                
                for name, skill in tactical_skills:
                    triggers = skill.get('triggers', [])
                    triggers_str = ', '.join(triggers) if triggers else 'General guidance'
                    instructions = skill.get('instructions', skill.get('description', ''))
                    
                    patches_section += f"### When: {triggers_str}\n"
                    patches_section += f"{instructions}\n\n"
                
                prompt_parts.append(patches_section)
            
            # Strategic skills (procedures/workflows)
            if strategic_skills:
                skills_section = "\n## Learned Skills (Procedures)\n\n"
                skills_section += "Use these step-by-step procedures when relevant:\n\n"
                
                for name, skill in strategic_skills:  # Limit to top 5
                    desc = skill.get('description', '')
                    instructions = skill.get('instructions', '')
                    triggers = skill.get('triggers', [])
                    triggers_str = ', '.join(triggers[:3]) if triggers else 'N/A'
                    
                    skills_section += f"### {name}\n"
                    skills_section += f"**When to use:** {triggers_str}\n"
                    if instructions:
                        # Show first 500 chars of instructions
                        instr_preview = instructions
                        skills_section += f"**Steps:**\n{instr_preview}\n"
                    skills_section += "\n"
                
                # if len(strategic_skills) > 5:
                #     skills_section += f"*...and {len(strategic_skills) - 5} more skills available*\n"
                
                prompt_parts.append(skills_section)
        
        # Inject relevant knowledge (factual reference)
        if knowledge:
            knowledge_section = "\n## Reference Knowledge\n\n"
            
            for entry in knowledge[:5]:  # Limit to 5 most relevant
                topic = entry.get('topic', entry.get('when_to_use', 'General'))
                content = entry.get('content', '')[:300]
                entry_type = entry.get('type', 'info')
                
                knowledge_section += f"### {topic}\n"
                knowledge_section += f"{content}\n\n"
            
            prompt_parts.append(knowledge_section)
        
        print(f"prompt_parts: {prompt_parts}")
        return "\n".join(prompt_parts)
    
    try:
        from appworld import AppWorld
        
        with AppWorld(task_id=task_id, experiment_name=experiment_name) as world:
            # Get task instruction and supervisor profile (ReMe format)
            task_instruction = world.task.instruction
            
            # Get supervisor profile for context (critical for performance!)
            try:
                supervisor = world.apis.supervisor.show_profile()
            except Exception as e:
                print(f"     ⚠️ Could not get supervisor profile: {e}")
                supervisor = {'first_name': '', 'last_name': '', 'email': '', 'phone_number': ''}
            
            # Build initial prompt using build_full_prompt (ReMe format with tools/skills/knowledge)
            # initial_prompt = build_full_prompt(
            #     supervisor=supervisor,
            #     instruction=task_instruction,
            #     tools=current_tools,
            #     skills=current_skills,
            #     knowledge=current_knowledge
            # )

            # system_prompt = build_system_prompt(
            #     tools=current_tools,
            #     skills=current_skills,
            #     knowledge=current_knowledge
            # )

            initial_user_task_prompt = build_user_task_prompt(
                supervisor=supervisor,
                instruction=task_instruction
            )


            
            # If workspace provided, append workspace-based tool signatures (preferred format)
            if workspace is not None:
                workspace_tools_section = build_tool_signatures_section(workspace)
                if workspace_tools_section:
                    initial_prompt += "\n" + workspace_tools_section
            
            # Debug: Print what's being injected
            print(f"\n  📦 Agent Resources for this task:")
            
            # Log workspace tools if available (preferred)
            if workspace is not None and hasattr(workspace, 'registry'):
                tool_list = workspace.registry.list_tools()
                if tool_list:
                    print(f"     🔧 Workspace Tools ({len(tool_list)}):")
                    for tool_info in tool_list[:5]:
                        print(f"        - {tool_info['name']}: {tool_info.get('description', '')[:50]}...")
                    if len(tool_list) > 5:
                        print(f"        ... and {len(tool_list) - 5} more")
                else:
                    print(f"     🔧 Workspace Tools: None")
            elif current_tools:
                print(f"     🔧 Tools ({len(current_tools)}):")
                for name, info in list(current_tools.items())[:5]:
                    print(f"        - {name}: {info.get('description', '')}...")
                if len(current_tools) > 5:
                    print(f"        ... and {len(current_tools) - 5} more")
            else:
                print(f"     🔧 Tools: None")
            
            if current_skills:
                print(f"     📚 Skills ({len(current_skills)}):")
                for name, skill in list(current_skills.items())[:5]:
                    print(f"        - {name}: {skill.get('description', '')}...")
                if len(current_skills) > 5:
                    print(f"        ... and {len(current_skills) - 5} more")
            else:
                print(f"     📚 Skills: None")
                
            if current_knowledge:
                print(f"     💡 Knowledge ({len(current_knowledge)}):")
                for k in current_knowledge[:3]:
                    print(f"        - {k.get('topic', k.get('when_to_use', 'unknown'))}...")
                if len(current_knowledge) > 3:
                    print(f"        ... and {len(current_knowledge) - 3} more")
            else:
                print(f"     💡 Knowledge: None")
            
            # trajectory.append({"role": "system", "content": system_prompt})
            trajectory.append({"role": "user", "content": initial_user_task_prompt})
            
            # Conversation history for multi-turn
            conversation = [{"role": "user", "content": initial_user_task_prompt}]
            tool_execution_log = []
            
            for step in range(max_interactions):
                print(f"\n  📍 Step {step + 1}/{max_interactions}")
                
                # # Build context from full conversation history (like ReMe)
                # if step == 0:
                #     current_input = initial_prompt
                # else:
                #     # Use full conversation history
                #     context_parts = []
                #     for msg in conversation:  # Full history
                #         role = msg['role']
                #         content = msg['content']
                #         context_parts.append(f"[{role.upper()}]\n{content}")
                #     current_input = "\n\n".join(context_parts)

                current_input = conversation
                
                print(f"     📝 Context length: {len(current_input)} chars")
                
                # Get agent response
                # Use execute_with_tools if agent has tool_executor, otherwise use call
                if hasattr(agent, 'execute_with_tools') and hasattr(agent, 'tool_executor') and agent.tool_executor:
                    print(f"     🔧 Calling agent with tool execution...")
                    response, tool_log = agent.execute_with_tools(current_input)
                    tool_execution_log.extend(tool_log)
                    
                    # Add tool execution log to trajectory for observability
                    if tool_log:
                        print(f"     🛠️  Tools called: {len(tool_log)}")
                        tool_summary = []
                        for tc in tool_log:
                            tool_name = tc.get('tool_name', 'unknown')
                            success = tc.get('success', False)
                            result = tc.get('result', '')
                            result_str = str(result)
                            
                            # Show more detailed result
                            print(f"\n        📍 Tool: {tool_name}")
                            print(f"           Status: {'✓ SUCCESS' if success else '✗ FAILED'}")
                            print(f"           Response ({len(result_str)} chars):")
                            # Show full response up to 500 chars
                            # if len(result_str) > 500:
                            #     print(f"           {result_str[:500]}...")
                            # else:
                            #     print(f"           {result_str}")
                            print(f"           {result_str}")
                            
                            tool_summary.append(f"- {tool_name}: {'✓' if success else '✗'} {result_str[:200]}")
                        
                        # tool_msg = f"[Tool Execution]\n" + "\n".join(tool_summary)
                        tool_msg = "\n".join(tool_summary)
                        trajectory.append({"role": "tool", "content": tool_msg, "type": "tool_execution"})
                        conversation.append({"role": "tool", "content": tool_msg})
                else:
                    print(f"     📨 Calling agent (no tool executor)...")
                    print(f"     📨 Agent input: {current_input}")
                    response = agent.call(current_input)
                
                print(f"     🤖 Agent response: {len(response)} chars")
                print(f"        Preview: {response}...")
                
                # trajectory.append({"role": "assistant", "content": response})
                # conversation.append({"role": "assistant", "content": response})
                
                # Check for RETRIEVE blocks (skill/knowledge retrieval)
                from examples.appworld_agent_system_prompt import parse_retrieve_block
                
                retrieve_results, remaining_response = parse_retrieve_block(
                    response, 
                    current_skills,  # Skills dict from context
                    current_knowledge  # Knowledge list from context
                )
                
                if retrieve_results:
                    print(f"     📚 RETRIEVE block found! Processing skill/knowledge requests...")
                    print(f"        Retrieved: {len(retrieve_results)} chars of context")
                    
                    # Send retrieval results back to LLM
                    retrieve_msg = f"[Retrieval Results]\n\n{retrieve_results}"
                    trajectory.append({"role": "user", "content": retrieve_msg, "type": "retrieval"})
                    conversation.append({"role": "user", "content": retrieve_msg})
                    
                    # If there's no code in remaining response, continue to next turn
                    if not remaining_response or remaining_response.strip() == '':
                        print(f"     📩 Sent retrieval results, waiting for code...")
                        continue
                    
                    # Use remaining response for code extraction
                    response = remaining_response
                
                # Extract and execute code in AppWorld environment
                code, fixed_response = extract_code(response)
                trajectory.append({"role": "assistant", "content": fixed_response})
                conversation.append({"role": "assistant", "content": fixed_response})
                
                if code and code != fixed_response:
                    print(f"     💻 Code extracted: {len(code)} chars")
                    print(f"        Preview: {code}...")
                    
                    # Execute in AppWorld
                    output = world.execute(code)
                    print(f"     📤 AppWorld output: {len(str(output))} chars")
                    print(f"        Preview: {str(output)}...")
                    
                    # Format output for next turn
                    # output_msg = f"Output:\n```\n{output}\n```"
                    output_msg = "Output:\n```\n" + output + "```\n\n"
                    trajectory.append({"role": "user", "content": output_msg})
                    conversation.append({"role": "user", "content": output_msg})
                    
                    # Check for task completion
                    if world.task_completed():
                        print(f"     ✅ Task completed!")
                        break
                else:
                    print(f"     ⚠️  No code extracted from response")
                    # No code to execute - check if task is done or agent is stuck
                    if world.task_completed():
                        print(f"     ✅ Task completed!")
                        break
                    
                    # Provide feedback to continue
                    feedback = "No code was provided. Please provide Python code to execute for the task."
                    trajectory.append({"role": "user", "content": feedback})
                    conversation.append({"role": "user", "content": feedback})
                    print(f"     📩 Sent feedback to agent")
            
            score = get_reward(world)
            task_completed = world.task_completed()
            
            print(f"\n  📊 Final score: {score:.2f}")
            print(f"  📊 Task completed: {task_completed}")
            print(f"  📊 Total steps: {len([t for t in trajectory if t['role'] == 'assistant'])}")
            print(f"  📊 Total tool calls: {len(tool_execution_log)}")
            
            return {
                'task_id': task_id,
                'task_description': task_instruction,  # Include for verifier/analysis
                'trajectory': trajectory,
                'score': score,
                'task_completed': task_completed,
                'tool_execution_log': tool_execution_log,
                'num_steps': len([t for t in trajectory if t['role'] == 'assistant'])
            }
            
    except ImportError:
        print(f"⚠️ AppWorld not available, simulating task {task_id}")
        use_real_appworld = False
    except Exception as e:
        print(f"⚠️ AppWorld error for {task_id}: {e}")
        import traceback
        traceback.print_exc()
        return {
            'task_id': task_id,
            'trajectory': trajectory,
            'score': 0.0,
            'task_completed': False,
            'error': str(e)
        }







def main(
    main_model: str = None,
    proposer_model: str = None,
    api_key: str = None,
    num_tasks: int = 10,
    num_test_tasks: int = 10,
    test_dataset: str = 'test_normal',
    batch_size: int = 5,
    session_id: str = None,
    test_only: bool = False,
    resume_from: int = 0,
    observation_window: int = 10,
    min_pattern_occurrences: int = 3,
    on_policy: bool = True,
    use_persistent_observer: bool = True,
    use_tool_enabled_proposer: bool = True,
    verbose: bool = True,
    eval_vanilla: bool = True,
    eval_evolved: bool = True
):
    """
    Run the AppWorld meta-evolver system aligned with hybrid architecture.
    
    Args:
        main_model: Model name for main agent (auto-detects provider from prefix)
        proposer_model: Model name for proposer (defaults to main model)
        api_key: API key (or set OPENAI_API_KEY, GOOGLE_API_KEY, or ANTHROPIC_API_KEY)
        num_tasks: Number of tasks to process
        batch_size: Samples per batch
        session_id: Session ID for tool isolation
        test_only: Skip training, only evaluate
        resume_from: Resume from sample index
        observation_window: Window for pattern detection
        min_pattern_occurrences: Min occurrences for pattern
        on_policy: Enable on-policy learning
        use_persistent_observer: Use file-based observer
        use_tool_enabled_proposer: Use tool-enabled proposer
        verbose: Enable verbose output
    """
    # Detect provider and validate API key
    provider = detect_provider(main_model)
    
    if not api_key:
        # Check provider-specific environment variables
        if provider == "openai":
            api_key = os.environ.get('OPENAI_API_KEY')
        elif provider == "google":
            api_key = os.environ.get('GOOGLE_API_KEY')
        else:
            api_key = os.environ.get('ANTHROPIC_API_KEY')
    
    if not api_key:
        env_var = {
            "openai": "OPENAI_API_KEY",
            "google": "GOOGLE_API_KEY", 
            "anthropic": "ANTHROPIC_API_KEY"
        }.get(provider, "API_KEY")
        raise ValueError(f"{provider.upper()} API key required (--api-key or {env_var})")

    
    # Set default models
    # default_main_model = "claude-haiku-4-5-20251001"
    # default_proposer_model = "claude-sonnet-4-5-20250929"
    
    # main_model = claude_model or default_main_model
    # prop_model = proposer_model or default_proposer_model

    main_model = main_model
    prop_model = proposer_model
    
    print("=" * 80)
    print("AppWorld Meta-Evolver Learning System (Multi-Provider)")
    print("=" * 80)
    print(f"Provider: {provider.upper()} 🔌")
    print(f"Main Agent Model: {main_model}")

    if prop_model != main_model:
        print(f"Proposer Model: {prop_model} 🔍")
    print(f"Mode: {'TEST ONLY' if test_only else 'TRAIN + TEST'}")
    print(f"Tasks: {num_tasks}")
    print(f"Batch size: {batch_size}")
    print(f"Learning Mode: {'ON-POLICY 🔄' if on_policy else 'OFF-POLICY'}")
    print(f"Observer: {'Persistent 💾' if use_persistent_observer else 'Memory-based'}")
    print(f"Proposer: {'Tool-Enabled 🔍' if use_tool_enabled_proposer else 'Standard'}")
    print("=" * 80 + "\n")
    
    # Generate session ID
    if session_id is None:
        session_id = f"appworld_{ToolWorkspace.generate_session_id(model_name=main_model, train_samples=num_tasks)}"
    
    print(f"Session ID: {session_id}\n")
    
    # Initialize Tool Workspace
    print("Step 1: Initializing Workspace...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    tools_sandbox_dir = os.path.join(project_root, "tools_sandbox")
    
    workspace = ToolWorkspace(
        session_id=session_id,
        base_dir=tools_sandbox_dir
    )
    print(f"✓ Workspace created: {workspace.workspace_dir}")
    
    # Validate tools
    validation_result = workspace.registry.validate_tools()
    if validation_result["removed"]:
        print(f"⚠️  Cleaned up {len(validation_result['removed'])} invalid tool(s)")
    print(f"  Valid tools: {len(validation_result['valid'])}\n")
    
    # Create agents
    print("Step 2: Creating agents...")
    
    # Build enhanced prompt with state injection support
    # Uses APPWORLD_SYSTEM_PROMPT_WITH_STATE with RETRIEVE tags
    # enhanced_system_prompt = build_prompt_with_state(
    #     tools={},  # Will be populated with current tools later
    #     skills={},  # Will be populated with current skills later
    #     knowledge=[]  # Will be populated with current knowledge later
    # )
    system_prompt = build_system_prompt(
                tools=None,
                skills=None,
                knowledge=None
            )
    
    base_agent = create_agent(
        model_name=main_model,
        api_key=api_key,
        system_prompt=system_prompt,
        provider=provider
    )
    base_agent.original_system_prompt = system_prompt
    print(f"✓ Created base agent: {main_model} ({provider})")
    
    proposer_agent = None
    if use_tool_enabled_proposer:
        # Use ToolEnabledClaudeAgent for proposer (like run_hybrid_claude.py)
        proposer_agent = ToolEnabledClaudeAgent(
            model_name=prop_model,
            api_key=api_key,
            system_prompt="You are an expert AI improvement agent that analyzes failures and proposes improvements.",
            max_tokens=8192,
            temperature=0.7,
            tool_executor=None,  # Proposer doesn't execute tools directly
            max_tool_iterations=3
        )
        print(f"✓ Created proposer agent: {prop_model} (ToolEnabled)")
    
    # Create observer
    print("\nStep 3: Creating modules...")
    if not use_persistent_observer:
        print("Using AppWorldObserver (score-based + persistent)...")
        observer = AppWorldObserver(workspace_dir=workspace.workspace_dir, verbose=verbose)
        print(f"  Observations saved to: {workspace.workspace_dir}/observations/")
    else:
        print("Using PersistentObserver...")
        observer = PersistentObserver(workspace_dir=workspace.workspace_dir)
    
    # Create proposer
    if use_tool_enabled_proposer:
        print("Using ToolEnabledProposer (with analysis tools)...")
        proposer = ToolEnabledProposer(
            workspace_dir=workspace.workspace_dir,
            tool_builder=workspace.builder,
            tool_registry=workspace.registry,
            observation_window=observation_window,
            enable_tool_evolution=True,
            enable_patches=True,
            proposer_agent=proposer_agent,
            verbose=verbose
        )
        if proposer_agent:
            print(f"  Proposer using enhanced model: {prop_model} 🔍")
        else:
            print(f"  Proposer using main agent model: {main_model}")
    else:
        print("Using HybridToolPatchProposer (standard)...")
        proposer = HybridToolPatchProposer(
            observation_window=observation_window,
            min_pattern_occurrences=min_pattern_occurrences,
            enable_tool_evolution=True,
            enable_patches=True
        )
    
    # Create updater
    updater = HybridToolPatchUpdater(
        tool_builder=workspace.builder,
        tool_registry=workspace.registry,
        verbose=verbose
    )
    
    # Load existing patches if resuming
    patches_file = os.path.join(workspace.workspace_dir, "patches.json")
    if resume_from > 0 and os.path.exists(patches_file):
        updater.load_patches(patches_file)
        print(f"✓ Loaded {len(updater.get_patches())} existing patches")
    
    # Load AppWorld tasks (or fall back to sample tasks)
    print("\nStep 4: Loading tasks...")
    task_ids = load_combined_training_data(num_tasks=num_tasks)
    use_real_appworld = task_ids is not None and len(task_ids) > 0
    
    print(task_ids)
    print(f"\n📋 Tasks: {len(task_ids)} {'(real AppWorld)' if use_real_appworld else '(simulated)'}")
    
    if test_only:
        print("\n⏭️ TEST ONLY mode - skipping training\n")
    else:
        # Training phase
        print("\n" + "=" * 80)
        print("TRAINING PHASE: Learn Features & Behavioral Patterns")
        print("=" * 80)
        print(f"Processing {len(task_ids)} tasks...")
        print(f"Batch size: {batch_size}")
        if resume_from > 0:
            print(f"Resuming from: {resume_from}\n")
        
        # Initialize agent for on-policy learning
        if on_policy:
            print(f"🔄 ON-POLICY MODE: Using {provider.upper()} ToolEnabled agent\n")
            current_agent = create_tool_enabled_agent(
                model_name=base_agent.model_name,
                api_key=api_key,
                system_prompt=base_agent.system_prompt,
                max_tokens=4096,
                temperature=0.7,
                tool_executor=workspace.executor,
                max_tool_iterations=3,
                provider=provider
            )
            current_agent.original_system_prompt = base_agent.original_system_prompt
        else:
            current_agent = base_agent
        
        # Track improvements
        improvement_history = []
        tools_created = 0
        tools_evolved = 0
        skill_added = 0
        skill_evolved = 0
        experience_added = 0
        experience_evolved = 0
        patches_added = 0
        
        # Process in batches
        tasks_to_process = task_ids[resume_from:]
        num_batches = (len(tasks_to_process) + batch_size - 1) // batch_size
        
        for batch_idx in range(num_batches):
            start_idx = batch_idx * batch_size
            end_idx = min(start_idx + batch_size, len(tasks_to_process))
            batch_tasks = tasks_to_process[start_idx:end_idx]
            
            actual_idx = resume_from + start_idx
            
            print(f"\n--- Batch {batch_idx + 1}/{num_batches} ---")
            print(f"Processing tasks {actual_idx} to {actual_idx + len(batch_tasks) - 1}")
            print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            
            batch_observations = []
            
            # Execute tasks
            for i, task_id in enumerate(batch_tasks):
                task_idx = actual_idx + i
                task_display = task_id if len(task_id) < 50 else task_id[:50] + "..."
                print(f"Task {task_idx + 1}/{resume_from + len(tasks_to_process)}: {task_display}")
                
                # Load current state for this task
                current_tools = format_tools_for_prompt(workspace.registry.list_tools())
                current_skills = load_skills_from_workspace(workspace.workspace_dir)
                current_knowledge = load_knowledge_from_workspace(workspace.workspace_dir)
                
                # Use real AppWorld or simulation
                result = run_appworld_task(
                    current_agent,
                    task_id,
                    experiment_name=session_id,
                    max_interactions=30,
                    use_real_appworld=use_real_appworld,
                    current_tools=current_tools,
                    current_skills=current_skills,
                    current_knowledge=current_knowledge
                )
                print(f"result: score={result.get('score', 0):.2f}, completed={result.get('task_completed', False)}")
                
                # Record observation
                obs = observer.observe_task(
                    task_id=result['task_id'],
                    trajectory=result['trajectory'],
                    score=result['score'],
                    task_completed=result['task_completed']
                )
                batch_observations.append(obs.to_dict() if hasattr(obs, 'to_dict') else obs)
            
            # Get batch statistics
            stats = observer.get_statistics()
            print(f"\nBatch stats: avg_score={stats['average_score']:.2f}, "
                  f"completion_rate={stats['completion_rate']:.1%}")
            
            # Propose improvements
            if stats['average_score'] < 0.9:
                print("\n🔍 Generating improvement proposal...")
                
                # Prepare context for proposer
                context = {
                    'tool_registry': workspace.registry,
                    'current_tools': workspace.registry.list_tools(),
                    'patches': updater.get_patches()
                }
                
                if use_tool_enabled_proposer:
                    # ToolEnabledProposer.propose(agent, obs, context)
                    proposal = proposer.propose(
                        current_agent,
                        batch_observations,
                        context
                    )
                else:
                    # HybridToolPatchProposer.propose(observations, current_tools, current_patches)
                    proposal = proposer.propose(
                        batch_observations,
                        workspace.registry.list_tools(),
                        updater.get_patches()
                    )
                
                if proposal:
                    action = proposal.get('action', proposal.get('proposal_type', 'SKIP'))
                    print(f"  Proposal action: {action}")
                    
                    if proposal.get('reasoning'):
                        reasoning = proposal['reasoning']
                        print(f"  Reasoning: {reasoning[:200]}...")
                    
                    # Handle proposal based on action type
                    # ToolEnabledProposer returns _final_agent with changes already applied
                    if use_tool_enabled_proposer and proposal.get('_final_agent') is not None:
                        current_agent = proposal['_final_agent']
                        
                        if action in ['CREATE_TOOL']:
                            print(f"  ✨ Created feature extraction tool!")
                            tools_created += 1
                        elif action in ['EVOLVE_TOOL']:
                            print(f"  🔄 Evolved feature tool!")
                            tools_evolved += 1
                        elif action in ['CREATE_SKILL', 'ADD_SKILL']:
                            print(f"  ✨ Created skill!")
                            skill_added += 1
                        elif action in ['EVOLVE_SKILL']:
                            print(f"  🔄 Evolved skill!")
                            skill_evolved += 1
                        elif action in ['CREATE_EXPERIENCE', 'ADD_KNOWLEDGE']:
                            print(f"  ✨ Created experience/knowledge!")
                            experience_added += 1
                        elif action in ['EVOLVE_EXPERIENCE']:
                            print(f"  🔄 Evolved experience!")
                            experience_evolved += 1
                        elif action == 'ADD_PATCH':
                            print(f"  📝 Added behavioral guidance patch!")
                            patches_added += 1
                            if hasattr(proposer, 'get_patches'):
                                updater.patches = proposer.get_patches()
                        elif action in ['SKIP', 'NO_ACTION']:
                            print(f"  ⏭️  No action needed")
                        else:
                            print(f"Invalid action {action}!")

                        if proposal.get('_validation_passed'):
                            print(f"  ✅ Verified and committed")
                        else:
                            print(f"  ⚠️ Changes reverted due to verification failure")
                        
                        
                    # elif action in ['CREATE_TOOL', 'EVOLVE_TOOL', 'ADD_PATCH', 'BUILD_TOOL', 'BEHAVIORAL_PATCH']:
                    #     # Fall back to updater for non-tool-enabled proposer
                    #     updated_agent = updater.update(current_agent, proposal)
                    #     if updated_agent:
                    #         current_agent = updated_agent
                    #         if 'evolve' in action.lower():
                    #             tools_evolved += 1
                    #         elif 'tool' in action.lower():
                    #             tools_created += 1
                    #         else:
                    #             patches_added += 1
                    # else:
                    #     print(f"  ⏭️  No action needed")
                    
                    # Update agent system prompt with current state (on-policy learning)
                    if on_policy:
                        # Load current state
                        current_tools = format_tools_for_prompt(workspace.registry.list_tools())
                        current_skills = load_skills_from_workspace(workspace.workspace_dir)
                        current_knowledge = load_knowledge_from_workspace(workspace.workspace_dir)
                        current_patches = updater.get_patches()
                        
                        # Build enhanced prompt with full state
                        enhanced_prompt = build_prompt_with_state(
                            tools=current_tools,
                            skills=current_skills,
                            knowledge=current_knowledge
                        )
                        
                        # Append patches if any
                        if current_patches:
                            patch_text = "\n".join([
                                f"- {p.get('name', 'patch')}: {p.get('description', '')}"
                                for p in current_patches
                            ])
                            enhanced_prompt += f"\n\n## Behavioral Patches\n{patch_text}"
                        
                        current_agent.system_prompt = enhanced_prompt
                        print(f"  📋 State: {len(current_tools)} tools, {len(current_skills)} skills, {len(current_knowledge)} knowledge")
                
                improvement_history.append({
                    'batch': batch_idx + 1,
                    'accuracy': stats['average_score'],
                    'action': action if proposal else None,
                    'committed': proposal.get('_committed', False) if proposal else False
                })
            
            # Save patches after each batch
            updater.save_patches(patches_file)
    
    # Final results
    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80)
    
    final_stats = observer.get_statistics()
    print(f"\n📊 Execution Statistics:")
    print(f"  Total tasks: {final_stats['total_tasks']}")
    print(f"  Completed: {final_stats.get('completed_tasks', 0)}")
    print(f"  Average score: {final_stats['average_score']:.2%}")
    print(f"  Completion rate: {final_stats['completion_rate']:.2%}")
    
    # print(f"\n🧬 Evolution Statistics:")
    # print(f"  Tools created: {tools_created}")
    # print(f"  Tools evolved: {tools_evolved}")
    # print(f"  Skill added:   {skill_added}")
    # print(f"  Skill evolved: {skill_evolved}")
    # print(f"  Experience added: {experience_added}")
    # print(f"  Experience evolved: {experience_added}")
    # print(f"  Patches added: {patches_added}")
    # print(f"  Total tools: {len(workspace.registry.list_tools())}")
    # print(f"  Total patches: {len(updater.get_patches())}")
    
    # =========================================================================
    # TESTING PHASE: Compare vanilla vs evolved agent
    # =========================================================================
    print("\n" + "=" * 80)
    print("TESTING PHASE: Vanilla vs Evolved Agent Comparison")
    print("=" * 80)
    
    # Load test tasks (different from training)
    # For test_challenge, load from end to avoid overlap with training (which uses first 35)
    from_end = (test_dataset == 'test_challenge')
    test_task_ids = load_appworld_data(dataset=test_dataset, num_tasks=num_test_tasks, from_end=from_end)
    
    if test_task_ids and len(test_task_ids) > 0:
        print(f"\n📊 Evaluating on {len(test_task_ids)} test tasks...")
        if(len(test_task_ids) > num_test_tasks):
            print(f"\nWarning: Only {num_test_tasks} test tasks available, using all available tasks.")
            test_task_ids = test_task_ids[:num_test_tasks]
        
        # Create vanilla agent (original system prompt, no tools/patches)
        vanilla_results = None
        evolved_results = None
        
        if eval_vanilla:
            print("\n1️⃣ Evaluating VANILLA agent (no enhancements)...")

            vanilla_agent = create_tool_enabled_agent(
                model_name=main_model,
                api_key=api_key,
                system_prompt=APPWORLD_SYSTEM_PROMPT,
                max_tokens=4096,
                temperature=0.6,
                tool_executor=None,  # No tools
                max_tool_iterations=3,
                provider=provider
            )
            
            vanilla_results = evaluate_appworld_agent(
                vanilla_agent,
                test_task_ids,
                experiment_name=f"{session_id}_vanilla",
                max_interactions=30,
                current_tools=None,
                current_skills=None,
                current_knowledge=None
            )
        else:
            print("\n1️⃣ Skipping VANILLA agent evaluation (--no-eval-vanilla)")
        
        if eval_evolved:
            # Create evolved agent (with tools, skills, knowledge, patches)
            print("\n2️⃣ Evaluating EVOLVED agent (with tools + patches)...")
            
            # Build enhanced prompt with all state
            final_tools = format_tools_for_prompt(workspace.registry.list_tools())
            final_skills = load_skills_from_workspace(workspace.workspace_dir)
            final_knowledge = load_knowledge_from_workspace(workspace.workspace_dir)
            final_patches = updater.get_patches()
            
            final_prompt = build_prompt_with_state(
                tools=final_tools,
                skills=final_skills,
                knowledge=final_knowledge
            )
            
            # # Add patches
            # if final_patches:
            #     patch_list = "\n".join([
            #         f"- **{p.get('name', 'Patch')}**: {p.get('description', '')}"
            #         for p in final_patches
            #     ])
            #     final_prompt += f"\n\n## Behavioral Guidance Patches\n\nApply these learned patterns:\n\n{patch_list}"
            
            evolved_agent = create_tool_enabled_agent(
                model_name=main_model,
                api_key=api_key,
                system_prompt=final_prompt,
                max_tokens=4096,
                temperature=0.7,
                tool_executor=workspace.executor,
                max_tool_iterations=3,
                provider=provider
            )
            
            evolved_results = evaluate_appworld_agent(
                evolved_agent,
                test_task_ids,
                experiment_name=f"{session_id}_evolved",
                max_interactions=30,
                current_tools=final_tools,
                current_skills=final_skills,
                current_knowledge=final_knowledge
            )
        else:
            print("\n2️⃣ Skipping EVOLVED agent evaluation (--no-eval-evolved)")
            # Initialize final_* variables for results even if not evaluating
            final_tools = format_tools_for_prompt(workspace.registry.list_tools())
            final_skills = load_skills_from_workspace(workspace.workspace_dir)
            final_knowledge = load_knowledge_from_workspace(workspace.workspace_dir)
            final_patches = updater.get_patches()
        
        # Compare results (only if both evaluated)
        if vanilla_results and evolved_results:
            print("\n" + "=" * 80)
            print("FINAL COMPARISON: Vanilla vs Evolved")
            print("=" * 80)
            
            vanilla_score = vanilla_results['average_score']
            evolved_score = evolved_results['average_score']
            vanilla_completion = vanilla_results['completion_rate']
            evolved_completion = evolved_results['completion_rate']
            vanilla_tgc = vanilla_results['task_goal_completion']
            evolved_tgc = evolved_results['task_goal_completion']

            improvement = evolved_score - vanilla_score
            completion_improvement = evolved_completion - vanilla_completion
            tgc_improvement = evolved_tgc - vanilla_tgc
            
            print("\nOverall Metrics:")
            print("-" * 95)
            print(f"  {'Agent':<25} {'Avg Score':<15} {'Completion Rate':<20} {'TGC':<15}")
            print("-" * 95)
            print(f"  {'Vanilla (no tools)':<25} {vanilla_score:>6.2%}         {vanilla_completion:>6.2%}              {vanilla_tgc:>6.2%}")
            print(f"  {'Evolved (tools+patches)':<25} {evolved_score:>6.2%}         {evolved_completion:>6.2%}              {evolved_tgc:>6.2%}")
            print("-" * 95)
            print(f"  {'Improvement':<25} {improvement:>+6.2%}         {completion_improvement:>+6.2%}              {tgc_improvement:>+6.2%}")
            print()
            
            if improvement > 0:
                print(f"✅ Evolved agent improved by {improvement:.2%} in score")
            elif improvement < 0:
                print(f"⚠️ Evolved agent decreased by {abs(improvement):.2%} in score")
            else:
                print(f"➡️ No change in score")
            
            # Update results with comparison
            results = {
                'session_id': session_id,
                'model': main_model,
                'training_stats': final_stats,
                'evolution_stats': {
                    'tools_created': tools_created,
                    'tools_evolved': tools_evolved,
                    'patches_added': patches_added,
                    'total_tools': len(workspace.registry.list_tools()),
                    'total_patches': len(updater.get_patches())
                },
                'vanilla_results': {
                    'average_score': vanilla_score,
                    'completion_rate': vanilla_completion,
                    'task_goal_completion': vanilla_tgc,
                    'tasks_completed': vanilla_results['tasks_completed'],
                    'total_tasks': vanilla_results['total_tasks']
                },
                'evolved_results': {
                    'average_score': evolved_score,
                    'completion_rate': evolved_completion,
                    'task_goal_completion': evolved_tgc,
                    'tasks_completed': evolved_results['tasks_completed'],
                    'total_tasks': evolved_results['total_tasks']
                },
                'improvement': {
                    'score_delta': improvement,
                    'completion_delta': completion_improvement,
                    'tgc_delta': tgc_improvement
                },
                'timestamp': datetime.now().isoformat()
            }
        elif vanilla_results:
            # Only vanilla evaluated
            print("\n" + "=" * 80)
            print("VANILLA AGENT RESULTS")
            print("=" * 80)
            vanilla_score = vanilla_results['average_score']
            vanilla_completion = vanilla_results['completion_rate']
            vanilla_tgc = vanilla_results['task_goal_completion']
            print(f"  Average Score: {vanilla_score:.2%}")
            print(f"  Completion Rate: {vanilla_completion:.2%}")
            print(f"  TGC (Task Goal Completion): {vanilla_tgc:.2%}")
            
            results = {
                'session_id': session_id,
                'model': main_model,
                'training_stats': final_stats,
                'vanilla_results': {
                    'average_score': vanilla_score,
                    'completion_rate': vanilla_completion,
                    'task_goal_completion': vanilla_tgc,
                    'tasks_completed': vanilla_results['tasks_completed'],
                    'total_tasks': vanilla_results['total_tasks']
                },
                'timestamp': datetime.now().isoformat()
            }
        elif evolved_results:
            # Only evolved evaluated
            print("\n" + "=" * 80)
            print("EVOLVED AGENT RESULTS")
            print("=" * 80)
            evolved_score = evolved_results['average_score']
            evolved_completion = evolved_results['completion_rate']
            evolved_tgc = evolved_results['task_goal_completion']
            print(f"  Average Score: {evolved_score:.2%}")
            print(f"  Completion Rate: {evolved_completion:.2%}")
            print(f"  TGC (Task Goal Completion): {evolved_tgc:.2%}")
            
            results = {
                'session_id': session_id,
                'model': main_model,
                'training_stats': final_stats,
                'evolution_stats': {
                    'tools_created': tools_created,
                    'tools_evolved': tools_evolved,
                    'patches_added': patches_added,
                    'total_tools': len(workspace.registry.list_tools()),
                    'total_patches': len(updater.get_patches())
                },
                'evolved_results': {
                    'average_score': evolved_score,
                    'completion_rate': evolved_completion,
                    'task_goal_completion': evolved_tgc,
                    'tasks_completed': evolved_results['tasks_completed'],
                    'total_tasks': evolved_results['total_tasks']
                },
                'timestamp': datetime.now().isoformat()
            }
        else:
            print("⚠️ No evaluations performed")
            results = {
                'session_id': session_id,
                'model': main_model,
                'training_stats': final_stats,
                'timestamp': datetime.now().isoformat()
            }
    
    # Save results
    results_path = os.path.join(workspace.workspace_dir, 'results.json')
    with open(results_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"\n✅ Results saved to: {results_path}")
    print(f"📁 Workspace: {workspace.workspace_dir}")
    
    print("\n" + "=" * 80)
    print("Complete!")
    print("=" * 80)
    print(f"\nTo reuse this session:")
    print(f"  --session-id {session_id}")
    
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="AppWorld Meta-Evolver (Multi-Provider: GPT, Gemini, Claude)")
    parser.add_argument('--api-key', type=str, help='API key (or set OPENAI_API_KEY, GOOGLE_API_KEY, ANTHROPIC_API_KEY)')
    parser.add_argument('--main-model', type=str, default='gpt-5', 
                        help='Model name (auto-detects provider: gpt-* = OpenAI, gemini-* = Google, claude-* = Anthropic)')
    parser.add_argument('--proposer-model', type=str, default=None, help='Proposer model (defaults to main model)')

    parser.add_argument('--num-tasks', type=int, default=10, help='Number of tasks')
    parser.add_argument('--num-test-tasks', type=int, default=10, help='Number of test tasks')
    parser.add_argument('--test-dataset', type=str, default='test_normal', 
                        choices=['test_normal', 'test_challenge'],
                        help='Test dataset to use (default: test_normal). test_challenge loads from end to avoid overlap with training.')
    parser.add_argument('--batch-size', type=int, default=5, help='Batch size')
    parser.add_argument('--session-id', type=str, help='Session ID')
    parser.add_argument('--test-only', action='store_true', help='Skip training')
    parser.add_argument('--resume-from', type=int, default=0, help='Resume from index')
    parser.add_argument('--observation-window', type=int, default=10, help='Observation window')
    parser.add_argument('--on-policy', action='store_true', default=True, help='On-policy learning')
    parser.add_argument('--use-tool-enabled-proposer', action='store_true', default=True)
    parser.add_argument('--use-persistent-observer', action='store_true', default=False)
    parser.add_argument('--quiet', action='store_true', help='Reduce output')
    parser.add_argument('--eval-vanilla', action='store_true', default=True, help='Evaluate vanilla agent')
    parser.add_argument('--no-eval-vanilla', dest='eval_vanilla', action='store_false', help='Skip vanilla agent evaluation')
    parser.add_argument('--eval-evolved', action='store_true', default=True, help='Evaluate evolved agent')
    parser.add_argument('--no-eval-evolved', dest='eval_evolved', action='store_false', help='Skip evolved agent evaluation')
    
    args = parser.parse_args()
    main(
        main_model=args.main_model,
        proposer_model=args.proposer_model,
        api_key=args.api_key,
        num_tasks=args.num_tasks,
        num_test_tasks=args.num_test_tasks,
        test_dataset=args.test_dataset,
        batch_size=args.batch_size,
        session_id=args.session_id,
        test_only=args.test_only,
        resume_from=args.resume_from,
        observation_window=args.observation_window,
        on_policy=args.on_policy,
        use_tool_enabled_proposer=args.use_tool_enabled_proposer,
        use_persistent_observer=args.use_persistent_observer,
        verbose=not args.quiet,
        eval_vanilla=args.eval_vanilla,
        eval_evolved=args.eval_evolved
    )
