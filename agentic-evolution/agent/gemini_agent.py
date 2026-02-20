"""Gemini Agent implementation using Google AI GenAI SDK (new SDK)."""

import json
from typing import Optional, Dict, Any, List
from .agent import Agent

try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


class GeminiAgent(Agent):
    """Agent implementation using Google Gemini API.
    
    Uses the new google.genai SDK (recommended by Google).
    """
    
    def __init__(
        self,
        model_name: str = "gemini-2.0-flash",
        api_key: str = None,
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs
    ):
        """Initialize Gemini Agent.
        
        Args:
            model_name: Gemini model name (e.g., "gemini-2.0-flash", 
                       "gemini-3-flash-preview", "gemini-1.5-pro")
            api_key: Google AI API key (required)
            system_prompt: System prompt for the agent
            max_tokens: Maximum tokens in response
            temperature: Temperature for generation
            **kwargs: Additional parameters passed to parent Agent
        """
        super().__init__(model_name, api_key, system_prompt)
        self.max_tokens = max_tokens
        self.temperature = temperature
        
        if not GEMINI_AVAILABLE:
            raise ImportError(
                "google-genai is required for GeminiAgent. "
                "Install it with: pip install google-genai"
            )
        
        if not api_key:
            raise ValueError(
                "api_key is required for GeminiAgent. "
                "Provide your Google AI API key."
            )
        
        # Initialize the client with API key
        self.client = genai.Client(api_key=api_key)
    
    def call(self, input_text: Any) -> str:
        """Call the Gemini model and return the response."""
        try:
            # Build contents for the model
            if isinstance(input_text, list):
                # Convert conversation list to Gemini format
                contents = []
                for msg in input_text:
                    role = msg.get("role") if isinstance(msg, dict) else None
                    content = msg.get("content", "") if isinstance(msg, dict) else msg
                    
                    # Handle content that could be string or list
                    if isinstance(content, list):
                        text_parts = []
                        for block in content:
                            if isinstance(block, dict) and "text" in block:
                                text_parts.append(block["text"])
                            elif isinstance(block, str):
                                text_parts.append(block)
                        content_str = "\n".join(text_parts)
                    else:
                        content_str = str(content)
                    
                    # Skip system role (handled separately)
                    if role == "system":
                        continue
                    
                    # Gemini uses "user" and "model" roles
                    gemini_role = "model" if role == "assistant" else "user"
                    
                    contents.append(
                        types.Content(
                            role=gemini_role,
                            parts=[types.Part.from_text(text=content_str)]
                        )
                    )
            else:
                # Simple single input
                contents = str(input_text)
            
            # Build config
            config = types.GenerateContentConfig(
                system_instruction=self.system_prompt if self.system_prompt else None,
                temperature=self.temperature,
                max_output_tokens=self.max_tokens,
            )
            
            # Call the API
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config
            )
            
            # Parse response
            if response.text:
                return response.text
            
            return ""
            
        except Exception as e:
            return f"Error calling Gemini model: {str(e)}"
    
    def call_with_native_tools(
        self,
        messages: list,
        tools: list = None,
        system: str = None
    ):
        """
        Call Gemini with native function calling support.
        
        Args:
            messages: List of message dicts with role and content
            tools: List of tool definitions (converted from Claude format)
            system: Optional system prompt override
            
        Returns:
            Response object with Claude-compatible structure
        """
        try:
            # Build contents for the model
            contents = []
            
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                
                # Handle content that could be list (tool results) or string
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "tool_result":
                                # Function response
                                parts.append(types.Part.from_function_response(
                                    name=item.get("tool_use_id", "function").split("_")[-1] if "_" in item.get("tool_use_id", "") else "function",
                                    response={"result": item.get("content", "")}
                                ))
                            elif item.get("type") == "text":
                                parts.append(types.Part.from_text(text=item.get("text", "")))
                        elif isinstance(item, str):
                            parts.append(types.Part.from_text(text=item))
                    
                    if parts:
                        gemini_role = "model" if role == "assistant" else "user"
                        contents.append(types.Content(role=gemini_role, parts=parts))
                else:
                    gemini_role = "model" if role == "assistant" else "user"
                    contents.append(
                        types.Content(
                            role=gemini_role,
                            parts=[types.Part.from_text(text=str(content))]
                        )
                    )
            
            # Convert Claude-format tools to Gemini function declarations
            gemini_tools = None
            if tools:
                function_declarations = []
                for tool in tools:
                    input_schema = tool.get("input_schema", {"type": "object", "properties": {}})
                    
                    function_declarations.append(types.FunctionDeclaration(
                        name=tool.get("name", ""),
                        description=tool.get("description", ""),
                        parameters=input_schema
                    ))
                
                gemini_tools = [types.Tool(function_declarations=function_declarations)]
            
            # Build config
            system_prompt = system or self.system_prompt
            config = types.GenerateContentConfig(
                system_instruction=system_prompt if system_prompt else None,
                temperature=self.temperature,
                max_output_tokens=self.max_tokens,
                tools=gemini_tools
            )
            
            # Call the API
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config
            )
            
            # Convert to Claude-compatible response format
            return GeminiResponseAdapter(response)
            
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


class GeminiResponseAdapter:
    """Adapts Gemini response to Claude-compatible format."""
    
    def __init__(self, gemini_response):
        self.gemini_response = gemini_response
        self.content = []
        self.stop_reason = "end_turn"
        
        try:
            # Check for function calls in candidates
            if hasattr(gemini_response, 'candidates') and gemini_response.candidates:
                candidate = gemini_response.candidates[0]
                
                if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                    for i, part in enumerate(candidate.content.parts):
                        if hasattr(part, 'function_call') and part.function_call:
                            self.stop_reason = "tool_use"
                            fc = part.function_call
                            
                            # Extract args
                            args = {}
                            if hasattr(fc, 'args') and fc.args:
                                args = dict(fc.args)
                            
                            self.content.append(ToolUseBlock(
                                id=f"toolu_{i}_{fc.name}",
                                name=fc.name,
                                input_dict=args
                            ))
                        elif hasattr(part, 'text') and part.text:
                            self.content.append(TextBlock(part.text))
            
            # Fallback to text
            if not self.content and hasattr(gemini_response, 'text') and gemini_response.text:
                self.content.append(TextBlock(gemini_response.text))
                
        except Exception as e:
            self.content = [TextBlock(f"Error parsing response: {str(e)}")]
            self.stop_reason = "error"
