"""Tool builder for generating tool code from specifications."""

import os
import json
from typing import Dict, Any, Optional
from datetime import datetime


class ToolBuilder:
    """
    Generates tool code from LLM-provided specifications.
    
    Responsibilities:
    1. Generate Python code for tools based on specifications
    2. Save tool files to tools_sandbox/generated/
    3. Validate generated code
    4. Register tools in the registry
    """
    
    TOOL_TEMPLATE = '''"""
Generated tool: {name}
Description: {description}
Created: {created_at}
"""

{imports}

{code}
'''
    
    def __init__(self, output_dir: str = "tools_sandbox/generated", session_id: Optional[str] = None):
        """
        Initialize tool builder.
        
        Args:
            output_dir: Base directory to save generated tools
            session_id: Optional session ID for isolating tools per training run
        """
        if session_id:
            # Create session-specific directory
            self.output_dir = os.path.join(output_dir, session_id)
        else:
            self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self.session_id = session_id
    
    def build_tool(
        self,
        name: str,
        description: str,
        code: str,
        parameters: Dict[str, Any],
        return_type: str,
        imports: Optional[list] = None,
        usage_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build a tool from specification.
        
        Args:
            name: Tool function name
            description: Tool description
            code: Python function code (the function definition)
            parameters: Parameter specifications
            return_type: Return type annotation
            imports: List of import statements (optional)
            usage_prompt: Tool-specific usage instructions for the LLM (optional but recommended)
            
        Returns:
            Tool metadata dictionary
        """
        # Validate tool name
        if not name.isidentifier():
            raise ValueError(f"Invalid tool name: {name}")
        
        # Prepare imports
        imports_str = ""
        if imports:
            imports_str = "\n".join(imports)
        
        # Generate full tool code
        tool_code = self.TOOL_TEMPLATE.format(
            name=name,
            description=description,
            created_at=datetime.utcnow().isoformat(),
            imports=imports_str,
            code=code
        )
        
        # Validate code (basic syntax check)
        try:
            compile(tool_code, "<string>", "exec")
        except SyntaxError as e:
            raise ValueError(f"Generated code has syntax error: {e}")
        
        # Save to file
        file_path = os.path.join(self.output_dir, f"tool_{name}.py")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(tool_code)
        
        # Create tool metadata
        tool_info = {
            "name": name,
            "type": "generated",
            "module": f"tools_sandbox.generated.tool_{name}",
            "function": name,
            "description": description,
            "parameters": parameters,
            "return_type": return_type,
            "file_path": file_path,
            "usage_prompt": usage_prompt or self._generate_default_usage_prompt(name, parameters)
        }
        
        return tool_info
    
    def _generate_default_usage_prompt(self, name: str, parameters: Dict[str, Any]) -> str:
        """
        Generate a default usage prompt if none is provided.
        
        Args:
            name: Tool name
            parameters: Parameter specifications
            
        Returns:
            Default usage prompt string
        """
        # Extract parameter names
        param_names = []
        if isinstance(parameters, dict):
            if "properties" in parameters:
                param_names = list(parameters.get("properties", {}).keys())
            else:
                param_names = [k for k in parameters.keys() if k not in ['type', 'properties', 'required']]
        
        params_str = ', '.join([f'{p}=...' for p in param_names]) if param_names else ''
        
        return f"""**How to call this tool:**
1. Format: `TOOL_CALL: {name}({params_str})`
2. Pass the input data as the appropriate parameter
3. The tool returns a dictionary of extracted features

**How to use the results:**
- Review the returned features
- Use them to inform your prediction (don't just copy the values)
- Combine with other context and behavioral patterns"""
    
    def update_tool(
        self,
        name: str,
        new_code: Optional[str] = None,
        new_description: Optional[str] = None,
        new_parameters: Optional[Dict[str, Any]] = None,
        new_usage_prompt: Optional[str] = None,
        new_imports: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        Update an existing tool.
        
        Args:
            name: Tool name
            new_code: New function code (optional)
            new_description: New description (optional)
            new_parameters: New parameter specs (optional)
            new_usage_prompt: New usage instructions (optional)
            new_imports: New import statements (optional)
            
        Returns:
            Updated tool metadata
        """
        file_path = os.path.join(self.output_dir, f"tool_{name}.py")
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Tool not found: {name}")
        
        # Read existing tool
        with open(file_path, 'r', encoding='utf-8') as f:
            existing_code = f.read()
        
        # For now, if new code is provided, rebuild the tool
        # (More sophisticated merging could be implemented)
        if new_code:
            # Extract imports from existing code (use new_imports if provided)
            if new_imports:
                imports = new_imports
            else:
                imports = []
                for line in existing_code.split('\n'):
                    if line.startswith('import ') or line.startswith('from '):
                        imports.append(line)
            
            # Rebuild with new code
            tool_info = self.build_tool(
                name=name,
                description=new_description or "Updated tool",
                code=new_code,
                parameters=new_parameters or {},
                return_type="Any",
                imports=imports,
                usage_prompt=new_usage_prompt
            )
            return tool_info
        
        # If only metadata is updated, return updated info
        return {
            "name": name,
            "type": "generated",
            "module": f"tools_sandbox.generated.tool_{name}",
            "function": name,
            "description": new_description,
            "parameters": new_parameters,
            "usage_prompt": new_usage_prompt,
        }
    
    def delete_tool(self, name: str):
        """
        Delete a tool file.
        
        Args:
            name: Tool name
        """
        file_path = os.path.join(self.output_dir, f"tool_{name}.py")
        if os.path.exists(file_path):
            os.remove(file_path)
    
    def parse_tool_spec(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse a tool specification from LLM output.
        
        Expected format:
        {
            "name": "my_tool",
            "description": "Does something useful",
            "code": "def my_tool(x: int) -> int:\n    return x * 2",
            "parameters": {
                "x": {"type": "int", "description": "Input value", "required": true}
            },
            "return_type": "int",
            "imports": ["import math"],
            "usage_prompt": "How to call this tool and interpret results..."
        }
        
        Args:
            spec: Tool specification dictionary
            
        Returns:
            Parsed tool metadata
        """
        required_fields = ["name", "description", "code"]
        for field in required_fields:
            if field not in spec:
                raise ValueError(f"Missing required field: {field}")
        
        return {
            "name": spec["name"],
            "description": spec["description"],
            "code": spec["code"],
            "parameters": spec.get("parameters", {}),
            "return_type": spec.get("return_type", "Any"),
            "imports": spec.get("imports", []),
            "usage_prompt": spec.get("usage_prompt", None)
        }

