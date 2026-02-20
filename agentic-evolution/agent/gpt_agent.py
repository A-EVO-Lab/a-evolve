"""GPT Agent implementation using OpenAI SDK."""

import json
from typing import Optional, Dict, Any, List
from .agent import Agent

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class GPTAgent(Agent):
    """Agent implementation using OpenAI GPT API.
    
    Uses the official openai Python SDK for GPT models.
    """
    
    def __init__(
        self,
        model_name: str = "gpt-5",
        api_key: str = None,
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs
    ):
        """Initialize GPT Agent.
        
        Args:
            model_name: OpenAI model name (e.g., "gpt-5", "gpt-5-mini", "gpt-4o")
            api_key: OpenAI API key (required)
            system_prompt: System prompt for the agent
            max_tokens: Maximum tokens in response
            temperature: Temperature for generation
            **kwargs: Additional parameters passed to parent Agent
        """
        super().__init__(model_name, api_key, system_prompt)
        self.max_tokens = max_tokens
        self.temperature = temperature
        
        if not OPENAI_AVAILABLE:
            raise ImportError(
                "openai is required for GPTAgent. "
                "Install it with: pip install openai"
            )
        
        if not api_key:
            raise ValueError(
                "api_key is required for GPTAgent. "
                "Provide your OpenAI API key."
            )
        
        # Initialize OpenAI client
        self.client = openai.OpenAI(api_key=api_key)
        
        # Determine if this model uses max_completion_tokens (newer API) or max_tokens (legacy)
        # GPT-5, o1, o3 models use the newer parameter and have restricted temperature (only 1 allowed)
        model_lower = model_name.lower()
        self.use_new_api = (
            model_lower.startswith("gpt-5") or
            model_lower.startswith("o1") or
            model_lower.startswith("o3") or
            "preview" in model_lower
        )

    
    def call(self, input_text: Any) -> str:
        """Call the GPT model and return the response."""
        try:
            messages = []
            
            # Add system prompt
            if self.system_prompt:
                messages.append({
                    "role": "system",
                    "content": self.system_prompt
                })
            
            if isinstance(input_text, list):
                # Treat input_text as a conversation list
                for msg in input_text:
                    role = msg.get("role") if isinstance(msg, dict) else None
                    content = msg.get("content", "") if isinstance(msg, dict) else msg
                    
                    # Skip system prompts in input (already handled above)
                    if role == "system":
                        continue
                    
                    # Handle content that could be string or list
                    if isinstance(content, list):
                        # OpenAI expects string content for most cases
                        # Extract text from content blocks
                        text_parts = []
                        for block in content:
                            if isinstance(block, dict) and "text" in block:
                                text_parts.append(block["text"])
                            elif isinstance(block, str):
                                text_parts.append(block)
                        content_str = "\n".join(text_parts)
                    else:
                        content_str = str(content)
                    
                    role_normalized = role if role in ("user", "assistant", "system") else "user"
                    messages.append({"role": role_normalized, "content": content_str})
                
                if len(messages) == (1 if self.system_prompt else 0):
                    messages.append({"role": "user", "content": ""})
            else:
                # Simple single turn input
                messages.append({"role": "user", "content": str(input_text)})
            
            # Call OpenAI API - use correct parameters based on model
            request_kwargs = {
                "model": self.model_name,
                "messages": messages
            }
            
            # Newer models (GPT-5, o1, o3) use max_completion_tokens and don't support custom temperature
            if self.use_new_api:
                request_kwargs["max_completion_tokens"] = self.max_tokens
                # Don't set temperature for newer models - they only support default (1)
            else:
                request_kwargs["max_tokens"] = self.max_tokens
                request_kwargs["temperature"] = self.temperature
            
            response = self.client.chat.completions.create(**request_kwargs)
            
            # Parse response
            if response.choices and len(response.choices) > 0:
                return response.choices[0].message.content or ""
            
            return ""
            
        except openai.APIConnectionError as e:
            return f"Error: Failed to connect to OpenAI API: {str(e)}"
        except openai.RateLimitError as e:
            return f"Error: Rate limit exceeded: {str(e)}"
        except openai.APIStatusError as e:
            return f"Error calling OpenAI API: {e.status_code} - {str(e)}"
        except Exception as e:
            return f"Error calling GPT model: {str(e)}"
    
    def call_with_native_tools(
        self,
        messages: list,
        tools: list = None,
        system: str = None
    ):
        """
        Call GPT with native function calling support.
        
        Args:
            messages: List of message dicts with role and content
            tools: List of tool definitions (converted from Claude format)
            system: Optional system prompt override
            
        Returns:
            Response object with OpenAI-compatible structure
        """
        try:
            # Build messages list with system prompt
            api_messages = []
            
            system_prompt = system or self.system_prompt
            if system_prompt:
                api_messages.append({
                    "role": "system",
                    "content": system_prompt
                })
            
            # Convert messages to OpenAI format
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                
                if role == "system":
                    continue  # Already handled
                
                # Handle content that could be list (tool results) or string
                if isinstance(content, list):
                    # Check for tool results
                    tool_results = []
                    text_parts = []
                    
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "tool_result":
                                tool_results.append({
                                    "role": "tool",
                                    "tool_call_id": item.get("tool_use_id", ""),
                                    "content": item.get("content", "")
                                })
                            elif item.get("type") == "text":
                                text_parts.append(item.get("text", ""))
                            elif hasattr(item, "type") and item.type == "tool_use":
                                # This is an assistant message with tool calls
                                pass
                        elif isinstance(item, str):
                            text_parts.append(item)
                    
                    if tool_results:
                        api_messages.extend(tool_results)
                    elif text_parts:
                        api_messages.append({"role": role, "content": "\n".join(text_parts)})
                else:
                    api_messages.append({"role": role, "content": str(content)})
            
            # Convert Claude-format tools to OpenAI function calling format
            openai_tools = None
            if tools:
                openai_tools = []
                for tool in tools:
                    openai_tools.append({
                        "type": "function",
                        "function": {
                            "name": tool.get("name", ""),
                            "description": tool.get("description", ""),
                            "parameters": tool.get("input_schema", {"type": "object", "properties": {}})
                        }
                    })
            
            # Build request - use correct parameters based on model
            request_kwargs = {
                "model": self.model_name,
                "messages": api_messages
            }
            
            # Newer models (GPT-5, o1, o3) use max_completion_tokens and don't support custom temperature
            if self.use_new_api:
                request_kwargs["max_completion_tokens"] = self.max_tokens
                # Don't set temperature for newer models - they only support default (1)
            else:
                request_kwargs["max_tokens"] = self.max_tokens
                request_kwargs["temperature"] = self.temperature
            
            if openai_tools:
                request_kwargs["tools"] = openai_tools
            
            # Call OpenAI API
            response = self.client.chat.completions.create(**request_kwargs)
            
            # Convert to Claude-compatible response format
            return GPTResponseAdapter(response)
            
        except Exception as e:
            # Return a mock response with error
            class ErrorResponse:
                def __init__(self, error):
                    self.content = [TextBlock(f"Error: {error}")]
                    self.stop_reason = "error"
            return ErrorResponse(str(e))


class TextBlock:
    """Simple text block for compatibility."""
    def __init__(self, text):
        self.type = "text"
        self.text = text


class ToolUseBlock:
    """Tool use block for compatibility."""
    def __init__(self, id, name, input_dict):
        self.type = "tool_use"
        self.id = id
        self.name = name
        self.input = input_dict


class GPTResponseAdapter:
    """Adapts OpenAI response to Claude-compatible format."""
    
    def __init__(self, openai_response):
        self.openai_response = openai_response
        self.content = []
        self.stop_reason = "end_turn"
        
        if openai_response.choices and len(openai_response.choices) > 0:
            choice = openai_response.choices[0]
            message = choice.message
            
            # Check for tool calls
            if message.tool_calls:
                self.stop_reason = "tool_use"
                for tc in message.tool_calls:
                    try:
                        input_dict = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except json.JSONDecodeError:
                        input_dict = {"raw": tc.function.arguments}
                    
                    self.content.append(ToolUseBlock(
                        id=tc.id,
                        name=tc.function.name,
                        input_dict=input_dict
                    ))
            
            # Add text content if present
            if message.content:
                self.content.append(TextBlock(message.content))
            
            # Set stop reason based on finish_reason
            if choice.finish_reason == "stop":
                self.stop_reason = "end_turn"
            elif choice.finish_reason == "tool_calls":
                self.stop_reason = "tool_use"
