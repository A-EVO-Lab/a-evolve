"""
Evolution State Module - Persistent State for Meta-Evolvers.

This module implements the persistent state St = {Tt, Mt, Kt} from the
"Agentic Evolution" framework:

- Tt: Registry of executable tools (code, APIs, scripts)
- Mt: Library of skills (SKILL.md format - procedures, checklists, policies)  
- Kt: Knowledge store (facts, exemplars, evidence)

Skills follow Claude's Agent Skills format:
- YAML frontmatter: name, description
- Markdown instructions: step-by-step guidance
- Optional: scripts, templates, examples

Reference: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field, asdict


@dataclass
class Skill:
    """
    A reusable skill following Claude's SKILL.md format.
    
    Skills are filesystem-based capabilities that provide:
    - Metadata (always loaded): name, description for discovery
    - Instructions (loaded when triggered): step-by-step procedures  
    - Resources (loaded as needed): scripts, templates, examples
    
    Example SKILL.md:
    ---
    name: temporal-analysis
    description: Analyze time patterns in event sequences to predict timing.
    ---
    # Temporal Analysis
    ## When to Use
    When predicting timestamps or time-sensitive events.
    ## Instructions
    1. Extract timestamps from event sequence
    2. Calculate inter-event gaps
    ...
    """
    # YAML frontmatter (Level 1: Metadata)
    id: str
    name: str  # max 64 chars, lowercase with hyphens
    description: str  # max 1024 chars - when to use this skill
    
    # Markdown instructions (Level 2: Instructions)
    instructions: str = ""  # Step-by-step guidance
    examples: str = ""  # Usage examples
    
    # Resources (Level 3: Scripts/templates)
    scripts: Dict[str, str] = field(default_factory=dict)  # name -> code
    templates: Dict[str, str] = field(default_factory=dict)  # name -> template
    
    # Conditions for triggering
    trigger_conditions: List[str] = field(default_factory=list)
    
    # Usage tracking
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: Optional[str] = None
    success_count: int = 0
    failure_count: int = 0
    usage_count: int = 0
    source_traces: List[str] = field(default_factory=list)  # Provenance
    
    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0
    
    def to_skill_md(self) -> str:
        """Export as SKILL.md format."""
        parts = []
        # YAML frontmatter
        parts.append("---")
        parts.append(f"name: {self.name}")
        parts.append(f"description: {self.description}")
        if self.trigger_conditions:
            parts.append(f"triggers: {', '.join(self.trigger_conditions)}")
        parts.append("---")
        parts.append("")
        
        # Main instructions
        parts.append(f"# {self.name.replace('-', ' ').title()}")
        parts.append("")
        if self.instructions:
            parts.append("## Instructions")
            parts.append(self.instructions)
            parts.append("")
        
        if self.examples:
            parts.append("## Examples")
            parts.append(self.examples)
            parts.append("")
        
        return "\n".join(parts)
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Skill':
        return cls(**data)
    
    @classmethod
    def from_skill_md(cls, content: str, skill_id: str) -> 'Skill':
        """Parse a SKILL.md file into a Skill object."""
        import re
        
        # Extract YAML frontmatter
        yaml_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if not yaml_match:
            return cls(id=skill_id, name=skill_id, description="", instructions=content)
        
        frontmatter = yaml_match.group(1)
        body = content[yaml_match.end():].strip()
        
        # Parse frontmatter
        name = skill_id
        description = ""
        triggers = []
        
        for line in frontmatter.split('\n'):
            if line.startswith('name:'):
                name = line.split(':', 1)[1].strip()
            elif line.startswith('description:'):
                description = line.split(':', 1)[1].strip()
            elif line.startswith('triggers:'):
                triggers = [t.strip() for t in line.split(':', 1)[1].split(',')]
        
        return cls(
            id=skill_id,
            name=name,
            description=description,
            instructions=body,
            trigger_conditions=triggers
        )


@dataclass
class KnowledgeEntry:
    """A knowledge unit with provenance (Kt component)."""
    id: str
    content: str
    entry_type: str  # 'fact', 'pattern', 'exemplar', 'rule'
    source_trace_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    confidence: float = 1.0
    access_count: int = 0
    last_accessed: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'KnowledgeEntry':
        return cls(**data)


class SkillLibrary:
    """Library of reusable skills and procedures (Mt)."""
    
    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir
        self.skills_file = os.path.join(workspace_dir, "skills.json")
        self.skills: Dict[str, Skill] = {}
        self._load()
    
    def _load(self):
        """Load skills from disk."""
        if os.path.exists(self.skills_file):
            with open(self.skills_file, 'r') as f:
                data = json.load(f)
                self.skills = {
                    k: Skill.from_dict(v) for k, v in data.get('skills', {}).items()
                }
    
    def save(self):
        """Save skills to disk."""
        os.makedirs(self.workspace_dir, exist_ok=True)
        with open(self.skills_file, 'w') as f:
            json.dump({
                'version': '1.0',
                'skills': {k: v.to_dict() for k, v in self.skills.items()}
            }, f, indent=2)
    
    def add_skill(self, skill: Skill) -> str:
        """Add a new skill to the library."""
        self.skills[skill.id] = skill
        self.save()
        return skill.id
    
    def get_skill(self, skill_id: str) -> Optional[Skill]:
        """Get a skill by ID."""
        return self.skills.get(skill_id)
    
    def update_skill(self, skill_id: str, updates: Dict) -> bool:
        """Update an existing skill."""
        if skill_id not in self.skills:
            return False
        skill = self.skills[skill_id]
        for key, value in updates.items():
            if hasattr(skill, key):
                setattr(skill, key, value)
        skill.updated_at = datetime.now().isoformat()
        self.save()
        return True
    
    def record_usage(self, skill_id: str, success: bool):
        """Record skill usage for tracking."""
        if skill_id in self.skills:
            skill = self.skills[skill_id]
            skill.usage_count += 1
            if success:
                skill.success_count += 1
            else:
                skill.failure_count += 1
            self.save()
    
    def list_skills(self) -> List[Dict]:
        """List all skills."""
        return [s.to_dict() for s in self.skills.values()]
    
    def search_skills(self, query: str, conditions: Optional[List[str]] = None) -> List[Skill]:
        """Search skills by query or conditions."""
        results = []
        query_lower = query.lower()
        for skill in self.skills.values():
            if (query_lower in skill.name.lower() or 
                query_lower in skill.description.lower() or
                query_lower in skill.procedure.lower()):
                results.append(skill)
            elif conditions:
                if any(c in skill.conditions for c in conditions):
                    results.append(skill)
        return results


class KnowledgeStore:
    """Knowledge store with provenance tracking (Kt)."""
    
    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir
        self.knowledge_file = os.path.join(workspace_dir, "knowledge.json")
        self.entries: Dict[str, KnowledgeEntry] = {}
        self._load()
    
    def _load(self):
        """Load knowledge from disk."""
        if os.path.exists(self.knowledge_file):
            with open(self.knowledge_file, 'r') as f:
                data = json.load(f)
                self.entries = {
                    k: KnowledgeEntry.from_dict(v) 
                    for k, v in data.get('entries', {}).items()
                }
    
    def save(self):
        """Save knowledge to disk."""
        os.makedirs(self.workspace_dir, exist_ok=True)
        with open(self.knowledge_file, 'w') as f:
            json.dump({
                'version': '1.0',
                'entries': {k: v.to_dict() for k, v in self.entries.items()}
            }, f, indent=2)
    
    def add_entry(self, entry: KnowledgeEntry) -> str:
        """Add a new knowledge entry."""
        self.entries[entry.id] = entry
        self.save()
        return entry.id
    
    def get_entry(self, entry_id: str) -> Optional[KnowledgeEntry]:
        """Get an entry by ID."""
        entry = self.entries.get(entry_id)
        if entry:
            entry.access_count += 1
            entry.last_accessed = datetime.now().isoformat()
        return entry
    
    def search(self, query: str, entry_type: Optional[str] = None, 
               tags: Optional[List[str]] = None) -> List[KnowledgeEntry]:
        """Search knowledge entries."""
        results = []
        query_lower = query.lower()
        for entry in self.entries.values():
            if entry_type and entry.entry_type != entry_type:
                continue
            if tags and not any(t in entry.tags for t in tags):
                continue
            if query_lower in entry.content.lower():
                results.append(entry)
        return results
    
    def prune_low_confidence(self, threshold: float = 0.3):
        """Remove entries with low confidence."""
        to_remove = [
            eid for eid, e in self.entries.items() 
            if e.confidence < threshold
        ]
        for eid in to_remove:
            del self.entries[eid]
        if to_remove:
            self.save()
        return len(to_remove)


@dataclass
class StateSnapshot:
    """Snapshot of evolution state for rollback."""
    version: int
    timestamp: str
    tools_count: int
    skills_count: int
    knowledge_count: int
    patches_count: int
    description: str = ""
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'StateSnapshot':
        return cls(**data)


class EvolutionState:
    """
    Unified evolution state St = {Tt, Mt, Kt}.
    
    Wraps:
    - Tt: Tool registry (from existing ToolWorkspace)
    - Mt: Skill library (new)
    - Kt: Knowledge store (new)
    
    Plus:
    - Version history for rollback
    - Evolution metrics tracking
    """
    
    def __init__(self, workspace_dir: str, tool_registry=None, tool_builder=None):
        """
        Initialize evolution state.
        
        Args:
            workspace_dir: Directory for persistent state
            tool_registry: Existing ToolRegistry (Tt)
            tool_builder: Existing ToolBuilder
        """
        self.workspace_dir = workspace_dir
        self.state_file = os.path.join(workspace_dir, "evolution_state.json")
        
        # Tt: Tool registry (from existing workspace)
        self.tool_registry = tool_registry
        self.tool_builder = tool_builder
        
        # Mt: Skill library
        self.skills = SkillLibrary(workspace_dir)
        
        # Kt: Knowledge store
        self.knowledge = KnowledgeStore(workspace_dir)
        
        # Version history
        self.version = 0
        self.history: List[StateSnapshot] = []
        
        # Evolution metrics
        self.metrics = {
            'initial_performance': None,
            'performance_history': [],  # List of (episode, performance)
            'ec_history': [],  # Evolution capacity over time
            'aulc_history': [],  # Area under learning curve
        }
        
        self._load()
    
    def _load(self):
        """Load state from disk."""
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                data = json.load(f)
                self.version = data.get('version', 0)
                self.history = [
                    StateSnapshot.from_dict(s) 
                    for s in data.get('history', [])
                ]
                self.metrics = data.get('metrics', self.metrics)
    
    def save(self):
        """Save state to disk."""
        os.makedirs(self.workspace_dir, exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump({
                'version': self.version,
                'history': [s.to_dict() for s in self.history],
                'metrics': self.metrics,
                'last_saved': datetime.now().isoformat()
            }, f, indent=2)
    
    def snapshot(self, description: str = "") -> StateSnapshot:
        """Create a snapshot of current state."""
        self.version += 1
        
        tools_count = len(self.tool_registry.list_tools()) if self.tool_registry else 0
        patches_count = 0
        patches_file = os.path.join(self.workspace_dir, "patches.json")
        if os.path.exists(patches_file):
            with open(patches_file, 'r') as f:
                patches_data = json.load(f)
                patches_count = len(patches_data) if isinstance(patches_data, list) else 0
        
        snapshot = StateSnapshot(
            version=self.version,
            timestamp=datetime.now().isoformat(),
            tools_count=tools_count,
            skills_count=len(self.skills.skills),
            knowledge_count=len(self.knowledge.entries),
            patches_count=patches_count,
            description=description
        )
        
        self.history.append(snapshot)
        
        # Also save tool files to version_snapshots/
        snapshots_dir = os.path.join(self.workspace_dir, "version_snapshots", f"v{self.version}")
        os.makedirs(snapshots_dir, exist_ok=True)
        
        # Copy current registry
        if self.tool_registry:
            registry_src = os.path.join(self.workspace_dir, "registry.json")
            if os.path.exists(registry_src):
                import shutil
                shutil.copy(registry_src, os.path.join(snapshots_dir, "registry.json"))
        
        self.save()
        return snapshot
    
    def rollback(self, version: int) -> bool:
        """Rollback to a previous version."""
        # Find the snapshot
        target = None
        for s in self.history:
            if s.version == version:
                target = s
                break
        
        if not target:
            return False
        
        # Restore from version_snapshots/
        snapshots_dir = os.path.join(self.workspace_dir, "version_snapshots", f"v{version}")
        if not os.path.exists(snapshots_dir):
            return False
        
        # Restore registry
        registry_backup = os.path.join(snapshots_dir, "registry.json")
        if os.path.exists(registry_backup):
            import shutil
            shutil.copy(registry_backup, os.path.join(self.workspace_dir, "registry.json"))
            if self.tool_registry:
                self.tool_registry.reload()
        
        return True
    
    def record_performance(self, episode: int, performance: float):
        """Record performance for evolution metrics."""
        self.metrics['performance_history'].append((episode, performance))
        
        if self.metrics['initial_performance'] is None:
            self.metrics['initial_performance'] = performance
        
        # Calculate EC (Evolution Capacity)
        ec = performance - self.metrics['initial_performance']
        self.metrics['ec_history'].append((episode, ec))
        
        # Calculate AULC (running average)
        performances = [p for _, p in self.metrics['performance_history']]
        aulc = sum(performances) / len(performances)
        self.metrics['aulc_history'].append((episode, aulc))
        
        self.save()
    
    def get_evolution_capacity(self) -> float:
        """Get current evolution capacity EC(N)."""
        if not self.metrics['ec_history']:
            return 0.0
        return self.metrics['ec_history'][-1][1]
    
    def get_aulc(self) -> float:
        """Get current AULC(N)."""
        if not self.metrics['aulc_history']:
            return 0.0
        return self.metrics['aulc_history'][-1][1]
    
    def get_summary(self) -> Dict:
        """Get summary of current state."""
        tools_count = len(self.tool_registry.list_tools()) if self.tool_registry else 0
        
        return {
            'version': self.version,
            'tools_count': tools_count,
            'skills_count': len(self.skills.skills),
            'knowledge_count': len(self.knowledge.entries),
            'evolution_capacity': self.get_evolution_capacity(),
            'aulc': self.get_aulc(),
            'num_snapshots': len(self.history)
        }
    
    def get_context_for_solver(self) -> str:
        """Generate context string for solver agent."""
        context_parts = []
        
        # Add skills summary
        if self.skills.skills:
            context_parts.append("# Available Skills\n")
            for skill in list(self.skills.skills.values())[:5]:
                context_parts.append(f"- **{skill.name}**: {skill.description[:100]}...")
            context_parts.append("")
        
        # Add knowledge summary
        patterns = self.knowledge.search("", entry_type="pattern")
        if patterns:
            context_parts.append("# Learned Patterns\n")
            for pattern in patterns[:5]:
                context_parts.append(f"- {pattern.content[:100]}...")
            context_parts.append("")
        
        return "\n".join(context_parts)
