"""Tool-enabled Agent that can call and execute tools."""

import re
import json
from typing import Optional, Dict, Any, List, Tuple
from .agent import Agent


class ToolEnabledAgent(Agent):
    """
    Agent that can parse and execute tool calls.
    
    This agent extends the base Agent with:
    1. Tool call parsing from agent output
    2. Tool execution via ToolExecutor
    3. Multi-turn conversation with tool results
    
    The agent can make multiple tool calls in a single response,
    and the results are fed back for the agent to continue reasoning.
    """
    
    def __init__(
        self,
        model_name: str,
        api_key: str,
        system_prompt: str,
        tool_executor=None,
        max_tool_iterations: int = 3
    ):
        """
        Initialize tool-enabled agent.
        
        Args:
            model_name: Model name
            api_key: API key
            system_prompt: System prompt (should include tool descriptions)
            tool_executor: ToolExecutor instance for executing tools
            max_tool_iterations: Maximum number of tool call iterations
        """
        super().__init__(model_name, api_key, system_prompt)
        self.tool_executor = tool_executor
        self.max_tool_iterations = max_tool_iterations
    
    def parse_tool_calls(self, text: str) -> List[Dict[str, Any]]:
        """
        Parse tool calls from agent output.
        
        Supports multiple formats:
        1. TOOL_CALL: tool_name(arg1=value1, arg2=value2)  - key=value format
        2. TOOL_CALL: tool_name([...])                     - positional list format
        3. TOOL_CALL: tool_name(events=[...])              - key=list format
        4. <tool_call>tool_name(...)</tool_call>           - XML-style format
        5. **TOOL_CALL:** tool_name(...)                   - Markdown bold variant
        6. **TOOL_CALL: tool_name**\n args...              - Markdown header variant
        
        Args:
            text: Agent output text
            
        Returns:
            List of tool call dictionaries with 'name' and 'args'
        """
        tool_calls = []
        
        # First, try to parse XML-style <tool_call> blocks
        xml_tool_calls = self._parse_xml_tool_calls(text)
        if xml_tool_calls:
            tool_calls.extend(xml_tool_calls)
        
        # Try to parse markdown-style tool calls: **TOOL_CALL:** tool_name or **TOOL_CALL: tool_name**
        markdown_tool_calls = self._parse_markdown_tool_calls(text)
        if markdown_tool_calls:
            tool_calls.extend(markdown_tool_calls)
        
        # Pattern: TOOL_CALL: function_name( - match the start of tool call
        # We'll manually find the matching closing parenthesis to handle multi-line args
        pattern = r'TOOL_CALL:\s*(\w+)\s*\('
        
        matches = re.finditer(pattern, text, re.MULTILINE | re.DOTALL)
        
        for match in matches:
            tool_name = match.group(1)
            start_pos = match.end()  # Position after opening '('
            
            # Find matching closing parenthesis by counting depth
            depth = 1
            end_pos = start_pos
            in_string = False
            string_char = None
            
            while end_pos < len(text) and depth > 0:
                char = text[end_pos]
                
                # Handle string literals (don't count parens inside strings)
                if char in '"\'':
                    if not in_string:
                        in_string = True
                        string_char = char
                    elif char == string_char and (end_pos == 0 or text[end_pos-1] != '\\'):
                        in_string = False
                        string_char = None
                elif not in_string:
                    if char == '(':
                        depth += 1
                    elif char == ')':
                        depth -= 1
                
                end_pos += 1
            
            if depth == 0:
                args_str = text[start_pos:end_pos-1].strip()  # Extract args between ( and )
                
                # Parse arguments using enhanced parsing
                args = self._parse_tool_arguments(args_str, tool_name)
                
                tool_calls.append({
                    'name': tool_name,
                    'args': args,
                    'raw': text[match.start():end_pos]
                })
        
        return tool_calls
    
    def _parse_xml_tool_calls(self, text: str) -> List[Dict[str, Any]]:
        """
        Parse XML-style tool calls: <tool_call>tool_name(...)</tool_call>
        
        Args:
            text: Agent output text
            
        Returns:
            List of tool call dictionaries
        """
        tool_calls = []
        
        # Pattern to match <tool_call>...</tool_call> blocks
        # Use DOTALL to match across newlines
        xml_pattern = r'<tool_call>\s*(\w+)\s*\(([\s\S]*?)\)\s*</tool_call>'
        
        matches = re.finditer(xml_pattern, text, re.MULTILINE | re.DOTALL)
        
        for match in matches:
            tool_name = match.group(1)
            args_content = match.group(2).strip()
            
            # Determine how to parse the arguments
            args = {}
            
            if args_content:
                # Check if args_content looks like raw event data (starts with {)
                # This handles: <tool_call>analyze_ecommerce_behavior({2025-01-01...},...)</tool_call>
                if args_content.startswith('{') or args_content.startswith('\n{'):
                    # It's raw event data passed directly - wrap as events_data
                    args['events_data'] = args_content
                elif '=' in args_content and not args_content.startswith('{'):
                    # It's key=value format
                    args = self._parse_tool_arguments(args_content, tool_name)
                else:
                    # Assume it's the events_data parameter
                    args['events_data'] = args_content
            
            tool_calls.append({
                'name': tool_name,
                'args': args,
                'raw': match.group(0)
            })
        
        return tool_calls
    
    def _parse_markdown_tool_calls(self, text: str) -> List[Dict[str, Any]]:
        """
        Parse Markdown-style tool calls.
        
        Supports formats:
        1. **TOOL_CALL:** tool_name(args...)
        2. **TOOL_CALL: tool_name**\n Parameters: ...
        3. **TOOL_CALL: tool_name(args...)**
        
        Args:
            text: Agent output text
            
        Returns:
            List of tool call dictionaries
        """
        tool_calls = []
        
        # Pattern 1: **TOOL_CALL:** tool_name( or **TOOL_CALL: tool_name(
        # This is a common LLM output format with markdown bold
        pattern1 = r'\*\*TOOL_CALL:\*\*\s*(\w+)\s*\('
        
        for match in re.finditer(pattern1, text, re.MULTILINE):
            tool_name = match.group(1)
            start_pos = match.end()
            
            # Find matching closing parenthesis
            args_str = self._extract_parenthesized_content(text, start_pos)
            if args_str is not None:
                args = self._parse_tool_arguments(args_str, tool_name)
                tool_calls.append({
                    'name': tool_name,
                    'args': args,
                    'raw': text[match.start():start_pos + len(args_str) + 1]
                })
        
        # Pattern 2: **TOOL_CALL: tool_name**\n followed by parameters
        # Example:
        # **TOOL_CALL: extract_features**
        # Parameters: events_data = "..."
        pattern2 = r'\*\*TOOL_CALL:\s*(\w+)\*\*\s*\n\s*(?:\*\*)?(?:Parameters?|Args?)(?:\*\*)?:\s*(\w+)\s*=\s*(.+?)(?:\n\n|\n\*\*|$)'
        
        for match in re.finditer(pattern2, text, re.MULTILINE | re.DOTALL):
            tool_name = match.group(1)
            param_name = match.group(2)
            param_value = match.group(3).strip()
            
            # Clean up the parameter value
            if param_value.startswith('"') or param_value.startswith("'"):
                content = self._extract_quoted_string(param_value)
                if content is not None:
                    param_value = content
            
            tool_calls.append({
                'name': tool_name,
                'args': {param_name: param_value},
                'raw': match.group(0)
            })
        
        return tool_calls
    
    def _extract_parenthesized_content(self, text: str, start_pos: int) -> Optional[str]:
        """
        Extract content between parentheses starting at start_pos.
        Handles nested parentheses and quoted strings.
        
        Args:
            text: Full text
            start_pos: Position right after opening parenthesis
            
        Returns:
            Content between parentheses, or None if not found
        """
        depth = 1
        end_pos = start_pos
        in_string = False
        string_char = None
        
        while end_pos < len(text) and depth > 0:
            char = text[end_pos]
            
            # Handle string literals (don't count parens inside strings)
            if char in '"\'':
                if not in_string:
                    in_string = True
                    string_char = char
                elif char == string_char and (end_pos == 0 or text[end_pos-1] != '\\'):
                    in_string = False
                    string_char = None
            elif not in_string:
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
            
            end_pos += 1
        
        if depth == 0:
            return text[start_pos:end_pos-1].strip()
        
        return None
    
    def _parse_tool_arguments(self, args_str: str, tool_name: str) -> Dict[str, Any]:
        """
        Enhanced argument parsing that handles multiple formats including multi-line strings.
        
        Args:
            args_str: The argument string between parentheses
            tool_name: Tool name (for inferring parameter names)
            
        Returns:
            Dictionary of parsed arguments
        """
        args = {}
        
        if not args_str.strip():
            return args
        
        args_str = args_str.strip()
        
        # Check if it's a positional argument (starts with [ or {)
        # This handles: TOOL_CALL: func([...]) format
        if args_str.startswith('[') or args_str.startswith('{'):
            # It's a positional argument - assume first parameter is 'events_data' for most tools
            # Keep as string for event data (don't try to parse JSON)
            param_name = 'events_data'  # Default for feature extraction tools
            args[param_name] = args_str
            return args
        
        # Check if it's a single key=value where value is a quoted string (possibly multi-line)
        # This handles: TOOL_CALL: func(events_data="line1\nline2\nline3") format
        first_eq = args_str.find('=')
        if first_eq != -1:
            potential_key = args_str[:first_eq].strip()
            # Check if it's a valid key (no special chars except underscore)
            if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', potential_key):
                potential_value = args_str[first_eq+1:].strip()
                
                # Check if value is a quoted string (possibly multi-line)
                if potential_value.startswith('"') or potential_value.startswith("'"):
                    quote_char = potential_value[0]
                    # Find the matching closing quote
                    content = self._extract_quoted_string(potential_value)
                    if content is not None:
                        args[potential_key] = content
                        return args
                
                # Check if the rest is a complete value (list, dict, or simple)
                if potential_value.startswith('[') or potential_value.startswith('{'):
                    # For event data, keep as string rather than parsing to JSON
                    # This preserves the original format
                    args[potential_key] = potential_value
                    return args
        
        # Fall back to splitting by comma for key=value pairs
        # But be careful with multi-line string values
        arg_pairs = self._split_args(args_str)
        for pair in arg_pairs:
            if '=' in pair:
                key, value = pair.split('=', 1)
                key = key.strip()
                value = value.strip()
                
                # Handle quoted strings
                if value.startswith('"') or value.startswith("'"):
                    content = self._extract_quoted_string(value)
                    if content is not None:
                        args[key] = content
                        continue
                
                # Parse the value
                parsed_value = self._safe_parse_value(value)
                if parsed_value is not None:
                    args[key] = parsed_value
                else:
                    # Keep as string (strip quotes if present)
                    args[key] = value.strip('"\'')
        
        return args
    
    def _extract_quoted_string(self, s: str) -> Optional[str]:
        """
        Extract the content of a quoted string, handling escapes.
        
        Args:
            s: String starting with a quote character
            
        Returns:
            The string content (without quotes), or None if not a valid quoted string
        """
        if not s or len(s) < 2:
            return None
        
        quote_char = s[0]
        if quote_char not in '"\'':
            return None
        
        # Find matching closing quote
        i = 1
        escaped = False
        while i < len(s):
            if escaped:
                escaped = False
                i += 1
                continue
            
            if s[i] == '\\':
                escaped = True
                i += 1
                continue
            
            if s[i] == quote_char:
                # Found closing quote
                content = s[1:i]
                # Unescape the content
                content = content.replace('\\n', '\n')
                content = content.replace('\\t', '\t')
                content = content.replace('\\r', '\r')
                content = content.replace(f'\\{quote_char}', quote_char)
                content = content.replace('\\\\', '\\')
                return content
            
            i += 1
        
        # No closing quote found - might be unclosed or extends to end
        # Return everything after the opening quote
        content = s[1:]
        content = content.replace('\\n', '\n')
        content = content.replace('\\t', '\t')
        content = content.replace('\\r', '\r')
        return content
    
    def _safe_parse_value(self, value_str: str) -> Any:
        """
        Safely parse a value string into Python object.
        
        Handles:
        - JSON arrays and objects
        - Python lists and dicts (with None, True, False)
        - Simple strings and numbers
        
        Args:
            value_str: String to parse
            
        Returns:
            Parsed value or None if parsing fails
        """
        value_str = value_str.strip()
        
        if not value_str:
            return None
        
        # First try: Parse as JSON (handles true, false, null)
        try:
            return json.loads(value_str)
        except json.JSONDecodeError:
            pass
        
        # Second try: Convert Python-style to JSON-style and try again
        # None -> null, True -> true, False -> false
        try:
            # Replace Python keywords with JSON equivalents
            json_str = value_str
            json_str = re.sub(r'\bNone\b', 'null', json_str)
            json_str = re.sub(r'\bTrue\b', 'true', json_str)
            json_str = re.sub(r'\bFalse\b', 'false', json_str)
            # Replace single quotes with double quotes (carefully)
            json_str = self._convert_python_strings_to_json(json_str)
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
        
        # Third try: Use ast.literal_eval for Python literals
        try:
            import ast
            return ast.literal_eval(value_str)
        except (ValueError, SyntaxError):
            pass
        
        # Return None to indicate parsing failed
        return None
    
    def _convert_python_strings_to_json(self, s: str) -> str:
        """
        Convert Python-style single-quoted strings to JSON double-quoted.
        Handles nested quotes and escaping.
        """
        result = []
        i = 0
        while i < len(s):
            if s[i] == "'":
                # Check if it's a string delimiter (not inside a double-quoted string)
                # Simple heuristic: replace ' with " if not preceded by \
                if i > 0 and s[i-1] == '\\':
                    result.append(s[i])
                else:
                    result.append('"')
            else:
                result.append(s[i])
            i += 1
        return ''.join(result)
    
    def _split_args(self, args_str: str) -> List[str]:
        """Split argument string by commas, respecting nested structures."""
        args = []
        current = []
        depth = 0
        
        for char in args_str:
            if char in '([{':
                depth += 1
                current.append(char)
            elif char in ')]}':
                depth -= 1
                current.append(char)
            elif char == ',' and depth == 0:
                args.append(''.join(current).strip())
                current = []
            else:
                current.append(char)
        
        if current:
            args.append(''.join(current).strip())
        
        return args
    
    def execute_with_tools(self, input_text: str) -> Tuple[str, List[Dict]]:
        """
        Execute agent with tool support.
        
        The agent can make tool calls, which will be executed and
        the results fed back to the agent for further reasoning.
        
        Args:
            input_text: User input
            
        Returns:
            (final_output, tool_execution_log)
        """
        if self.tool_executor is None:
            # No tool executor, fall back to standard call
            return self._call_without_tools(input_text), []
        
        conversation_history = []
        tool_execution_log = []
        current_input = input_text
        
        for iteration in range(self.max_tool_iterations):
            # Get agent response (WITHOUT triggering tool execution again)
            output = self._call_without_tools(current_input)
            conversation_history.append({
                'iteration': iteration,
                'input': current_input,
                'output': output
            })
            
            # Check for tool calls
            tool_calls = self.parse_tool_calls(output)
            
            if not tool_calls:
                # No tool calls, return the output
                # Store conversation history in state for observer to capture
                if not hasattr(self, 'state'):
                    self.state = {}
                self.state['conversation_history'] = conversation_history
                return output, tool_execution_log
            
            # Execute tool calls
            tool_results = []
            for tool_call in tool_calls:
                result = self.tool_executor.execute_tool_by_name(
                    tool_call['name'],
                    tool_call['args']
                )
                
                tool_results.append({
                    'tool': tool_call['name'],
                    'args': tool_call['args'],
                    'success': result['success'],
                    'result': result.get('result'),
                    'error': result.get('error')
                })
                
                tool_execution_log.append({
                    'iteration': iteration,
                    'tool_name': tool_call['name'],
                    'arguments': tool_call['args'],
                    'success': result['success'],
                    'result': result.get('result'),
                    'error': result.get('error'),
                    'raw_output': str(result.get('result', ''))
                })
            
            # Format tool results for next iteration
            tool_results_text = self._format_tool_results(tool_results)
            
            # Continue conversation with tool results
            current_input = f"{input_text}\n\n[Previous attempt and tool results]\n{output}\n\n{tool_results_text}\n\nPlease continue based on the tool results above."
        
        # Max iterations reached, return last output
        # Store conversation history in state
        if not hasattr(self, 'state'):
            self.state = {}
        self.state['conversation_history'] = conversation_history
        return conversation_history[-1]['output'], tool_execution_log
    
    def _format_tool_results(self, tool_results: List[Dict]) -> str:
        """Format tool execution results for agent."""
        formatted = ["Tool execution results:"]
        
        for i, result in enumerate(tool_results, 1):
            formatted.append(f"\n[Tool {i}]: {result['tool']}")
            formatted.append(f"  Args: {result['args']}")
            if result['success']:
                formatted.append(f"  Result: {result['result']}")
            else:
                formatted.append(f"  Error: {result['error']}")
        
        return '\n'.join(formatted)
    
    def _call_without_tools(self, input_text: str) -> str:
        """
        Internal method to call LLM without tool execution.
        Must be implemented by subclass or will call the subclass's call method.
        """
        # This will be overridden by subclass behavior
        raise NotImplementedError("Subclass must implement _call_without_tools or override call()")
    
    def call(self, input_text: str) -> str:
        """
        Standard call method (no tool execution).
        Override in subclass for actual model calls.
        """
        raise NotImplementedError

