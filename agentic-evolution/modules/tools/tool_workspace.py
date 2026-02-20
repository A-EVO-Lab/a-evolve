"""Tool workspace for managing tools in isolated training sessions."""

import os
import shutil
from typing import Optional
from datetime import datetime
from .tool_builder import ToolBuilder
from .tool_registry import ToolRegistry
from .tool_executor import ToolExecutor


class ToolWorkspace:
    """
    Manages a complete tool environment for a single training session.
    
    Each workspace has its own:
    - Tool registry
    - Generated tools directory
    - Tool builder and executor
    
    This provides isolation between different training runs while allowing
    tools to evolve within a single session.
    """
    
    def __init__(self, session_id: str, base_dir: str = "tools_sandbox"):
        """
        Initialize a tool workspace for a training session.
        
        Args:
            session_id: Unique identifier for this training session
            base_dir: Base directory for all tool storage
        """
        self.session_id = session_id
        self.base_dir = base_dir
        self.workspace_dir = os.path.join(base_dir, "sessions", session_id)
        
        # Create workspace directory structure
        os.makedirs(os.path.join(self.workspace_dir, "tools"), exist_ok=True)
        
        # Initialize components with session-specific paths
        self.builder = ToolBuilder(
            output_dir=os.path.join(base_dir, "generated"),
            session_id=session_id
        )
        self.registry = ToolRegistry(
            registry_path=os.path.join(base_dir, "registry.json"),
            session_id=session_id
        )
        # IMPORTANT: Executor should use the SAME registry instance
        # to avoid synchronization issues
        self.executor = ToolExecutor(
            registry_path=os.path.join(base_dir, "registry.json"),
            session_id=session_id
        )
        # Override executor's registry with our registry instance
        self.executor.registry = self.registry
    
    def get_context(self):
        """
        Get context dictionary for proposer.
        
        Returns:
            dict with tool_registry and tool_executor
        """
        return {
            "tool_registry": self.registry,
            "tool_executor": self.executor
        }
    
    def get_stats(self):
        """Get statistics about this workspace."""
        stats = self.registry.get_tool_stats()
        stats["session_id"] = self.session_id
        stats["workspace_dir"] = self.workspace_dir
        return stats
    
    def cleanup(self):
        """
        Clean up this workspace (deletes all tools and registry).
        Use with caution!
        """
        if os.path.exists(self.workspace_dir):
            shutil.rmtree(self.workspace_dir)
    
    def export_tools(self, export_dir: str):
        """
        Export tools from this workspace to a directory.
        
        Args:
            export_dir: Directory to export tools to
        """
        os.makedirs(export_dir, exist_ok=True)
        
        # Copy registry
        import json
        with open(self.registry.registry_path, 'r') as f:
            registry_data = json.load(f)
        
        with open(os.path.join(export_dir, "registry.json"), 'w') as f:
            json.dump(registry_data, f, indent=2)
        
        # Copy tool files
        tools_dir = os.path.join(self.base_dir, "generated", self.session_id)
        if os.path.exists(tools_dir):
            export_tools_dir = os.path.join(export_dir, "tools")
            shutil.copytree(tools_dir, export_tools_dir, dirs_exist_ok=True)
    
    @staticmethod
    def generate_session_id(
        model_name: str = None,
        train_samples: int = None,
        timestamp: datetime = None
    ) -> str:
        """
        Generate a session ID based on training parameters.
        
        Similar to the buffer_path naming in run_example.py
        
        Args:
            model_name: Model name (e.g., "claude-sonnet-4")
            train_samples: Number of training samples
            timestamp: Timestamp (default: now)
            
        Returns:
            Session ID string like "claude_sonnet_4_train_100_20251213_123045"
        """
        if timestamp is None:
            timestamp = datetime.now()
        
        timestamp_str = timestamp.strftime('%Y%m%d_%H%M%S')
        
        # Clean model name
        if model_name:
            # Shorten model name similar to run_example.py
            model_clean = model_name.replace('/', '_').replace(':', '_').replace('.', '_')
            if 'claude' in model_clean.lower():
                parts = [p for p in model_clean.split('_') 
                        if 'claude' in p or 'sonnet' in p or 'opus' in p or 'haiku' in p 
                        or any(c.isdigit() for c in p)]
                model_clean = '_'.join(parts)[:30]
            elif 'gpt' in model_clean.lower():
                parts = [p for p in model_clean.split('_') 
                        if 'gpt' in p or any(c.isdigit() for c in p)]
                model_clean = '_'.join(parts)[:30]
            else:
                model_clean = model_clean[:30]
        else:
            model_clean = "model"
        
        # Build session ID
        parts = [model_clean]
        if train_samples:
            parts.append(f"train_{train_samples}")
        parts.append(timestamp_str)
        
        return "_".join(parts)
    
    @staticmethod
    def list_sessions(base_dir: str = "tools_sandbox") -> list:
        """
        List all existing workspace sessions.
        
        Args:
            base_dir: Base directory for tool storage
            
        Returns:
            List of session IDs
        """
        sessions_dir = os.path.join(base_dir, "sessions")
        if not os.path.exists(sessions_dir):
            return []
        
        return [d for d in os.listdir(sessions_dir) 
                if os.path.isdir(os.path.join(sessions_dir, d))]


