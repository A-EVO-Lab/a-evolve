"""
Apply Module - Stage 3 of the Proposer Agent.

Applies the to-do items from the plan with version control.
All changes are revertable until verified and committed.
"""

import os
import json
import copy
import shutil
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, field, asdict

from .plan import TodoItem, TodoStatus


@dataclass
class VersionSnapshot:
    """A snapshot of the agent state for version control."""
    version_id: str
    timestamp: str
    description: str
    todo_id: Optional[str] = None
    
    # State snapshots
    system_prompt: str = ""
    tool_files: Dict[str, str] = field(default_factory=dict)  # filepath -> content
    registry_data: Dict[str, Any] = field(default_factory=dict)
    patches: List[Dict] = field(default_factory=list)
    
    # Tracking
    is_committed: bool = False
    is_reverted: bool = False
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'VersionSnapshot':
        return cls(**d)


class ApplyModule:
    """
    Stage 3: Apply Changes with Version Control.
    
    This module:
    1. Creates snapshots before applying changes
    2. Applies to-do items one by one
    3. Supports revert if verification fails
    4. Commits changes after successful verification
    """
    
    def __init__(
        self,
        tool_builder,
        tool_registry,
        workspace_dir: str,
        verbose: bool = True
    ):
        """
        Initialize the apply module.
        
        Args:
            tool_builder: ToolBuilder for creating/updating tools
            tool_registry: ToolRegistry for managing tools
            workspace_dir: Directory for storing version snapshots
            verbose: Whether to print debug output
        """
        self.tool_builder = tool_builder
        self.tool_registry = tool_registry
        self.workspace_dir = workspace_dir
        self.verbose = verbose
        
        # Version control
        self.snapshots: List[VersionSnapshot] = []
        self.current_version_id: Optional[str] = None
        
        # Staging area (changes before commit)
        self.staged_changes: List[Dict] = []
        self.staged_agent: Optional[Any] = None
        
        # Paths
        self.snapshots_dir = os.path.join(workspace_dir, "version_snapshots")
        os.makedirs(self.snapshots_dir, exist_ok=True)
    
    def run(
        self,
        agent,
        todos: List[TodoItem],
        patches: List[Dict],
        context: Dict[str, Any]
    ) -> Tuple[Any, List[Dict]]:
        """
        Run Stage 3: Apply all to-do items.
        
        Args:
            agent: The agent to update
            todos: List of TodoItems to apply
            patches: Current list of patches
            context: Shared context
            
        Returns:
            Tuple of (updated_agent, applied_changes)
        """
        if self.verbose:
            print("\n[Apply] 🛠️ Stage 3: Applying Changes...")
        
        # Create snapshot before any changes
        self._create_snapshot(agent, patches, "before_apply")
        
        applied_changes = []
        updated_agent = agent
        updated_patches = list(patches)  # Copy
        
        for todo in todos:
            if todo.status != TodoStatus.PENDING:
                continue
            
            # Mark as in progress
            todo.status = TodoStatus.IN_PROGRESS
            
            try:
                if self.verbose:
                    print(f"[Apply]   Applying: {todo.action} - {todo.description[:50]}...")
                
                # Apply based on action type
                if todo.action in ["CREATE_TOOL", "CREATE_FEATURE_TOOL"]:
                    result = self._apply_create_tool(updated_agent, todo, context)
                    updated_agent = result["agent"]
                    applied_changes.append(result)
                    
                elif todo.action in ["EVOLVE_TOOL", "EVOLVE_FEATURE_TOOL"]:
                    result = self._apply_evolve_tool(updated_agent, todo, context)
                    updated_agent = result["agent"]
                    applied_changes.append(result)
                    
                elif todo.action == "ADD_SKILL":
                    result = self._apply_add_skill(updated_agent, todo, context)
                    updated_agent = result["agent"]
                    applied_changes.append(result)
                    
                elif todo.action == "EVOLVE_SKILL":
                    result = self._apply_evolve_skill(updated_agent, todo, context)
                    updated_agent = result["agent"]
                    applied_changes.append(result)
                    
                elif todo.action == "ADD_KNOWLEDGE":
                    result = self._apply_add_knowledge(updated_agent, todo, context)
                    updated_agent = result["agent"]
                    applied_changes.append(result)
                    
                elif todo.action == "EVOLVE_KNOWLEDGE":
                    result = self._apply_evolve_knowledge(updated_agent, todo, context)
                    updated_agent = result["agent"]
                    applied_changes.append(result)
                
                elif todo.action == "INDUCE_WORKFLOW":
                    result = self._apply_induce_workflow(updated_agent, todo, context)
                    updated_agent = result["agent"]
                    applied_changes.append(result)
                
                else:
                    if self.verbose:
                        print(f"[Apply]     Skipping unknown action: {todo.action}")
                    todo.status = TodoStatus.SKIPPED
                    continue
                
                # Mark as completed
                todo.status = TodoStatus.COMPLETED
                todo.completed_at = datetime.now().isoformat()
                
                if self.verbose:
                    print(f"[Apply]     ✅ Applied successfully")
                
            except Exception as e:
                if self.verbose:
                    print(f"[Apply]     ❌ Failed: {str(e)}")
                todo.status = TodoStatus.FAILED
                todo.error_message = str(e)
                applied_changes.append({
                    "todo_id": todo.id,
                    "action": todo.action,
                    "success": False,
                    "error": str(e)
                })
        
        # Store staged changes for potential revert
        self.staged_changes = applied_changes
        self.staged_agent = updated_agent
        
        # Update patches in context
        context["patches"] = updated_patches
        
        return updated_agent, applied_changes
    
    def _apply_create_tool(
        self,
        agent,
        todo: TodoItem,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Apply a CREATE_FEATURE_TOOL action."""
        spec = todo.spec
        
        if not spec.get("name"):
            raise ValueError("Tool spec missing 'name'")
        if not spec.get("code"):
            raise ValueError("Tool spec missing 'code'")
        
        # Normalize code escapes
        code = spec.get("code", "")
        if "\\n" in code and "\n" not in code:
            code = code.replace("\\n", "\n").replace("\\t", "\t")
        
        # Build the tool
        tool_info = self.tool_builder.build_tool(
            name=spec["name"],
            description=spec.get("description", "Generated tool"),
            code=code,
            parameters=spec.get("parameters", {}),
            return_type=spec.get("return_type", "dict"),
            imports=spec.get("imports", []),
            usage_prompt=spec.get("usage_prompt")
        )
        
        # Register the tool
        tool_id = self.tool_registry.register_tool(tool_info)
        
        # Auto-generate SKILL.md for tool usage (like Claude Code's skill system)
        skill_file = self._generate_tool_skill_doc(
            tool_name=spec["name"],
            description=spec.get("description", ""),
            usage_prompt=spec.get("usage_prompt", ""),
            parameters=spec.get("parameters", {}),
            code_example=spec.get("code_example", "")
        )
        
        # Update agent's system prompt with tool signatures
        updated_agent = self._update_agent_prompt(agent, context)
        
        return {
            "todo_id": todo.id,
            "action": "CREATE_FEATURE_TOOL",
            "success": True,
            "tool_id": tool_id,
            "tool_name": spec["name"],
            "file_path": tool_info.get("file_path"),
            "skill_file": skill_file,
            "agent": updated_agent
        }
    
    def _apply_evolve_tool(
        self,
        agent,
        todo: TodoItem,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Apply an EVOLVE_FEATURE_TOOL action."""
        spec = todo.spec
        
        tool_id = spec.get("tool_id") or spec.get("name")
        if not tool_id:
            raise ValueError("Tool spec missing 'tool_id' or 'name'")
        
        new_code = spec.get("new_code") or spec.get("code")
        if not new_code:
            raise ValueError("Tool spec missing 'new_code'")
        
        # Normalize code escapes
        if "\\n" in new_code and "\n" not in new_code:
            new_code = new_code.replace("\\n", "\n").replace("\\t", "\t")
        
        # Find the tool
        tool = self.tool_registry.get_tool(tool_id)
        if not tool:
            tool = self.tool_registry.get_tool_by_name(tool_id)
        
        if not tool:
            raise ValueError(f"Tool '{tool_id}' not found for evolution")
        
        # Update the tool
        updated_info = self.tool_builder.update_tool(
            name=tool["name"],
            new_code=new_code,
            new_description=spec.get("new_description"),
            new_parameters=spec.get("new_parameters"),
            new_usage_prompt=spec.get("new_usage_prompt"),
            new_imports=spec.get("imports") or spec.get("new_imports")
        )
        
        # Update registry
        self.tool_registry.update_tool(tool["id"], updated_info)
        
        # Re-generate SKILL.md for evolved tool
        skill_file = self._generate_tool_skill_doc(
            tool_name=tool["name"],
            description=spec.get("new_description") or tool.get("description", ""),
            usage_prompt=spec.get("new_usage_prompt") or tool.get("usage_prompt", ""),
            parameters=spec.get("new_parameters") or tool.get("parameters", {}),
            code_example=spec.get("code_example", "")
        )
        
        # Update agent's system prompt
        updated_agent = self._update_agent_prompt(agent, context)
        
        return {
            "todo_id": todo.id,
            "action": "EVOLVE_FEATURE_TOOL",
            "success": True,
            "tool_id": tool["id"],
            "tool_name": tool["name"],
            "file_path": updated_info.get("file_path"),
            "skill_file": skill_file,
            "agent": updated_agent
        }
    
    def _generate_tool_skill_doc(
        self,
        tool_name: str,
        description: str,
        usage_prompt: str = "",
        parameters: Dict[str, Any] = None,
        code_example: str = ""
    ) -> str:
        """
        Generate a SKILL.md document for a tool.
        
        This creates a skill document that teaches the solver how to use the tool,
        following the Claude Code SKILL.md format with YAML frontmatter.
        
        Args:
            tool_name: Name of the tool
            description: Tool description
            usage_prompt: How to use the tool
            parameters: Tool parameters dict
            code_example: Example code showing tool usage
            
        Returns:
            Path to the created skill file
        """
        # Sanitize name for filename
        safe_name = tool_name.replace(" ", "_").replace("/", "_").lower()
        
        # Build parameters documentation
        params_doc = ""
        if parameters:
            params_doc = "## Parameters\n\n"
            for param_name, param_info in parameters.items():
                if isinstance(param_info, dict):
                    param_type = param_info.get("type", "any")
                    param_desc = param_info.get("description", "")
                    required = param_info.get("required", False)
                    req_label = " (required)" if required else " (optional)"
                    params_doc += f"- **{param_name}** (`{param_type}`){req_label}: {param_desc}\n"
                else:
                    params_doc += f"- **{param_name}**: {param_info}\n"
            params_doc += "\n"
        
        # Build example section
        example_doc = ""
        if code_example:
            example_doc = f"""## Example Usage

```python
{code_example}
```

"""
        elif usage_prompt:
            example_doc = f"""## How to Use

{usage_prompt}

"""
        
        # Create SKILL.md content with YAML frontmatter (Claude Code format)
        skill_content = f"""---
name: {safe_name}_usage
description: How to use the {tool_name} tool. Use when you need to {description.lower()[:100] if description else 'use this tool'}.
---

# {tool_name} Tool Usage

**Purpose:** {description}

**When to use:** When your task requires {description.lower()[:150] if description else 'specialized processing'}.

{params_doc}{example_doc}## Best Practices

1. **Verify input format** - Ensure your input matches the expected parameter types
2. **Check return values** - Handle the tool's output appropriately
3. **Error handling** - Catch any errors from the tool execution

---
*Auto-generated skill for tool: {tool_name}*
*Created: {datetime.now().isoformat()}*
"""
        
        # Create skills directory and file
        skills_dir = os.path.join(self.workspace_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)
        
        skill_file = os.path.join(skills_dir, f"{safe_name}_usage.md")
        
        try:
            with open(skill_file, "w") as f:
                f.write(skill_content)
            
            if self.verbose:
                print(f"[Apply]     📚 Generated tool skill doc: {skill_file}")
                
        except Exception as e:
            if self.verbose:
                print(f"[Apply]     ⚠️ Failed to generate tool skill doc: {e}")
            return None
        
        return skill_file
    
    # NOTE: _apply_patch removed - patches are now handled as skills with scope="tactical_patch"
    # Use ADD_SKILL with scope="tactical_patch" instead of ADD_PATCH
    
    def _apply_add_skill(
        self,
        agent,
        todo: TodoItem,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Apply an ADD_SKILL action.
        
        Skills are stored as individual markdown files in skills/ directory.
        Each skill is a separate file: skills/{skill_name}.md
        
        Skills can have different scopes:
        - strategic_skill: Long-term, reusable skills
        - tactical_patch: Short-term, narrow behavioral patches
        """
        spec = todo.spec
        
        skill_name = spec.get("name")
        if not skill_name:
            raise ValueError("Skill spec missing 'name'")
        
        description = spec.get("description", "")
        instructions = spec.get("instructions", "")
        triggers = spec.get("triggers", [])
        
        # New scope-related fields (for patch-like skills)
        scope = spec.get("scope", "strategic_skill")  # or "tactical_patch"
        ttl_episodes = spec.get("ttl_episodes")  # Optional expiration
        priority = spec.get("priority", 0)  # Urgency level
        
        # Support patch-like specs (backward compatibility)
        if not instructions and spec.get("patch"):
            instructions = spec.get("patch")
            scope = "tactical_patch"
        if spec.get("hypothesis") and not description:
            description = spec.get("hypothesis")
            scope = "tactical_patch"
        if spec.get("error_ids") and not triggers:
            triggers = spec.get("error_ids")
        
        # Sanitize skill name for filename
        safe_name = skill_name.replace(" ", "_").replace("/", "_").lower()
        
        # Format as markdown file
        triggers_str = ", ".join(triggers) if triggers else "N/A"
        scope_label = "🎯 Tactical Patch" if scope == "tactical_patch" else "📚 Strategic Skill"
        
        skill_content = f"""# {skill_name}

**Scope:** {scope_label}

**Description:** {description}

**When to use:** {triggers_str}

## Instructions

{instructions}

---
*Created: {datetime.now().isoformat()}*
*Version: 1*
*Scope: {scope}*
"""
        if ttl_episodes:
            skill_content += f"*TTL: {ttl_episodes} episodes*\n"
        if priority:
            skill_content += f"*Priority: {priority}*\n"
        
        # Create skills directory and skill file
        skills_dir = os.path.join(self.workspace_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)
        
        skill_file = os.path.join(skills_dir, f"{safe_name}.md")
        
        try:
            with open(skill_file, "w") as f:
                f.write(skill_content)
            
            if self.verbose:
                print(f"[Apply]     💾 Created {scope} skill: {skill_file}")
                
        except Exception as e:
            if self.verbose:
                print(f"[Apply]     ⚠️ Failed to save skill: {e}")
            raise
        
        # Update context for prompt injection
        skills = context.get("skills", {})
        skills[skill_name] = {
            "name": skill_name,
            "description": description,
            "instructions": instructions,
            "triggers": triggers,
            "scope": scope,
            "ttl_episodes": ttl_episodes,
            "priority": priority,
            "timestamp": datetime.now().isoformat(),
            "version": 1,
            "file_path": skill_file
        }
        context["skills"] = skills
        
        # Update agent's system prompt with skills
        updated_agent = self._update_agent_prompt(agent, context)
        
        return {
            "todo_id": todo.id,
            "action": "ADD_SKILL",
            "success": True,
            "skill_name": skill_name,
            "scope": scope,
            "file_path": skill_file,
            "agent": updated_agent
        }
    
    def _apply_evolve_skill(
        self,
        agent,
        todo: TodoItem,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Apply an EVOLVE_SKILL action.
        
        Updates an existing skill file in the skills/ directory.
        """
        spec = todo.spec
        
        skill_name = spec.get("skill_name") or spec.get("name")
        if not skill_name:
            raise ValueError("Skill spec missing 'skill_name' or 'name'")
        
        # Sanitize skill name for filename
        safe_name = skill_name.replace(" ", "_").replace("/", "_").lower()
        
        skills_dir = os.path.join(self.workspace_dir, "skills")
        skill_file = os.path.join(skills_dir, f"{safe_name}.md")
        
        # Check if skill file exists
        if not os.path.exists(skill_file):
            # Try to find by listing directory
            if os.path.exists(skills_dir):
                available = [f[:-3] for f in os.listdir(skills_dir) if f.endswith('.md')]
                raise ValueError(f"Skill '{skill_name}' not found. Available: {available}")
            else:
                raise ValueError(f"Skills directory not found: {skills_dir}")
        
        # Read existing file to get version
        with open(skill_file, "r") as f:
            old_content = f.read()
        
        # Extract version from old content
        import re
        version_match = re.search(r'\*Version: (\d+)\*', old_content)
        old_version = int(version_match.group(1)) if version_match else 1
        
        # Build new content
        new_description = spec.get("new_description") or spec.get("description", "")
        new_instructions = spec.get("new_instructions") or spec.get("instructions", "")
        new_triggers = spec.get("new_triggers") or spec.get("triggers", [])
        triggers_str = ", ".join(new_triggers) if new_triggers else "N/A"
        
        new_content = f"""# {skill_name}

**Description:** {new_description}

**When to use:** {triggers_str}

## Instructions

{new_instructions}

---
*Updated: {datetime.now().isoformat()}*
*Version: {old_version + 1}*
"""
        
        # Write updated skill file
        try:
            with open(skill_file, "w") as f:
                f.write(new_content)
            
            if self.verbose:
                print(f"[Apply]     💾 Updated skill '{skill_name}' (v{old_version} → v{old_version + 1})")
                
        except Exception as e:
            if self.verbose:
                print(f"[Apply]     ⚠️ Failed to update skill: {e}")
            raise
        
        # Update context
        skills = context.get("skills", {})
        skills[skill_name] = {
            "name": skill_name,
            "description": new_description,
            "instructions": new_instructions,
            "triggers": new_triggers,
            "timestamp": datetime.now().isoformat(),
            "version": old_version + 1,
            "file_path": skill_file
        }
        context["skills"] = skills
        
        updated_agent = self._update_agent_prompt(agent, context)
        
        return {
            "todo_id": todo.id,
            "action": "EVOLVE_SKILL",
            "success": True,
            "skill_name": skill_name,
            "version": old_version + 1,
            "file_path": skill_file,
            "agent": updated_agent
        }
    
    def _apply_add_knowledge(
        self,
        agent,
        todo: TodoItem,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Apply an ADD_KNOWLEDGE action."""
        spec = todo.spec
        
        topic = spec.get("topic")
        if not topic:
            raise ValueError("Knowledge spec missing 'topic'")
        
        # Create knowledge entry
        knowledge_entry = {
            "id": f"knowledge_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "type": spec.get("type", "general"),
            "topic": topic,
            "content": spec.get("content", ""),
            "examples": spec.get("examples", []),
            "timestamp": datetime.now().isoformat(),
            "version": 1
        }
        
        # Get or create knowledge list in context
        knowledge = context.get("knowledge", [])
        knowledge.append(knowledge_entry)
        context["knowledge"] = knowledge
        
        # Persist knowledge to workspace
        knowledge_file = os.path.join(self.workspace_dir, "knowledge.json")
        try:
            os.makedirs(os.path.dirname(knowledge_file), exist_ok=True)
            with open(knowledge_file, "w") as f:
                json.dump(knowledge, f, indent=2)
            if self.verbose:
                print(f"[Apply]     💾 Saved knowledge '{topic}' to {knowledge_file}")
        except Exception as e:
            if self.verbose:
                print(f"[Apply]     ⚠️ Failed to save knowledge: {e}")
        
        # Update agent's system prompt with knowledge
        updated_agent = self._update_agent_prompt(agent, context)
        
        return {
            "todo_id": todo.id,
            "action": "ADD_KNOWLEDGE",
            "success": True,
            "knowledge_id": knowledge_entry["id"],
            "topic": topic,
            "agent": updated_agent
        }
    
    def _apply_evolve_knowledge(
        self,
        agent,
        todo: TodoItem,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Apply an EVOLVE_KNOWLEDGE action."""
        spec = todo.spec
        
        knowledge_id = spec.get("knowledge_id") or spec.get("topic")
        if not knowledge_id:
            raise ValueError("Knowledge spec missing 'knowledge_id' or 'topic'")
        
        knowledge = context.get("knowledge", [])
        
        # Find and update knowledge entry
        found = False
        for k in knowledge:
            if k.get("id") == knowledge_id or k.get("topic") == knowledge_id:
                k["content"] = spec.get("new_content", k.get("content", ""))
                k["examples"] = spec.get("new_examples", k.get("examples", []))
                k["timestamp"] = datetime.now().isoformat()
                k["version"] = k.get("version", 1) + 1
                found = True
                break
        
        if not found:
            raise ValueError(f"Knowledge '{knowledge_id}' not found for evolution")
        
        context["knowledge"] = knowledge
        
        # Persist
        knowledge_file = os.path.join(self.workspace_dir, "knowledge.json")
        try:
            with open(knowledge_file, "w") as f:
                json.dump(knowledge, f, indent=2)
            if self.verbose:
                print(f"[Apply]     💾 Updated knowledge '{knowledge_id}'")
        except Exception as e:
            if self.verbose:
                print(f"[Apply]     ⚠️ Failed to save knowledge: {e}")
        
        updated_agent = self._update_agent_prompt(agent, context)
        
        return {
            "todo_id": todo.id,
            "action": "EVOLVE_KNOWLEDGE",
            "success": True,
            "knowledge_id": knowledge_id,
            "agent": updated_agent
        }
    
    def _apply_induce_workflow(
        self,
        agent,
        todo: TodoItem,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Apply an INDUCE_WORKFLOW action (AWM pattern).
        
        Uses LLM to extract reusable workflows from successful task trajectories.
        Saves the induced workflow as a skill with scope=workflow_memory.
        """
        spec = todo.spec
        
        workflow_name = spec.get("name")
        if not workflow_name:
            raise ValueError("Workflow spec missing 'name'")
        
        description = spec.get("description", "Induced workflow from successful executions")
        triggers = spec.get("triggers", [])
        source_task_ids = spec.get("source_task_ids", [])
        
        if self.verbose:
            print(f"[Apply]     🔍 Inducing workflow '{workflow_name}' from {len(source_task_ids)} source tasks")
        
        # Get observations from context to find successful trajectories
        observations = context.get("observations", [])
        
        # Filter to matching source tasks with high scores
        successful_trajectories = []
        for obs in observations:
            task_id = obs.get("task_id", "")
            score = obs.get("score", 0)
            trajectory = obs.get("trajectory", obs.get("messages", []))
            
            # Include if in source list and successful, or if no source list and high score
            if (not source_task_ids or task_id in source_task_ids) and score >= 0.8:
                if trajectory:
                    successful_trajectories.append({
                        "task_id": task_id,
                        "score": score,
                        "trajectory": trajectory
                    })
        
        if self.verbose:
            print(f"[Apply]     📊 Found {len(successful_trajectories)} successful trajectories")
        
        # Build workflow content
        if successful_trajectories and hasattr(agent, 'call'):
            # Use LLM to induce workflow (AWM pattern)
            workflow_content = self._induce_workflow_via_llm(
                agent, successful_trajectories, workflow_name, description
            )
        else:
            # Fallback: create placeholder workflow
            workflow_content = self._create_placeholder_workflow(
                workflow_name, description, triggers
            )
        
        # Save as skill with scope=workflow_memory
        skill_file = self._save_workflow_as_skill(
            workflow_name=workflow_name,
            description=description,
            triggers=triggers,
            workflow_content=workflow_content
        )
        
        # Update skills in context
        skills = context.get("skills", {})
        skills[workflow_name] = {
            "name": workflow_name,
            "description": description,
            "instructions": workflow_content,
            "triggers": triggers,
            "scope": "workflow_memory"
        }
        context["skills"] = skills
        
        updated_agent = self._update_agent_prompt(agent, context)
        
        return {
            "todo_id": todo.id,
            "action": "INDUCE_WORKFLOW",
            "success": True,
            "workflow_name": workflow_name,
            "skill_file": skill_file,
            "num_source_trajectories": len(successful_trajectories),
            "agent": updated_agent
        }
    
    def _induce_workflow_via_llm(
        self,
        agent,
        trajectories: List[Dict],
        workflow_name: str,
        description: str
    ) -> str:
        """Use LLM to induce reusable workflow from trajectories (AWM pattern)."""
        
        # Format trajectories for LLM
        examples_text = []
        for traj in trajectories[:5]:  # Limit to 5 examples
            task_id = traj["task_id"]
            score = traj["score"]
            
            # Extract think/action pairs from trajectory
            trajectory_str = self._format_trajectory(traj["trajectory"])
            
            examples_text.append(f"""### Task: {task_id} (score: {score:.2f})
{trajectory_str}
""")
        
        prompt = f"""Given these successful task trajectories, extract a reusable workflow called "{workflow_name}".

Description: {description}

## Successful Trajectories

{"".join(examples_text)}

## Your Task

Extract the common pattern from these successful executions as a reusable workflow.

Guidelines:
- Replace task-specific values with descriptive variables like {{user_id}}, {{app_name}}, {{date}}
- Keep API method names and invariant elements unchanged
- Include <think> reasoning for when to use each step
- Include <action> code blocks with the actual API calls
- Focus on the reusable pattern, not task-specific details

Format your response as:

## Workflow: {workflow_name}

When to use: [describe when this workflow applies]

<think>
[reasoning for first step]
</think>
<action>
[code for first step]
</action>

<think>
[reasoning for next step]
</think>
<action>
[code for next step]
</action>

..."""

        try:
            response = agent.call(prompt)
            return response
        except Exception as e:
            if self.verbose:
                print(f"[Apply]     ⚠️ LLM induction failed: {e}")
            return self._create_placeholder_workflow(workflow_name, description, [])
    
    def _format_trajectory(self, trajectory: List[Dict]) -> str:
        """Format trajectory as think/action pairs."""
        import re
        parts = []
        
        for entry in trajectory:
            if entry.get("role") == "assistant":
                content = entry.get("content", "")
                if isinstance(content, list):
                    # Handle content blocks
                    content = " ".join(
                        block.get("text", "") for block in content 
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                
                # Extract think blocks
                think_match = re.search(r'<think>(.*?)</think>', content, re.DOTALL)
                if think_match:
                    parts.append(f"<think>\n{think_match.group(1).strip()}\n</think>")
                
                # Extract action/code blocks
                action_match = re.search(r'```python\n(.*?)```', content, re.DOTALL)
                if action_match:
                    parts.append(f"<action>\n{action_match.group(1).strip()}\n</action>")
        
        return "\n\n".join(parts[:10])  # Limit output
    
    def _create_placeholder_workflow(
        self,
        workflow_name: str,
        description: str,
        triggers: List[str]
    ) -> str:
        """Create placeholder workflow when LLM induction not available."""
        triggers_str = ', '.join(triggers) if triggers else 'when relevant patterns occur'
        
        return f"""## Workflow: {workflow_name}

When to use: {triggers_str}

{description}

<think>
Identify the pattern and prepare variables.
</think>
<action>
# Workflow pattern to be filled from actual execution
# Placeholder - will be refined as more examples are collected
pass
</action>
"""
    
    def _save_workflow_as_skill(
        self,
        workflow_name: str,
        description: str,
        triggers: List[str],
        workflow_content: str
    ) -> str:
        """Save induced workflow as SKILL.md file."""
        
        skills_dir = os.path.join(self.workspace_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)
        
        # Create YAML frontmatter + markdown content
        triggers_yaml = ", ".join(triggers) if triggers else ""
        
        skill_content = f'''---
name: {workflow_name}
description: {description[:150]}
scope: workflow_memory
triggers: {triggers_yaml}
---

{workflow_content}
'''
        
        # Save to file
        skill_file = os.path.join(skills_dir, f"{workflow_name}.md")
        with open(skill_file, "w") as f:
            f.write(skill_content)
        
        if self.verbose:
            print(f"[Apply]     💾 Saved workflow skill: {skill_file}")
        
        return skill_file
    
    def _update_agent_prompt(
        self,
        agent,
        context: Dict[str, Any],
        patches: Optional[List[Dict]] = None
    ):
        """Update agent's system prompt with tools and patches."""
        from agent.tool_enabled_claude_agent import ToolEnabledClaudeAgent
        
        # Get base prompt
        base_prompt = getattr(agent, 'original_system_prompt', agent.system_prompt)
        
        # Get tool signatures
        tool_signatures = self.tool_registry.export_all_signatures()
        
        # Build tools section
        tools_section = ""
        if tool_signatures:
            tools_section = f"""

# Available Feature Extraction Tools

You have access to specialized tools for extracting features.
**⚠️ CRITICAL: Read each tool's "How to Use" section carefully!**

{tool_signatures}

## How to Use Tools

1. Read the tool's usage instructions
2. Call tools with the EXACT input format expected
3. Format: `TOOL_CALL: tool_name(param_name='your_value')`
4. Use tool outputs to inform your prediction

**Common Mistakes:**
❌ Don't call tools with empty arguments
❌ Don't ignore tool usage instructions
✅ Pass input in the format the tool expects
"""
        
        # Build patches section
        patches_section = ""
        if patches is None:
            patches = context.get("patches", [])
        
        if patches:
            patches_section = "\n\n# Behavioral Guidance (Learned Patterns)\n\n"
            for i, p in enumerate(patches, 1):
                patch_text = p.get("patch", str(p)) if isinstance(p, dict) else str(p)
                patches_section += f"{i}. {patch_text}\n\n"
        
        # Build skills section
        skills_section = ""
        skills = context.get("skills", {})
        if skills:
            skills_section = "\n\n# Available Skills\n\n"
            for skill_name, skill_data in skills.items():
                description = skill_data.get("description", "")
                instructions = skill_data.get("instructions", "")
                triggers = skill_data.get("triggers", [])
                
                skills_section += f"## {skill_name}\n"
                skills_section += f"**Description:** {description}\n"
                if triggers:
                    skills_section += f"**When to use:** {', '.join(triggers)}\n"
                skills_section += f"\n{instructions}\n\n"
        
        # Build knowledge section
        knowledge_section = ""
        knowledge = context.get("knowledge", [])
        if knowledge:
            knowledge_section = "\n\n# Reference Knowledge\n\n"
            for k in knowledge:
                topic = k.get("topic", "")
                content = k.get("content", "")
                k_type = k.get("type", "general")
                
                knowledge_section += f"## {topic} ({k_type})\n"
                knowledge_section += f"{content}\n\n"
        
        # Combine
        new_prompt = base_prompt + tools_section + skills_section + knowledge_section + patches_section
        
        # Create new agent
        agent_class = type(agent)
        
        if isinstance(agent, ToolEnabledClaudeAgent):
            new_agent = agent_class(
                model_name=agent.model_name,
                api_key=getattr(agent, 'api_key', None),
                system_prompt=new_prompt,
                max_tokens=agent.max_tokens,
                temperature=agent.temperature,
                tool_executor=agent.tool_executor,
                max_tool_iterations=agent.max_tool_iterations
            )
        else:
            new_agent = agent_class(
                model_name=agent.model_name,
                api_key=getattr(agent, 'api_key', None),
                system_prompt=new_prompt
            )
        
        new_agent.original_system_prompt = base_prompt
        new_agent.set_state(copy.deepcopy(agent.get_state()))
        
        return new_agent
    
    def _create_snapshot(
        self,
        agent,
        patches: List[Dict],
        description: str,
        todo_id: Optional[str] = None
    ) -> VersionSnapshot:
        """Create a version snapshot."""
        version_id = f"v_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Capture tool files
        tool_files = {}
        for tool in self.tool_registry.list_tools():
            filepath = tool.get("file_path", "")
            if filepath and os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    tool_files[filepath] = f.read()
        
        snapshot = VersionSnapshot(
            version_id=version_id,
            timestamp=datetime.now().isoformat(),
            description=description,
            todo_id=todo_id,
            system_prompt=agent.system_prompt,
            tool_files=tool_files,
            registry_data={"tools": self.tool_registry.list_tools()},
            patches=list(patches)
        )
        
        self.snapshots.append(snapshot)
        self.current_version_id = version_id
        
        # Save snapshot to disk
        snapshot_path = os.path.join(self.snapshots_dir, f"{version_id}.json")
        with open(snapshot_path, 'w') as f:
            json.dump(snapshot.to_dict(), f, indent=2)
        
        if self.verbose:
            print(f"[Apply]   📸 Created snapshot: {version_id}")
        
        return snapshot
    
    def revert(self, agent, version_id: Optional[str] = None) -> Tuple[Any, List[Dict]]:
        """
        Revert to a previous version.
        
        Args:
            agent: Current agent
            version_id: Version to revert to (default: previous version)
            
        Returns:
            Tuple of (reverted_agent, reverted_patches)
        """
        if not self.snapshots:
            raise ValueError("No snapshots available for revert")
        
        # Find target snapshot
        target_snapshot = None
        if version_id:
            for s in self.snapshots:
                if s.version_id == version_id:
                    target_snapshot = s
                    break
        else:
            # Revert to most recent non-committed snapshot
            for s in reversed(self.snapshots):
                if not s.is_committed and not s.is_reverted:
                    target_snapshot = s
                    break
        
        if not target_snapshot:
            raise ValueError(f"Snapshot not found: {version_id}")
        
        if self.verbose:
            print(f"[Apply]   ⏪ Reverting to: {target_snapshot.version_id}")
        
        # Restore tool files
        for filepath, content in target_snapshot.tool_files.items():
            dir_path = os.path.dirname(filepath)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(filepath, 'w') as f:
                f.write(content)
        
        # Restore registry
        # (This is a simplified version - in production you'd need full registry restore)
        
        # Mark as reverted
        target_snapshot.is_reverted = True
        
        # Clear staged changes
        self.staged_changes = []
        self.staged_agent = None
        
        # Restore agent prompt
        agent_class = type(agent)
        reverted_agent = agent_class(
            model_name=agent.model_name,
            api_key=getattr(agent, 'api_key', None),
            system_prompt=target_snapshot.system_prompt
        )
        
        reverted_agent.original_system_prompt = getattr(agent, 'original_system_prompt', agent.system_prompt)
        
        return reverted_agent, target_snapshot.patches
    
    def commit(self) -> bool:
        """
        Commit staged changes (after successful verification).
        
        Returns:
            True if committed successfully
        """
        if not self.snapshots:
            return False
        
        # Mark latest snapshot as committed
        latest = self.snapshots[-1]
        latest.is_committed = True
        
        # Clear staged changes
        self.staged_changes = []
        
        if self.verbose:
            print(f"[Apply]   ✅ Committed: {latest.version_id}")
        
        # Save updated snapshot
        snapshot_path = os.path.join(self.snapshots_dir, f"{latest.version_id}.json")
        with open(snapshot_path, 'w') as f:
            json.dump(latest.to_dict(), f, indent=2)
        
        return True
    
    def get_staged_changes(self) -> List[Dict]:
        """Get list of staged changes."""
        return self.staged_changes
    
    def get_version_history(self) -> List[Dict]:
        """Get version history."""
        return [s.to_dict() for s in self.snapshots]
