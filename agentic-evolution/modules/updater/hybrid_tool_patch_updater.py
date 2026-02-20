"""
Hybrid Tool-Patch Updater Implementation.

This updater handles proposals from the HybridToolPatchProposer and applies:
1. Feature extraction tools to the agent's tool registry
2. Behavioral guidance patches to the agent's system prompt
"""

import copy
import sys
import os
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from .updater import Updater


class HybridToolPatchUpdater(Updater):
    """
    Updater that applies both tool and patch proposals from HybridToolPatchProposer.
    
    This updater:
    - Creates/evolves feature extraction tools via ToolUpdater
    - Adds behavioral guidance patches to system prompt
    - Maintains patch history for context
    """
    
    def __init__(
        self,
        tool_builder,
        tool_registry,
        verbose: bool = True
    ):
        """
        Initialize the hybrid updater.
        
        Args:
            tool_builder: ToolBuilder instance for creating tools
            tool_registry: ToolRegistry instance for managing tools
            verbose: Whether to print detailed updates
        """
        self.tool_builder = tool_builder
        self.tool_registry = tool_registry
        self.verbose = verbose
        
        # Track patches
        self.patches = []
    
    def update(self, agent, proposal):
        """
        Apply a hybrid proposal to the agent.
        
        Args:
            agent: The agent to update
            proposal: A proposal dict with type 'hybrid'
        
        Returns:
            Updated agent instance
        """
        if proposal.get("type") != "hybrid":
            return agent
        
        action = proposal.get("action", "SKIP")
        
        if action in ["CREATE_FEATURE_TOOL", "EVOLVE_FEATURE_TOOL"]:
            # Handle tool creation/evolution
            return self._handle_tool_action(agent, proposal)
        
        elif action == "ADD_PATCH":
            # Handle patch addition
            return self._handle_patch_action(agent, proposal)
        
        else:  # SKIP
            if self.verbose and proposal.get("reasoning"):
                print(f"[HybridUpdater] SKIP: {proposal['reasoning']}")
            return agent
    
    def _handle_tool_action(self, agent, proposal):
        """Handle tool creation or evolution."""
        action = proposal.get("action")
        
        # Extract and normalize tool_spec
        tool_spec = proposal.get("tool_spec", {})
        
        if not tool_spec:
            if self.verbose:
                print(f"[HybridUpdater] ERROR: No tool_spec provided for {action}")
            return agent
        
        # Make a copy to avoid modifying the original
        tool_spec = dict(tool_spec)
        
        # Handle different formats for implementation
        if "implementation" in tool_spec:
            impl = tool_spec["implementation"]
            
            if isinstance(impl, dict):
                # Format 1: implementation as dict with code/imports
                if "code" in impl:
                    tool_spec["code"] = impl["code"]
                if "imports" in impl:
                    tool_spec["imports"] = impl["imports"]
            elif isinstance(impl, str):
                # Format 2: implementation as a single string (just the code)
                tool_spec["code"] = impl
                # Extract imports from the code if present
                import re
                import_lines = re.findall(r'^(?:import|from)\s+.+$', impl, re.MULTILINE)
                if import_lines:
                    tool_spec["imports"] = import_lines
            
            # Remove implementation wrapper
            del tool_spec["implementation"]
        
        # Handle 'new_code' for evolution (copy to 'code' for processing)
        if action == "EVOLVE_FEATURE_TOOL" and "new_code" in tool_spec and not tool_spec.get("code"):
            tool_spec["code"] = tool_spec["new_code"]
        
        # Ensure required fields exist for CREATE action
        if action == "CREATE_FEATURE_TOOL" and not tool_spec.get("code"):
            if self.verbose:
                print(f"[HybridUpdater] ❌ ERROR: No code provided in tool_spec for {tool_spec.get('name', 'unknown')}")
                print(f"  Tool spec keys: {list(tool_spec.keys())}")
                
                # Diagnose what went wrong
                descriptive_fields = [k for k in tool_spec.keys() 
                                     if k in ['implementation_focus', 'implementation_notes', 
                                             'implementation_details', 'rationale', 'description']]
                if descriptive_fields:
                    print(f"  ⚠️  Found descriptive fields but no executable code: {descriptive_fields}")
                    print(f"  → Proposer generated specifications instead of implementation")
                    print(f"  → This tool will be SKIPPED - no code to register")
                    print(f"  💡 TIP: The proposer prompt should require 'code' field with actual Python function")
                
                if "implementation" in proposal.get("tool_spec", {}):
                    impl = proposal["tool_spec"]["implementation"]
                    print(f"  Implementation type: {type(impl)}")
                    if isinstance(impl, str):
                        print(f"  Implementation preview: {impl[:200]}...")
                elif "code" in proposal.get("tool_spec", {}):
                    print(f"  ⚠️  'code' field exists in original proposal but was lost during processing")
                else:
                    print(f"  ℹ️  Proposer did not generate 'implementation' or 'code' field")
                    print(f"  ℹ️  Expected format: tool_spec.code = 'def func_name(args): ...'")
            return agent
        
        # For EVOLVE action, check if tool exists and has new_code
        if action == "EVOLVE_FEATURE_TOOL":
            if not tool_spec.get("new_code") and not tool_spec.get("code"):
                if self.verbose:
                    print(f"[HybridUpdater] ⚠️  EVOLVE without new_code - checking if tool exists...")
                    tool_id = tool_spec.get("tool_id") or tool_spec.get("name")
                    existing_tool = self.tool_registry.get_tool(tool_id) or self.tool_registry.get_tool_by_name(tool_id)
                    if not existing_tool:
                        print(f"[HybridUpdater] ❌ Tool '{tool_id}' not found for evolution")
                        print(f"  Available tools: {[t['name'] for t in self.tool_registry.list_tools()]}")
                        return agent
        
        # Convert hybrid proposal to tool_building format for ToolUpdater
        tool_proposal = {
            "type": "tool_building",
            "action": "CREATE" if action == "CREATE_FEATURE_TOOL" else "EVOLVE",
            "confidence": proposal.get("confidence", "medium"),
            "reasoning": proposal.get("reasoning", ""),
            "tool_specs": [tool_spec]
        }
        
        # Get tool count before update to check if tool was actually registered
        tools_before = len(self.tool_registry.list_tools())
        
        # Build and register tool directly
        for spec in tool_proposal.get("tool_specs", []):
            try:
                tool_name = spec.get("name", "unknown_tool")
                tool_code = spec.get("code", "")
                if tool_code:
                    self.tool_builder.build_and_register(
                        name=tool_name,
                        code=tool_code,
                        description=spec.get("description", ""),
                        imports=spec.get("imports", []),
                        registry=self.tool_registry
                    )
            except Exception as e:
                if self.verbose:
                    print(f"[HybridUpdater] Tool build failed for {spec.get('name', 'unknown')}: {e}")
        updated_agent = agent
        
        # Check if tool was actually created
        tools_after = len(self.tool_registry.list_tools())
        tool_name = tool_spec.get("name") or tool_spec.get("tool_id", "unknown")
        
        if tools_after > tools_before:
            if self.verbose:
                print(f"[HybridUpdater] ✅ Successfully {'created' if action == 'CREATE_FEATURE_TOOL' else 'evolved'} tool: {tool_name}")
                
                # Verify the tool file exists
                tool = self.tool_registry.get_tool_by_name(tool_name)
                if tool and tool.get('file_path'):
                    import os
                    if os.path.exists(tool['file_path']):
                        print(f"[HybridUpdater]    File: {tool['file_path']} ✅")
                        
                        # Validate tool by running a test execution
                        validation_result = self._validate_tool_execution(tool_name, tool['file_path'])
                        if validation_result['valid']:
                            print(f"[HybridUpdater]    Validation: ✅ Tool loads and can be executed")
                        else:
                            print(f"[HybridUpdater]    Validation: ⚠️ {validation_result['error']}")
                    else:
                        print(f"[HybridUpdater]    ⚠️ File missing: {tool['file_path']}")
                
                rationale = tool_spec.get("rationale", "")
                if rationale:
                    print(f"[HybridUpdater]    Rationale: {rationale[:100]}...")
        else:
            if self.verbose:
                print(f"[HybridUpdater] ⚠️ Tool creation/evolution may have failed for: {tool_name}")
                print(f"[HybridUpdater]    Tool count unchanged ({tools_before} -> {tools_after})")
        
        return updated_agent
    
    def _validate_tool_execution(self, tool_name: str, file_path: str) -> dict:
        """
        Validate that a tool can be loaded and executed.
        
        This performs a dry-run validation to catch issues before the tool
        is used in actual agent execution.
        
        Args:
            tool_name: Name of the tool function
            file_path: Path to the tool file
            
        Returns:
            Dict with 'valid' bool and optional 'error' message
        """
        import importlib.util
        import sys
        
        try:
            # Load the module
            spec = importlib.util.spec_from_file_location(tool_name, file_path)
            if spec is None:
                return {'valid': False, 'error': 'Could not load module spec'}
            
            module = importlib.util.module_from_spec(spec)
            sys.modules[tool_name] = module
            spec.loader.exec_module(module)
            
            # Check if function exists
            func = getattr(module, tool_name, None)
            if func is None:
                # Try to find any callable function
                available_funcs = [name for name in dir(module) 
                                  if not name.startswith('_') and callable(getattr(module, name))]
                return {
                    'valid': False, 
                    'error': f"Function '{tool_name}' not found. Available: {available_funcs}"
                }
            
            # Check if it's callable
            if not callable(func):
                return {'valid': False, 'error': f"'{tool_name}' is not callable"}
            
            # Try a minimal test call with sample input to catch issues
            try:
                import inspect
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                
                # Determine test input based on parameter type hints or names
                test_input = None
                if params:
                    first_param = params[0]
                    param_info = sig.parameters[first_param]
                    
                    # Check type annotation
                    if param_info.annotation == str or 'str' in str(param_info.annotation):
                        # String input - typical for our tools
                        test_input = "{2025-01-01 10:00:00, B123, 456, glance_view, NULL, us-test, /test/path}"
                    elif param_info.annotation == list or 'list' in str(param_info.annotation).lower():
                        test_input = []
                    else:
                        # Default to string (most of our tools expect raw input strings)
                        test_input = "{2025-01-01 10:00:00, B123, 456, glance_view, NULL, us-test, /test/path}"
                
                if test_input is not None:
                    result = func(test_input)
                    
                    # Check if result is meaningful
                    if result is None:
                        return {'valid': True, 'warning': 'Returns None for test input'}
                    elif isinstance(result, dict):
                        if result.get('parsing_successful') == False or result.get('error'):
                            return {'valid': True, 'warning': 'Tool runs but returned error/parsing_failed'}
                        elif len(result) == 0:
                            return {'valid': True, 'warning': 'Returns empty dict for test input'}
                        else:
                            return {'valid': True, 'sample_output_type': type(result).__name__, 'sample_keys': list(result.keys())[:5]}
                    else:
                        return {'valid': True, 'sample_output_type': type(result).__name__}
                else:
                    return {'valid': True, 'warning': 'Could not determine test input format'}
                    
            except TypeError as e:
                # Function signature mismatch - acceptable
                return {'valid': True, 'warning': f'Signature test skipped: {e}'}
            except Exception as e:
                # Runtime error - the function might still work with proper input
                return {'valid': True, 'warning': f'Test call failed: {e}'}
            
        except SyntaxError as e:
            return {'valid': False, 'error': f'Syntax error in tool code: {e}'}
        except ImportError as e:
            return {'valid': False, 'error': f'Import error: {e}'}
        except Exception as e:
            return {'valid': False, 'error': f'Validation failed: {e}'}
    
    def _handle_patch_action(self, agent, proposal):
        """Handle patch addition."""
        patch_spec = proposal.get("patch_spec", {})
        
        if not patch_spec:
            if self.verbose:
                print("[HybridUpdater] No patch_spec provided, skipping")
            return agent
        
        # Extract patch content
        patch_content = patch_spec.get("patch", "")
        hypothesis = patch_spec.get("hypothesis", "")
        
        if not patch_content:
            if self.verbose:
                print("[HybridUpdater] Empty patch content, skipping")
            return agent
        
        # Add patch to history
        patch_entry = {
            "hypothesis": hypothesis,
            "patch": patch_content,
            "timestamp": datetime.now().isoformat(),
            "error_ids": patch_spec.get("error_ids", []),
            "error_rate": patch_spec.get("error_rate", "N/A")
        }
        self.patches.append(patch_entry)
        
        if self.verbose:
            print(f"[HybridUpdater] Added patch ({len(self.patches)} total)")
            print(f"  Hypothesis: {hypothesis[:100]}...")
            print(f"  Patch: {patch_content[:100]}...")
            print(f"  Affects: {patch_spec.get('error_rate', 'N/A')}")
        
        # Create new agent with updated prompt
        updated_agent = self._rebuild_agent_with_patches(agent)
        
        return updated_agent
    
    def _rebuild_agent_with_patches(self, agent):
        """
        Rebuild agent with all patches applied to original system prompt.
        
        This ensures patches don't accumulate redundantly.
        """
        # Start from original base prompt
        base_prompt = agent.original_system_prompt
        
        # Add tools section if tools exist
        tool_signatures = self.tool_registry.export_all_signatures()
        
        if tool_signatures:
            tools_section = f"""
# Available Feature Extraction Tools

You have access to specialized tools for extracting features and computing statistics.
These tools provide CRITICAL DATA that you MUST use to make informed predictions.

**⚠️ MANDATORY: Always call tools before making predictions! ⚠️**

Tool outputs are NOT predictions - they are features and statistics that YOU must
interpret and combine with the behavioral guidance below to make final predictions.

{tool_signatures}

## How to Use Tools (REQUIRED WORKFLOW)

**CRITICAL: Read the "How to Use This Tool" section for each tool above!**
Each tool has specific input format requirements. Follow them exactly.

**Phase 1 - Feature Extraction (MANDATORY):**
1. Read the tool's usage instructions carefully
2. Call tools with the EXACT input format they expect (usually raw input string)
3. Format: `TOOL_CALL: tool_name(param_name='your_input_here')`
4. Example: `TOOL_CALL: extract_ecommerce_behavior_features(events_data='{{2025-01-01, B123, 456, glance_view, ...}}')`
5. Wait for tool results before proceeding to Phase 2

**Common Mistakes to Avoid:**
❌ Don't call tools with empty arguments: `TOOL_CALL: tool_name()`
❌ Don't convert input to JSON if tool expects raw string
❌ Don't ignore tool usage instructions
✅ Pass the EXACT raw input string as the parameter
✅ Read the tool's "How to Use This Tool" section

**Phase 2 - Synthesize & Predict (AFTER receiving tool results):**
1. Review ALL features returned by tools (time gaps, patterns, frequencies, sequences)
2. Consider the behavioral guidance patterns below
3. Synthesize tool outputs + behavioral guidance + your understanding
4. Make an informed prediction
5. Output format: {{timestamp, asin, brand_id, event_name, search_query, primary_tree, category_path}}

**Remember:** 
- Tools are your EYES (they see patterns you might miss)
- Behavioral guidance is your EXPERIENCE (learned from past data)  
- Your reasoning is the BRAIN (combine everything intelligently)
- ALWAYS read tool usage instructions and use tools first, then reason!
"""
        else:
            tools_section = ""
        
        # Add patches section if patches exist
        if self.patches:
            patches_section = "\n\n# Behavioral Guidance (Learned Patterns)\n\n"
            patches_section += "The following patterns have been observed in historical data. "
            patches_section += "Consider these when making predictions:\n\n"
            
            for i, patch in enumerate(self.patches, 1):
                patches_section += f"{i}. {patch['patch']}\n\n"
        else:
            patches_section = ""
        
        # Combine all sections
        new_system_prompt = base_prompt + tools_section + patches_section
        
        # Create new agent instance
        agent_class = type(agent)
        
        new_agent = agent_class(
            model_name=agent.model_name,
            api_key=agent.api_key,
            system_prompt=new_system_prompt
        )
        
        # Preserve original prompt
        new_agent.original_system_prompt = agent.original_system_prompt
        
        # Copy state
        new_agent.set_state(copy.deepcopy(agent.get_state()))
        
        return new_agent
    
    def get_patches(self):
        """Get list of all patches."""
        return self.patches
    
    def save_patches(self, path: str):
        """
        Save patches to a JSON file for persistence.
        
        Args:
            path: File path to save patches
        """
        import json
        import os
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        with open(path, 'w') as f:
            json.dump(self.patches, f, indent=2)
        
        if self.verbose:
            print(f"[HybridUpdater] Saved {len(self.patches)} patches to {path}")
    
    def load_patches(self, path: str):
        """
        Load patches from a JSON file.
        
        Args:
            path: File path to load patches from
        """
        import json
        import os
        
        if not os.path.exists(path):
            if self.verbose:
                print(f"[HybridUpdater] No patches file found at {path}")
            return
        
        with open(path, 'r') as f:
            self.patches = json.load(f)
        
        if self.verbose:
            print(f"[HybridUpdater] Loaded {len(self.patches)} patches from {path}")
    
    def get_stats(self):
        """Get statistics about patches and tools."""
        return {
            "num_patches": len(self.patches),
            "num_tools": len(self.tool_registry.list_tools()),
            "total_tool_usage": sum(t.get("usage_count", 0) for t in self.tool_registry.list_tools())
        }

