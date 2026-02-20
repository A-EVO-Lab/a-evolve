"""Claude agent with tool execution support."""

from typing import Optional
from .claude_agent import ClaudeAgent
from .tool_enabled_agent import ToolEnabledAgent


class ToolEnabledClaudeAgent(ClaudeAgent, ToolEnabledAgent):
    """
    Claude agent that can parse and execute tool calls.
    
    Combines ClaudeAgent (direct Anthropic API) with ToolEnabledAgent (tool execution).
    """
    
    def __init__(
        self,
        model_name: str = "claude-sonnet-4-20250514",
        api_key: str = None,
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        tool_executor=None,
        max_tool_iterations: int = 3,
        **kwargs
    ):
        """
        Initialize tool-enabled Claude agent.
        
        Args:
            model_name: Claude model name
            api_key: Anthropic API key (required)
            system_prompt: System prompt (should include tool descriptions)
            max_tokens: Maximum tokens in response
            temperature: Temperature for generation
            tool_executor: ToolExecutor instance for executing tools
            max_tool_iterations: Maximum number of tool call iterations
        """
        # Initialize ClaudeAgent
        ClaudeAgent.__init__(
            self,
            model_name=model_name,
            api_key=api_key,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs
        )
        
        # Initialize tool support
        self.tool_executor = tool_executor
        self.max_tool_iterations = max_tool_iterations
    
    def _call_without_tools(self, input_text: str) -> str:
        """
        Internal method to call Claude LLM without tool execution.
        This is used by execute_with_tools to avoid infinite recursion.
        """
        return ClaudeAgent.call(self, input_text)
    
    def call(self, input_text: str, use_tools: bool = True) -> str:
        """
        Call the agent, optionally with tool support.
        
        Args:
            input_text: User input
            use_tools: Whether to enable tool execution (default: True)
            
        Returns:
            Agent output (may include tool execution results)
        """
        if use_tools and self.tool_executor is not None:
            # Use tool execution loop
            final_output, tool_log = self.execute_with_tools(input_text)
            
            # Store tool execution log in state for debugging
            if 'tool_execution_log' not in self.state:
                self.state['tool_execution_log'] = []
            self.state['tool_execution_log'].extend(tool_log)
            
            return final_output
        else:
            # Standard call without tools
            return ClaudeAgent.call(self, input_text)
