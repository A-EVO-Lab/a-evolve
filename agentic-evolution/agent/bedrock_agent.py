"""Bedrock-based Agent implementation using boto3."""

import json
from typing import Optional, Dict, Any
from .agent import Agent

try:
    import boto3
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


class BedrockAgent(Agent):
    """Agent implementation using AWS Bedrock models.
    
    Supports Claude and other Bedrock models via boto3.
    """
    
    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        system_prompt: str = "",
        aws_region: str = "us-west-2",
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs
    ):
        """Initialize Bedrock Agent.
        
        Args:
            model_name: Bedrock model ID (e.g., "us.anthropic.claude-sonnet-4-20250514-v1:0",
                       "anthropic.claude-3-sonnet-20240229-v1:0", "amazon.titan-text-express-v1")
            api_key: Not used for Bedrock (uses AWS credentials)
            system_prompt: System prompt for the agent
            aws_region: AWS region for Bedrock (default: us-west-2)
            aws_access_key_id: AWS access key (optional, uses IAM role if not provided)
            aws_secret_access_key: AWS secret key (optional, uses IAM role if not provided)
            max_tokens: Maximum tokens in response
            temperature: Temperature for generation
            **kwargs: Additional parameters passed to parent Agent
        """
        super().__init__(model_name, api_key, system_prompt)
        self.aws_region = aws_region
        self.max_tokens = max_tokens
        self.temperature = temperature
        
        if not BOTO3_AVAILABLE:
            raise ImportError(
                "boto3 is required for BedrockAgent. "
                "Install it with: pip install boto3"
            )
        
        # Initialize boto3 bedrock runtime client
        if aws_access_key_id and aws_secret_access_key:
            self.bedrock_runtime = boto3.client(
                'bedrock-runtime',
                region_name=aws_region,
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key
            )
        else:
            # Use IAM role or default credentials
            self.bedrock_runtime = boto3.client(
                'bedrock-runtime',
                region_name=aws_region
            )
        
        # Determine model provider based on model_name
        self.model_provider = self._detect_model_provider(model_name)
    
    def _detect_model_provider(self, model_name: str) -> str:
        """Detect the model provider from model name."""
        if "anthropic" in model_name.lower() or "claude" in model_name.lower():
            return "anthropic"
        elif "nova" in model_name.lower():
            return "amazon_nova"
        elif "amazon" in model_name.lower() or "titan" in model_name.lower():
            return "amazon"
        elif "meta" in model_name.lower() or "llama" in model_name.lower():
            return "meta"
        elif "mistral" in model_name.lower():
            return "mistral"
        elif "cohere" in model_name.lower():
            return "cohere"
        elif "qwen" in model_name.lower():
            return "qwen"
        elif "ai21" in model_name.lower() or "jamba" in model_name.lower():
            return "ai21"
        elif "openai" in model_name.lower() or "gpt" in model_name.lower():
            return "openai"
        else:
            # Default to anthropic format
            return "anthropic"
    
    def _format_anthropic_request(self, input_text: str) -> Dict[str, Any]:
        """Format request for Anthropic Claude models."""
        # Check if this is Claude 3+ (new message format) or older Claude
        model_lower = self.model_name.lower()
        is_claude3_plus = (
            "claude-3" in model_lower or 
            "claude-sonnet-4" in model_lower or
            "claude-opus-4" in model_lower or
            "claude-haiku-3" in model_lower or
            # Support any Claude 4.x or Claude 5.x versions
            "claude-4" in model_lower or
            "claude-5" in model_lower or
            # Support version patterns like sonnet-4-5, opus-4-2, etc.
            any(f"sonnet-{v}" in model_lower for v in ["4", "5", "6"]) or
            any(f"opus-{v}" in model_lower for v in ["4", "5", "6"])
        )
        
        if is_claude3_plus:
            # Claude 3+ format with separate system message
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": input_text}]
                    }
                ]
            }
            if self.system_prompt:
                body["system"] = [{"type": "text", "text": self.system_prompt}]
        else:
            # Older Claude format (Claude 2 and earlier)
            prompt = input_text
            if self.system_prompt:
                prompt = f"{self.system_prompt}\n\nHuman: {input_text}\n\nAssistant:"
            
            body = {
                "prompt": prompt,
                "max_tokens_to_sample": self.max_tokens,
                "temperature": self.temperature,
            }
        
        return body
    
    def _format_amazon_request(self, input_text: str) -> Dict[str, Any]:
        """Format request for Amazon Titan models."""
        prompt = input_text
        if self.system_prompt:
            prompt = f"{self.system_prompt}\n\n{prompt}"
        
        body = {
            "inputText": prompt,
            "textGenerationConfig": {
                "maxTokenCount": self.max_tokens,
                "temperature": self.temperature,
            }
        }
        return body
    
    def _format_meta_request(self, input_text: str) -> Dict[str, Any]:
        """Format request for Meta Llama models."""
        prompt = input_text
        if self.system_prompt:
            prompt = f"{self.system_prompt}\n\n{prompt}"
        
        body = {
            "prompt": prompt,
            "max_gen_len": self.max_tokens,
            "temperature": self.temperature,
        }
        return body
    
    def _format_mistral_request(self, input_text: str) -> Dict[str, Any]:
        """Format request for Mistral models."""
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": input_text})
        
        body = {
            "prompt": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        return body
    
    def _format_cohere_request(self, input_text: str) -> Dict[str, Any]:
        """Format request for Cohere models."""
        prompt = input_text
        if self.system_prompt:
            prompt = f"{self.system_prompt}\n\n{prompt}"
        
        body = {
            "prompt": prompt,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        return body
    
    def _parse_anthropic_response(self, response: Dict[str, Any]) -> str:
        """Parse response from Anthropic Claude models."""
        # Check if this is Claude 3+ format
        model_lower = self.model_name.lower()
        is_claude3_plus = (
            "claude-3" in model_lower or 
            "claude-sonnet-4" in model_lower or
            "claude-opus-4" in model_lower or
            "claude-haiku-3" in model_lower or
            # Support any Claude 4.x or Claude 5.x versions
            "claude-4" in model_lower or
            "claude-5" in model_lower or
            # Support version patterns like sonnet-4-5, opus-4-2, etc.
            any(f"sonnet-{v}" in model_lower for v in ["4", "5", "6"]) or
            any(f"opus-{v}" in model_lower for v in ["4", "5", "6"])
        )
        
        if is_claude3_plus:
            # Claude 3+ format: response has "content" array
            content_blocks = response.get("content", [])
            if isinstance(content_blocks, list) and len(content_blocks) > 0:
                return content_blocks[0].get("text", "")
        else:
            # Older Claude format: response has "completion"
            return response.get("completion", "")
        return ""
    
    def _parse_amazon_response(self, response: Dict[str, Any]) -> str:
        """Parse response from Amazon Titan models."""
        results = response.get("results", [])
        if results and len(results) > 0:
            return results[0].get("outputText", "")
        return ""
    
    def _parse_meta_response(self, response: Dict[str, Any]) -> str:
        """Parse response from Meta Llama models."""
        return response.get("generation", "")
    
    def _parse_mistral_response(self, response: Dict[str, Any]) -> str:
        """Parse response from Mistral models."""
        outputs = response.get("outputs", [])
        if outputs and len(outputs) > 0:
            return outputs[0].get("text", "")
        return ""
    
    def _parse_cohere_response(self, response: Dict[str, Any]) -> str:
        """Parse response from Cohere models."""
        generations = response.get("generations", [])
        if generations and len(generations) > 0:
            return generations[0].get("text", "")
        return ""
    
    def _format_qwen_request(self, input_text: str) -> Dict[str, Any]:
        """Format request for Qwen models."""
        messages = [{"role": "user", "content": input_text}]
        
        if self.system_prompt:
            messages.insert(0, {"role": "system", "content": self.system_prompt})
        
        return {
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": 0.9
        }
    
    def _parse_qwen_response(self, response: Dict[str, Any]) -> str:
        """Parse response from Qwen models."""
        # Qwen uses OpenAI-like format
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            return message.get("content", "")
        # Alternative format
        output = response.get("output", {})
        if output:
            return output.get("text", "")
        return ""
    
    def _format_nova_request(self, input_text: str) -> Dict[str, Any]:
        """Format request for Amazon Nova models.
        
        Nova uses a simpler message format similar to Claude 3+.
        """
        # Nova uses simpler content format - just text strings in an array
        messages = [
            {
                "role": "user",
                "content": [{"text": input_text}]
            }
        ]
        
        # System prompt goes as a separate field in Nova
        body = {
            "messages": messages,
            "inferenceConfig": {
                "max_new_tokens": self.max_tokens,
                "temperature": self.temperature,
                "top_p": 0.9
            }
        }
        
        if self.system_prompt:
            # Nova uses 'system' field at root level, with text array format
            body["system"] = [{"text": self.system_prompt}]
        
        return body
    
    def _parse_nova_response(self, response: Dict[str, Any]) -> str:
        """Parse response from Amazon Nova models."""
        # Try output.message.content format (Converse API)
        output = response.get("output", {})
        message = output.get("message", {})
        content = message.get("content", [])
        if content and len(content) > 0:
            return content[0].get("text", "")
        
        # Alternative: direct content in response
        if "content" in response:
            content = response["content"]
            if isinstance(content, list) and len(content) > 0:
                return content[0].get("text", "")
        
        # Alternative: direct text field
        if "text" in response:
            return response["text"]
            
        return ""
    
    def _format_ai21_request(self, input_text: str) -> Dict[str, Any]:
        """Format request for AI21 models."""
        messages = [{"role": "user", "content": input_text}]
        
        if self.system_prompt:
            messages.insert(0, {"role": "system", "content": self.system_prompt})
        
        return {
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": 0.9
        }
    
    def _parse_ai21_response(self, response: Dict[str, Any]) -> str:
        """Parse response from AI21 models."""
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            return message.get("content", "")
        return ""
    
    def _format_openai_request(self, input_text: str) -> Dict[str, Any]:
        """Format request for OpenAI GPT models on Bedrock."""
        messages = [{"role": "user", "content": input_text}]
        
        if self.system_prompt:
            messages.insert(0, {"role": "system", "content": self.system_prompt})
        
        return {
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": 0.9
        }
    
    def _parse_openai_response(self, response: Dict[str, Any]) -> str:
        """Parse response from OpenAI GPT models."""
        # Standard OpenAI format
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")
            if content:
                return content
            # Alternative: text field
            return choices[0].get("text", "")
        # Alternative response format
        return response.get("output", {}).get("text", "")
    
    def call(self, input_text: str) -> str:
        """Call the Bedrock model and return the response."""
        try:
            # Nova models use Converse API, others use InvokeModel API
            if self.model_provider == "amazon_nova":
                return self._call_with_converse(input_text)
            
            # Format request based on model provider
            if self.model_provider == "anthropic":
                body = self._format_anthropic_request(input_text)
            elif self.model_provider == "amazon":
                body = self._format_amazon_request(input_text)
            elif self.model_provider == "meta":
                body = self._format_meta_request(input_text)
            elif self.model_provider == "mistral":
                body = self._format_mistral_request(input_text)
            elif self.model_provider == "cohere":
                body = self._format_cohere_request(input_text)
            elif self.model_provider == "qwen":
                body = self._format_qwen_request(input_text)
            elif self.model_provider == "ai21":
                body = self._format_ai21_request(input_text)
            elif self.model_provider == "openai":
                body = self._format_openai_request(input_text)
            else:
                # Default to anthropic
                body = self._format_anthropic_request(input_text)
            
            # Invoke Bedrock model
            response = self.bedrock_runtime.invoke_model(
                modelId=self.model_name,
                body=json.dumps(body)
            )
            
            # Parse response
            response_body = json.loads(response['body'].read())
            
            if self.model_provider == "anthropic":
                return self._parse_anthropic_response(response_body)
            elif self.model_provider == "amazon":
                return self._parse_amazon_response(response_body)
            elif self.model_provider == "meta":
                return self._parse_meta_response(response_body)
            elif self.model_provider == "mistral":
                return self._parse_mistral_response(response_body)
            elif self.model_provider == "cohere":
                return self._parse_cohere_response(response_body)
            elif self.model_provider == "qwen":
                return self._parse_qwen_response(response_body)
            elif self.model_provider == "ai21":
                return self._parse_ai21_response(response_body)
            elif self.model_provider == "openai":
                return self._parse_openai_response(response_body)
            else:
                return self._parse_anthropic_response(response_body)
                
        except Exception as e:
            return f"Error calling Bedrock model: {str(e)}"
    
    def _call_with_converse(self, input_text: str) -> str:
        """Call Nova models using Converse API.
        
        Nova requires the Converse API instead of InvokeModel.
        """
        try:
            # Build messages
            messages = [
                {
                    "role": "user",
                    "content": [{"text": input_text}]
                }
            ]
            
            # Build request
            request = {
                "modelId": self.model_name,
                "messages": messages,
                "inferenceConfig": {
                    "maxTokens": self.max_tokens,
                    "temperature": self.temperature,
                    "topP": 0.9
                }
            }
            
            # Add system prompt if exists
            if self.system_prompt:
                request["system"] = [{"text": self.system_prompt}]
            
            # Call Converse API
            response = self.bedrock_runtime.converse(**request)
            
            # Parse response
            output = response.get("output", {})
            message = output.get("message", {})
            content = message.get("content", [])
            
            if content and len(content) > 0:
                return content[0].get("text", "")
            
            return ""
            
        except Exception as e:
            return f"Error calling Nova with Converse API: {str(e)}"

