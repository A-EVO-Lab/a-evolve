"""Bedrock agent with tool execution support."""

from typing import Optional
from .bedrock_agent import BedrockAgent
from .tool_enabled_agent import ToolEnabledAgent


class ToolEnabledBedrockAgent(BedrockAgent, ToolEnabledAgent):
    """
    Bedrock agent that can parse and execute tool calls.
    
    Combines BedrockAgent (Bedrock API calls) with ToolEnabledAgent (tool execution).
    """
    
    def __init__(
        self,
        model_name: str,
        system_prompt: str = "",
        aws_region: str = "us-west-2",
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        tool_executor=None,
        max_tool_iterations: int = 3,
        **kwargs
    ):
        """
        Initialize tool-enabled Bedrock agent.
        
        Args:
            model_name: Bedrock model ID
            system_prompt: System prompt (should include tool descriptions)
            aws_region: AWS region
            aws_access_key_id: AWS access key (optional)
            aws_secret_access_key: AWS secret key (optional)
            max_tokens: Maximum tokens in response
            temperature: Temperature for generation
            tool_executor: ToolExecutor instance for executing tools
            max_tool_iterations: Maximum number of tool call iterations
        """
        # Initialize BedrockAgent
        BedrockAgent.__init__(
            self,
            model_name=model_name,
            api_key=None,  # Not used for Bedrock
            system_prompt=system_prompt,
            aws_region=aws_region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs
        )
        
        # Initialize tool support
        self.tool_executor = tool_executor
        self.max_tool_iterations = max_tool_iterations
    
    def _call_without_tools(self, input_text: str) -> str:
        """
        Internal method to call Bedrock LLM without tool execution.
        This is used by execute_with_tools to avoid infinite recursion.
        """
        return BedrockAgent.call(self, input_text)
    
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
            return BedrockAgent.call(self, input_text)

