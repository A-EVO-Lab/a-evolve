"""Claude Agent implementation using Anthropic SDK directly."""

import json
from typing import Optional, Dict, Any, List
from .agent import Agent

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


class ClaudeAgent(Agent):
    """Agent implementation using Anthropic Claude API directly.
    
    Uses the official anthropic Python SDK instead of AWS Bedrock.
    """
    
    def __init__(
        self,
        model_name: str = "claude-sonnet-4-20250514",
        api_key: str = None,
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs
    ):
        """Initialize Claude Agent.
        
        Args:
            model_name: Claude model name (e.g., "claude-sonnet-4-20250514",
                       "claude-3-5-sonnet-20241022", "claude-3-opus-20240229")
            api_key: Anthropic API key (required)
            system_prompt: System prompt for the agent
            max_tokens: Maximum tokens in response
            temperature: Temperature for generation
            **kwargs: Additional parameters passed to parent Agent
        """
        super().__init__(model_name, api_key, system_prompt)
        self.max_tokens = max_tokens
        self.temperature = temperature
        
        if not ANTHROPIC_AVAILABLE:
            raise ImportError(
                "anthropic is required for ClaudeAgent. "
                "Install it with: pip install anthropic"
            )
        
        if not api_key:
            raise ValueError(
                "api_key is required for ClaudeAgent. "
                "Provide your Anthropic API key."
            )
        
        # Initialize Anthropic client
        self.client = anthropic.Anthropic(api_key=api_key)
    
    def call(self, input_text: Any) -> str:
        """Call the Claude model and return the response."""
        try:
            # Build request kwargs
            request_kwargs = {
                "model": self.model_name,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
            }
            
            messages = []
            system_parts: List[str] = []
            
            if isinstance(input_text, list):
                # Treat input_text as a conversation list; normalize to Claude blocks
                for msg in input_text:
                    role = msg.get("role") if isinstance(msg, dict) else None
                    content = msg.get("content", "") if isinstance(msg, dict) else msg
                    
                    # Collect inline system prompts separately
                    if role == "system":
                        system_parts.append(str(content))
                        continue
                    
                    blocks = []
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") and "text" in block:
                                blocks.append(block)
                            else:
                                blocks.append({"type": "text", "text": str(block)})
                    else:
                        blocks.append({"type": "text", "text": str(content)})
                    
                    role_normalized = role if role in ("user", "assistant") else "user"
                    messages.append({"role": role_normalized, "content": blocks})
                
                if not messages:
                    messages = [{"role": "user", "content": [{"type": "text", "text": ""}]}]
            else:
                # Simple single turn input
                messages = [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": str(input_text)}]
                    }
                ]
            
            # Add messages and system prompt (including any inline system entries)
            request_kwargs["messages"] = messages
            
            system_prompt_parts = []
            if self.system_prompt:
                system_prompt_parts.append(self.system_prompt)
            if system_parts:
                system_prompt_parts.append("\n\n".join(system_parts))
            if system_prompt_parts:
                request_kwargs["system"] = "\n\n".join(system_prompt_parts)
            
            # Call Claude API
            response = self.client.messages.create(**request_kwargs)
            
            # Parse response
            if response.content and len(response.content) > 0:
                # Get text from the first content block
                content_block = response.content[0]
                if hasattr(content_block, 'text'):
                    return content_block.text
                elif isinstance(content_block, dict) and 'text' in content_block:
                    return content_block['text']
            
            return ""
            
        except anthropic.APIConnectionError as e:
            return f"Error: Failed to connect to Anthropic API: {str(e)}"
        except anthropic.RateLimitError as e:
            return f"Error: Rate limit exceeded: {str(e)}"
        except anthropic.APIStatusError as e:
            return f"Error calling Claude API: {e.status_code} - {str(e)}"
        except Exception as e:
            return f"Error calling Claude model: {str(e)}"
    
    def call_with_native_tools(
        self,
        messages: list,
        tools: list = None,
        system: str = None
    ):
        """
        Call Claude with native tool support (learn-claude-code pattern).
        
        This enables on-demand skill loading via tool_result for cache-preserving
        injection. Skills are loaded when the model calls the Skill tool.
        
        Args:
            messages: List of message dicts with role and content
            tools: List of tool definitions (JSON schema format)
            system: Optional system prompt override
            
        Returns:
            Full Claude API response object
        """
        try:
            request_kwargs = {
                "model": self.model_name,
                "max_tokens": self.max_tokens,
                "messages": messages,
            }
            
            # Add system prompt
            if system:
                request_kwargs["system"] = system
            elif self.system_prompt:
                request_kwargs["system"] = self.system_prompt
            
            # Add tools if provided
            if tools:
                request_kwargs["tools"] = tools
            
            # Call Claude API
            response = self.client.messages.create(**request_kwargs)
            return response
            
        except Exception as e:
            # Return a mock response with error
            class ErrorResponse:
                def __init__(self, error):
                    self.content = [{"type": "text", "text": f"Error: {error}"}]
                    self.stop_reason = "error"
            return ErrorResponse(str(e))
