"""Tool registry for managing built-in and generated tools."""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any
import importlib.util
import sys


class ToolRegistry:
    """
    Manages tool registration, discovery, and metadata.
    
    Tools can be:
    - Built-in: Pre-defined tools in tools_sandbox/builtin/
    - Generated: Model-generated tools in tools_sandbox/generated/
    """
    
    def __init__(self, registry_path: str = "tools_sandbox/registry.json", session_id: Optional[str] = None):
        """
        Initialize the tool registry.
        
        Args:
            registry_path: Base path to the registry JSON file
            session_id: Optional session ID for isolating tools per training run
        """
        if session_id:
            # Create session-specific registry path
            self.registry_path = registry_path.replace("registry.json", f"sessions/{session_id}/registry.json")
        else:
            self.registry_path = registry_path
        self.session_id = session_id
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.load()
    
    def load(self):
        """Load tools from registry file and validate tool files exist."""
        if os.path.exists(self.registry_path):
            with open(self.registry_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # Validate each tool
                valid_tools = []
                invalid_tools = []
                
                for tool in data.get("tools", []):
                    # Check if tool file exists (for generated tools)
                    if tool.get("type") == "generated":
                        file_path = tool.get("file_path", "")
                        if file_path and not os.path.exists(file_path):
                            invalid_tools.append(tool)
                            continue
                    
                    valid_tools.append(tool)
                    self.tools[tool["id"]] = tool
                
                # Report and clean up invalid tools
                if invalid_tools:
                    print(f"[ToolRegistry] ⚠️  Found {len(invalid_tools)} tool(s) with missing files:")
                    for tool in invalid_tools:
                        print(f"[ToolRegistry]    - {tool['name']}: {tool.get('file_path', 'N/A')}")
                    print(f"[ToolRegistry]    These zombie entries will be removed from registry")
                    
                    # Save the cleaned registry
                    self.save()
        else:
            # Initialize with empty registry
            self.tools = {}
            self.save()
    
    def save(self):
        """Save tools to registry file."""
        os.makedirs(os.path.dirname(self.registry_path), exist_ok=True)
        data = {
            "version": "1.0",
            "tools": list(self.tools.values())
        }
        with open(self.registry_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def register_tool(self, tool_info: Dict[str, Any]) -> str:
        """
        Register a new tool.
        
        Args:
            tool_info: Tool metadata including:
                - name: Tool name
                - type: "builtin" or "generated"
                - module: Module path
                - function: Function name
                - description: Tool description
                - parameters: Parameter specifications
                - return_type: Return type
                - file_path: Path to the tool file (for generated tools)
                
        Returns:
            Tool ID
            
        Raises:
            ValueError: If tool file doesn't exist for generated tools
        """
        # Validate file exists for generated tools
        if tool_info.get("type") == "generated":
            file_path = tool_info.get("file_path", "")
            if not file_path:
                raise ValueError(f"Generated tool '{tool_info.get('name')}' must have file_path")
            if not os.path.exists(file_path):
                raise ValueError(f"Tool file not found: {file_path}")
        
        # Generate unique ID
        tool_id = f"{tool_info['type']}_{tool_info['name']}"
        
        # Add metadata
        tool_info["id"] = tool_id
        tool_info["created_at"] = datetime.utcnow().isoformat() + "Z"
        tool_info["usage_count"] = tool_info.get("usage_count", 0)
        tool_info["success_rate"] = tool_info.get("success_rate", 1.0)
        tool_info["last_used"] = None
        tool_info["last_updated"] = None
        
        # Register
        self.tools[tool_id] = tool_info
        self.save()
        
        return tool_id
    
    def validate_tools(self) -> Dict[str, List[str]]:
        """
        Validate all registered tools and remove invalid ones.
        
        Returns:
            Dict with 'valid' and 'removed' tool names
        """
        valid_tools = []
        removed_tools = []
        
        for tool_id, tool in list(self.tools.items()):
            if tool.get("type") == "generated":
                file_path = tool.get("file_path", "")
                if not file_path or not os.path.exists(file_path):
                    removed_tools.append(tool["name"])
                    del self.tools[tool_id]
                    continue
            
            valid_tools.append(tool["name"])
        
        if removed_tools:
            self.save()
        
        return {
            "valid": valid_tools,
            "removed": removed_tools
        }
    
    def update_tool(self, tool_id: str, updates: Dict[str, Any]):
        """
        Update tool metadata.
        
        Args:
            tool_id: Tool ID
            updates: Fields to update
        """
        if tool_id not in self.tools:
            raise ValueError(f"Tool {tool_id} not found")
        
        self.tools[tool_id].update(updates)
        self.tools[tool_id]["last_updated"] = datetime.utcnow().isoformat() + "Z"
        self.save()
    
    def get_tool(self, tool_id: str) -> Optional[Dict[str, Any]]:
        """Get tool metadata by ID."""
        return self.tools.get(tool_id)
    
    def get_tool_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get tool metadata by name (returns first match)."""
        for tool in self.tools.values():
            if tool["name"] == name:
                return tool
        return None
    
    def list_tools(self, tool_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all tools.
        
        Args:
            tool_type: Filter by type ("builtin" or "generated")
            
        Returns:
            List of tool metadata
        """
        tools = list(self.tools.values())
        if tool_type:
            tools = [t for t in tools if t["type"] == tool_type]
        return tools
    
    def remove_tool(self, tool_id: str):
        """Remove a tool from registry."""
        if tool_id in self.tools:
            del self.tools[tool_id]
            self.save()
    
    def record_usage(self, tool_id: str, success: bool):
        """
        Record tool usage for statistics.
        
        Args:
            tool_id: Tool ID
            success: Whether the execution was successful
        """
        if tool_id not in self.tools:
            return
        
        tool = self.tools[tool_id]
        usage_count = tool.get("usage_count", 0)
        success_rate = tool.get("success_rate", 1.0)
        
        # Update statistics
        new_count = usage_count + 1
        new_success_rate = (success_rate * usage_count + (1 if success else 0)) / new_count
        
        tool["usage_count"] = new_count
        tool["success_rate"] = new_success_rate
        tool["last_used"] = datetime.utcnow().isoformat() + "Z"
        
        self.save()
    
    def get_tool_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics about tools."""
        total_tools = len(self.tools)
        builtin_tools = len([t for t in self.tools.values() if t["type"] == "builtin"])
        generated_tools = len([t for t in self.tools.values() if t["type"] == "generated"])
        
        total_usage = sum(t.get("usage_count", 0) for t in self.tools.values())
        avg_success_rate = (
            sum(t.get("success_rate", 0) for t in self.tools.values()) / total_tools
            if total_tools > 0 else 0
        )
        
        return {
            "total_tools": total_tools,
            "builtin_tools": builtin_tools,
            "generated_tools": generated_tools,
            "total_usage": total_usage,
            "avg_success_rate": avg_success_rate
        }
    
    def export_tool_signature(self, tool_id: str, include_usage_prompt: bool = True) -> str:
        """
        Export tool signature for LLM consumption.
        
        Returns a formatted string describing the tool that can be included
        in the agent's system prompt.
        
        Args:
            tool_id: Tool identifier
            include_usage_prompt: Whether to include the tool-specific usage prompt
        """
        tool = self.get_tool(tool_id)
        if not tool:
            return ""
        
        params = []
        parameters = tool.get("parameters", {})
        
        # Handle case where parameters is not a dict (e.g., list or None)
        if not isinstance(parameters, dict):
            parameters = {}
        
        # Handle both formats: JSON Schema format and simple dict format
        if "properties" in parameters:
            # JSON Schema format: {"type": "object", "properties": {...}, "required": [...]}
            properties = parameters.get("properties", {})
            required_params = parameters.get("required", [])
            
            for param_name, param_info in properties.items():
                # param_info could be a dict or a string
                if isinstance(param_info, dict):
                    param_type = param_info.get("type", "Any")
                    param_desc = param_info.get("description", "")
                    default = param_info.get("default")
                    is_required = param_name in required_params
                elif isinstance(param_info, str):
                    # If it's a string, treat it as the type
                    param_type = param_info
                    param_desc = ""
                    default = None
                    is_required = param_name in required_params
                else:
                    continue
                
                if is_required:
                    params.append(f"{param_name}: {param_type} - {param_desc}")
                else:
                    default_str = f" (default: {default})" if default is not None else ""
                    params.append(f"{param_name}: {param_type} - {param_desc}{default_str} [optional]")
        else:
            # Simple dict format: {"param_name": {"type": "...", "description": "...", "required": True}}
            for param_name, param_info in parameters.items():
                # Skip metadata fields like 'type', 'properties', 'required'
                if param_name in ['type', 'properties', 'required']:
                    continue
                    
                if isinstance(param_info, dict):
                    required = param_info.get("required", False)
                    param_type = param_info.get("type", "Any")
                    param_desc = param_info.get("description", "")
                    default = param_info.get("default")
                elif isinstance(param_info, str):
                    # If it's a string, treat it as the type
                    param_type = param_info
                    param_desc = ""
                    default = None
                    required = False
                else:
                    continue
                
                if required:
                    params.append(f"{param_name}: {param_type} - {param_desc}")
                else:
                    default_str = f" (default: {default})" if default is not None else ""
                    params.append(f"{param_name}: {param_type} - {param_desc}{default_str} [optional]")
        
        params_str = "\n  ".join(params) if params else "None"
        
        # Get parameter names for usage example
        parameters = tool.get("parameters", {})
        if "properties" in parameters:
            param_names = list(parameters.get("properties", {}).keys())
        else:
            param_names = [k for k in parameters.keys() if k not in ['type', 'properties', 'required']]
        
        # Build the signature
        signature = f"""### Tool: `{tool['name']}`
**Description:** {tool['description']}
**Parameters:**
  {params_str}
**Returns:** {tool.get('return_type', 'Any')}
"""
        
        # Add usage_prompt if available and requested
        if include_usage_prompt and tool.get('usage_prompt'):
            signature += f"\n**How to Use This Tool:**\n{tool['usage_prompt']}\n"
        else:
            # Provide a generic usage example
            signature += f"**Usage:** `TOOL_CALL: {tool['name']}({', '.join(param_names)})`\n"
        
        return signature
    
    def export_all_signatures(self, include_usage_prompts: bool = True) -> str:
        """
        Export all tool signatures for LLM consumption.
        
        Args:
            include_usage_prompts: Whether to include tool-specific usage prompts
        """
        signatures = []
        for tool_id in sorted(self.tools.keys()):
            signatures.append(self.export_tool_signature(tool_id, include_usage_prompts))
        return "\n\n".join(signatures)
    
    def export_tool_with_code(self, tool_id: str) -> Dict[str, Any]:
        """
        Export tool including its source code for evolution analysis.
        
        Args:
            tool_id: Tool identifier
            
        Returns:
            Dictionary with tool metadata and full source code
        """
        tool = self.get_tool(tool_id)
        if not tool:
            return None
        
        # Read the actual implementation code
        code = ""
        file_path = tool.get('file_path')
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    code = f.read()
            except Exception as e:
                code = f"# Error reading code: {str(e)}"
        
        return {
            'id': tool['id'],
            'name': tool['name'],
            'description': tool['description'],
            'parameters': tool['parameters'],
            'return_type': tool['return_type'],
            'code': code,  # Full implementation
            'usage_prompt': tool.get('usage_prompt', ''),  # Tool-specific usage instructions
            'usage_count': tool.get('usage_count', 0),
            'success_rate': tool.get('success_rate', 1.0),
            'created_at': tool.get('created_at'),
            'last_used': tool.get('last_used'),
            'file_path': file_path
        }
    
    def export_tools_for_evolution(
        self, 
        include_code: bool = True, 
        min_usage: int = 3,
        max_tools: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Export tools that are candidates for evolution.
        
        Only includes tools that have been used enough to identify patterns.
        Sorted by usage to prioritize frequently used tools.
        
        Args:
            include_code: Whether to include full source code
            min_usage: Minimum usage count to be considered
            max_tools: Maximum number of tools to return
            
        Returns:
            List of tool dictionaries with code and statistics
        """
        # Get tools with sufficient usage
        candidates = []
        for tool_id, tool in self.tools.items():
            if tool.get('usage_count', 0) >= min_usage:
                if include_code:
                    tool_data = self.export_tool_with_code(tool_id)
                else:
                    tool_data = dict(tool)
                
                if tool_data:
                    candidates.append(tool_data)
        
        # Sort by usage count (most used first)
        candidates.sort(key=lambda t: t.get('usage_count', 0), reverse=True)
        
        # Return top N
        return candidates[:max_tools]

