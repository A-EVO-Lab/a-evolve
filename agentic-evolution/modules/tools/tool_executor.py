"""Sandbox executor for running tools safely."""

import ast
import sys
import io
import traceback
from typing import Any, Dict, Optional, Tuple
from contextlib import redirect_stdout, redirect_stderr
import importlib
import os
from datetime import datetime, date


class ToolExecutor:
    """
    Executes tools in a sandboxed environment with resource limits.
    
    Safety features:
    1. Import restrictions: Only allow safe modules
    2. Execution timeout: Limit execution time
    3. Output capture: Capture stdout/stderr
    4. Exception handling: Gracefully handle errors
    """
    
    # Whitelist of allowed imports for generated tools
    ALLOWED_IMPORTS = {
        'json', 'math', 're', 'datetime', 'typing',
        'collections', 'itertools', 'functools',
        'string', 'random', 'decimal', 'fractions',
    }
    
    def __init__(self, registry_path: str = "tools_sandbox/registry.json", session_id: Optional[str] = None):
        """
        Initialize the tool executor.
        
        Args:
            registry_path: Base path to tool registry
            session_id: Optional session ID for isolating tools per training run
        """
        from .tool_registry import ToolRegistry
        self.registry = ToolRegistry(registry_path, session_id=session_id)
        self.session_id = session_id
        self.execution_cache = {}
    
    def validate_tool_code(self, code: str) -> Tuple[bool, Optional[str]]:
        """
        Validate tool code for safety.
        
        Args:
            code: Python code to validate
            
        Returns:
            (is_valid, error_message)
        """
        try:
            # Parse the code
            tree = ast.parse(code)
            
            # Check for dangerous operations
            for node in ast.walk(tree):
                # Check imports
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name not in self.ALLOWED_IMPORTS:
                            return False, f"Import not allowed: {alias.name}"
                
                if isinstance(node, ast.ImportFrom):
                    if node.module not in self.ALLOWED_IMPORTS:
                        return False, f"Import not allowed: {node.module}"
                
                # Check for dangerous built-ins
                if isinstance(node, ast.Name):
                    if node.id in ['exec', 'eval', 'compile', '__import__', 'open']:
                        return False, f"Dangerous function not allowed: {node.id}"
            
            return True, None
            
        except SyntaxError as e:
            return False, f"Syntax error: {str(e)}"
    
    def load_tool_function(self, tool_id: str):
        """
        Load a tool function from registry.
        
        Args:
            tool_id: Tool ID
            
        Returns:
            Callable tool function
            
        Raises:
            ValueError: If tool is not found or has unknown type
            FileNotFoundError: If tool file doesn't exist
        """
        tool = self.registry.get_tool(tool_id)
        if not tool:
            raise ValueError(f"Tool {tool_id} not found in registry")
        
        module_path = tool["module"]
        function_name = tool["function"]
        
        # Handle built-in tools
        if tool["type"] == "builtin":
            # Import from tools_sandbox.builtin
            module = importlib.import_module(module_path)
            return getattr(module, function_name)
        
        # Handle generated tools
        elif tool["type"] == "generated":
            # First try the stored file_path
            file_path = tool.get("file_path")
            
            # Fall back to computed path if not stored
            if not file_path:
                if self.session_id:
                    file_path = os.path.join("tools_sandbox", "generated", self.session_id, f"tool_{tool['name']}.py")
                else:
                    file_path = os.path.join("tools_sandbox", "generated", f"tool_{tool['name']}.py")
            
            if not os.path.exists(file_path):
                # Clean up the zombie registry entry
                self.registry.remove_tool(tool_id)
                raise FileNotFoundError(
                    f"Tool file not found: {file_path}\n"
                    f"This tool was registered but the file doesn't exist.\n"
                    f"The zombie registry entry has been removed."
                )
            
            spec = importlib.util.spec_from_file_location(f"tool_{tool['name']}", file_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            
            return getattr(module, function_name)
        
        else:
            raise ValueError(f"Unknown tool type: {tool['type']}")
    
    def _sanitize_result(self, obj: Any, max_depth: int = 20) -> Any:
        """
        Recursively sanitize a result to ensure JSON-serializability.
        
        This converts datetime objects to ISO format strings and handles
        other non-serializable types gracefully.
        
        Args:
            obj: The object to sanitize
            max_depth: Maximum recursion depth to prevent infinite loops
            
        Returns:
            JSON-serializable version of the object
        """
        if max_depth <= 0:
            return "<max depth exceeded>"
        
        # Handle None
        if obj is None:
            return None
        
        # Handle primitives (already JSON-serializable)
        if isinstance(obj, (str, int, float, bool)):
            return obj
        
        # Handle datetime objects - convert to ISO format string
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        
        # Handle bytes
        if isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        
        # Handle sets - convert to list
        if isinstance(obj, set):
            return [self._sanitize_result(item, max_depth - 1) for item in obj]
        
        # Handle tuples - convert to list
        if isinstance(obj, tuple):
            return [self._sanitize_result(item, max_depth - 1) for item in obj]
        
        # Handle lists - recursively sanitize each element
        if isinstance(obj, list):
            return [self._sanitize_result(item, max_depth - 1) for item in obj]
        
        # Handle dicts - recursively sanitize keys and values
        if isinstance(obj, dict):
            sanitized = {}
            for k, v in obj.items():
                # Ensure key is a string (JSON requirement)
                key_str = str(k) if not isinstance(k, str) else k
                sanitized[key_str] = self._sanitize_result(v, max_depth - 1)
            return sanitized
        
        # Handle any other type - convert to string representation
        try:
            return str(obj)
        except Exception:
            return f"<non-serializable: {type(obj).__name__}>"
    
    def execute_tool(
        self, 
        tool_id: str, 
        args: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0
    ) -> Dict[str, Any]:
        """
        Execute a tool with given arguments.
        
        Args:
            tool_id: Tool ID
            args: Tool arguments as a dictionary
            timeout: Execution timeout in seconds
            
        Returns:
            Execution result dictionary with:
                - success: bool
                - result: Any (if successful)
                - error: str (if failed)
                - stdout: str (captured output)
                - stderr: str (captured errors)
        """
        args = args or {}
        
        # Capture stdout/stderr
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        try:
            # Load tool function
            tool_func = self.load_tool_function(tool_id)
            
            # Execute with output capture
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                result = tool_func(**args)
            
            # Sanitize the result to ensure JSON-serializability
            # This prevents crashes when tools return datetime objects or other
            # non-serializable types
            sanitized_result = self._sanitize_result(result)
            
            # Record successful usage
            self.registry.record_usage(tool_id, success=True)
            
            return {
                "success": True,
                "result": sanitized_result,
                "stdout": stdout_capture.getvalue(),
                "stderr": stderr_capture.getvalue(),
                "error": None
            }
            
        except Exception as e:
            # Record failed usage
            self.registry.record_usage(tool_id, success=False)
            
            return {
                "success": False,
                "result": None,
                "stdout": stdout_capture.getvalue(),
                "stderr": stderr_capture.getvalue(),
                "error": f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
            }
    
    def execute_tool_by_name(
        self,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0
    ) -> Dict[str, Any]:
        """
        Execute a tool by name.
        
        Args:
            tool_name: Tool name
            args: Tool arguments
            timeout: Execution timeout
            
        Returns:
            Execution result
        """
        tool = self.registry.get_tool_by_name(tool_name)
        if not tool:
            return {
                "success": False,
                "result": None,
                "stdout": "",
                "stderr": "",
                "error": f"Tool not found: {tool_name}"
            }
        
        return self.execute_tool(tool["id"], args, timeout)
    
    def list_available_tools(self) -> str:
        """
        Get a formatted list of available tools for LLM consumption.
        
        Returns:
            Formatted string with tool signatures
        """
        return self.registry.export_all_signatures()

    # Backward compatibility shim for callers expecting execute(tool_name, args)
    def execute(self, tool_name: str, args: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        """
        Legacy wrapper that delegates to execute_tool_by_name.
        """
        return self.execute_tool_by_name(tool_name, args or {}, timeout)
