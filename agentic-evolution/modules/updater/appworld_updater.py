"""
AppWorld Updater - Apply proposals and persist state.

Handles:
- Tool creation/evolution
- Skill creation/evolution  
- Knowledge creation/evolution
- State persistence to disk
- Agent prompt injection
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path

# Action constants (originally from appworld_proposer)
ACTION_ADD_TOOL = "ADD_TOOL"
ACTION_EVOLVE_TOOL = "EVOLVE_TOOL"
ACTION_ADD_SKILL = "ADD_SKILL"
ACTION_EVOLVE_SKILL = "EVOLVE_SKILL"
ACTION_ADD_KNOWLEDGE = "ADD_KNOWLEDGE"
ACTION_EVOLVE_KNOWLEDGE = "EVOLVE_KNOWLEDGE"
ACTION_SKIP = "SKIP"


class AppWorldUpdater:
    """
    Updater for AppWorld that applies proposals to state.
    
    Manages the St = {Tt, Mt, Kt} state:
    - Tools (Tt): Python files
    - Skills (Mt): SKILL.md files
    - Knowledge (Kt): JSON entries
    """
    
    def __init__(
        self,
        workspace_dir: str,
        verbose: bool = True
    ):
        """
        Initialize AppWorld updater.
        
        Args:
            workspace_dir: Directory for state persistence
            verbose: Enable verbose output
        """
        self.workspace_dir = workspace_dir
        self.verbose = verbose
        
        # Create state directories
        self.tools_dir = Path(workspace_dir) / "tools"
        self.skills_dir = Path(workspace_dir) / "skills"
        self.knowledge_file = Path(workspace_dir) / "knowledge.json"
        
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        
        # Load existing state
        self._load_state()
        
        # Update history
        self.update_history: List[Dict] = []
    
    def _load_state(self):
        """Load existing state from disk."""
        # Load knowledge
        if self.knowledge_file.exists():
            with open(self.knowledge_file, 'r') as f:
                self.knowledge = json.load(f)
        else:
            self.knowledge = []
        
        # Load tools registry
        self.tools_registry_file = Path(self.workspace_dir) / "tools_registry.json"
        if self.tools_registry_file.exists():
            with open(self.tools_registry_file, 'r') as f:
                self.tools_registry = json.load(f)
        else:
            self.tools_registry = {}
        
        # Load skills registry
        self.skills_registry_file = Path(self.workspace_dir) / "skills_registry.json"
        if self.skills_registry_file.exists():
            with open(self.skills_registry_file, 'r') as f:
                self.skills_registry = json.load(f)
        else:
            self.skills_registry = {}
    
    def apply(self, proposal: Dict, proposer=None) -> bool:
        """
        Apply a proposal to update state.
        
        Args:
            proposal: Proposal dict from proposer
            proposer: Optional proposer to update in-memory state
            
        Returns:
            True if successfully applied
        """
        action = proposal.get('action', ACTION_SKIP)
        
        if action == ACTION_SKIP:
            if self.verbose:
                print("  ⏭️ Skipping - no action needed")
            return False
        
        try:
            if action == ACTION_ADD_TOOL:
                return self._apply_add_tool(proposal, proposer)
            elif action == ACTION_EVOLVE_TOOL:
                return self._apply_evolve_tool(proposal, proposer)
            elif action == ACTION_ADD_SKILL:
                return self._apply_add_skill(proposal, proposer)
            elif action == ACTION_EVOLVE_SKILL:
                return self._apply_evolve_skill(proposal, proposer)
            elif action == ACTION_ADD_KNOWLEDGE:
                return self._apply_add_knowledge(proposal, proposer)
            elif action == ACTION_EVOLVE_KNOWLEDGE:
                return self._apply_evolve_knowledge(proposal, proposer)
            else:
                if self.verbose:
                    print(f"  ⚠️ Unknown action: {action}")
                return False
                
        except Exception as e:
            if self.verbose:
                print(f"  ❌ Apply error: {e}")
            return False
    
    def _apply_add_tool(self, proposal: Dict, proposer=None) -> bool:
        """Add a new tool."""
        spec = proposal.get('tool_spec', {})
        name = spec.get('name', f"tool_{len(self.tools_registry)}")
        code = spec.get('code', '')
        description = spec.get('description', '')
        
        if not code:
            if self.verbose:
                print("  ⚠️ No tool code provided")
            return False
        
        # Create tool file
        tool_id = f"tool_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        tool_file = self.tools_dir / f"{name}.py"
        
        with open(tool_file, 'w') as f:
            f.write(f'"""\n{description}\n"""\n\n')
            f.write(code)
        
        # Update registry
        self.tools_registry[tool_id] = {
            'name': name,
            'description': description,
            'file': str(tool_file),
            'created_at': datetime.now().isoformat()
        }
        self._save_tools_registry()
        
        # Update proposer state if provided
        if proposer:
            proposer.add_tool(name, spec)
        
        if self.verbose:
            print(f"  ✓ Added tool: {name}")
        
        self._log_update(ACTION_ADD_TOOL, tool_id)
        return True
    
    def _apply_evolve_tool(self, proposal: Dict, proposer=None) -> bool:
        """Evolve an existing tool."""
        spec = proposal.get('tool_spec', {})
        target_id = spec.get('target_id', '')
        
        if target_id not in self.tools_registry:
            if self.verbose:
                print(f"  ⚠️ Tool not found: {target_id}")
            return False
        
        tool = self.tools_registry[target_id]
        tool['updated_at'] = datetime.now().isoformat()
        tool['evolution_notes'] = spec.get('changes', '')
        
        self._save_tools_registry()
        
        if self.verbose:
            print(f"  ✓ Evolved tool: {tool.get('name')}")
        
        self._log_update(ACTION_EVOLVE_TOOL, target_id)
        return True
    
    def _apply_add_skill(self, proposal: Dict, proposer=None) -> bool:
        """Add a new skill."""
        spec = proposal.get('skill_spec', {})
        name = spec.get('name', f"skill_{len(self.skills_registry)}")
        description = spec.get('description', '')
        instructions = spec.get('instructions', '')
        
        # Create SKILL.md file
        skill_id = f"skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        skill_file = self.skills_dir / f"{name}.md"
        
        skill_content = f"""---
name: {name}
description: {description}
---

# {name.replace('-', ' ').title()}

## Instructions

{instructions}
"""
        
        with open(skill_file, 'w') as f:
            f.write(skill_content)
        
        # Update registry
        self.skills_registry[skill_id] = {
            'name': name,
            'description': description,
            'file': str(skill_file),
            'created_at': datetime.now().isoformat()
        }
        self._save_skills_registry()
        
        # Update proposer state if provided
        if proposer:
            proposer.add_skill(spec)
        
        if self.verbose:
            print(f"  ✓ Added skill: {name}")
        
        self._log_update(ACTION_ADD_SKILL, skill_id)
        return True
    
    def _apply_evolve_skill(self, proposal: Dict, proposer=None) -> bool:
        """Evolve an existing skill."""
        spec = proposal.get('skill_spec', {})
        target_id = spec.get('target_id', '')
        
        if target_id not in self.skills_registry:
            if self.verbose:
                print(f"  ⚠️ Skill not found: {target_id}")
            return False
        
        skill = self.skills_registry[target_id]
        skill['updated_at'] = datetime.now().isoformat()
        skill['evolution_notes'] = spec.get('changes', '')
        
        self._save_skills_registry()
        
        if self.verbose:
            print(f"  ✓ Evolved skill: {skill.get('name')}")
        
        self._log_update(ACTION_EVOLVE_SKILL, target_id)
        return True
    
    def _apply_add_knowledge(self, proposal: Dict, proposer=None) -> bool:
        """Add new knowledge."""
        spec = proposal.get('knowledge_spec', {})
        
        knowledge_id = f"k_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        entry = {
            'id': knowledge_id,
            'type': spec.get('type', 'fact'),
            'when_to_use': spec.get('when_to_use', 'General'),
            'content': spec.get('content', ''),
            'created_at': datetime.now().isoformat()
        }
        
        self.knowledge.append(entry)
        self._save_knowledge()
        
        # Update proposer state if provided
        if proposer:
            proposer.add_knowledge(spec)
        
        if self.verbose:
            print(f"  ✓ Added knowledge: {entry['content'][:50]}...")
        
        self._log_update(ACTION_ADD_KNOWLEDGE, knowledge_id)
        return True
    
    def _apply_evolve_knowledge(self, proposal: Dict, proposer=None) -> bool:
        """Evolve existing knowledge."""
        spec = proposal.get('knowledge_spec', {})
        target_id = spec.get('target_id', '')
        
        for entry in self.knowledge:
            if entry.get('id') == target_id:
                entry['updated_at'] = datetime.now().isoformat()
                entry['evolution_notes'] = spec.get('changes', '')
                self._save_knowledge()
                
                if self.verbose:
                    print(f"  ✓ Evolved knowledge: {target_id}")
                
                self._log_update(ACTION_EVOLVE_KNOWLEDGE, target_id)
                return True
        
        if self.verbose:
            print(f"  ⚠️ Knowledge not found: {target_id}")
        return False
    
    def _save_tools_registry(self):
        """Save tools registry to disk."""
        with open(self.tools_registry_file, 'w') as f:
            json.dump(self.tools_registry, f, indent=2)
    
    def _save_skills_registry(self):
        """Save skills registry to disk."""
        with open(self.skills_registry_file, 'w') as f:
            json.dump(self.skills_registry, f, indent=2)
    
    def _save_knowledge(self):
        """Save knowledge to disk."""
        with open(self.knowledge_file, 'w') as f:
            json.dump(self.knowledge, f, indent=2)
    
    def _log_update(self, action: str, target_id: str):
        """Log an update to history."""
        self.update_history.append({
            'action': action,
            'target_id': target_id,
            'timestamp': datetime.now().isoformat()
        })
    
    def get_state(self) -> Dict:
        """Get current state for prompt injection."""
        return {
            'tools': self.tools_registry,
            'skills': self.skills_registry,
            'knowledge': self.knowledge
        }
    
    def get_statistics(self) -> Dict:
        """Get state statistics."""
        return {
            'num_tools': len(self.tools_registry),
            'num_skills': len(self.skills_registry),
            'num_knowledge': len(self.knowledge),
            'num_updates': len(self.update_history)
        }
