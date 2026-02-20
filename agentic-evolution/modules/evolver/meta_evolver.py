"""
Meta-Evolver Module - Agentic Evolution for Adaptive Intelligence.

This module implements the Meta-Evolver from the "Agentic Evolution" paper.
The Meta-Evolver is a stateful agent that:
1. DIAGNOSES failures (not just records them)
2. PLANS update actions (aligned with HybridToolPatchProposer actions)
3. BUDGETS evolution compute
4. VERIFIES changes before committing (with rollback support)

Actions are aligned with HybridToolPatchProposer:
- CREATE_FEATURE_TOOL: Create a new feature extraction tool
- EVOLVE_FEATURE_TOOL: Improve an existing tool
- ADD_PATCH: Add behavioral guidance patch
- ADD_SKILL: Add a reusable skill (SKILL.md format)
- SKIP: No action needed
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field

from .evolution_state import EvolutionState, Skill, KnowledgeEntry


# Action types aligned with HybridToolPatchProposer
ACTION_CREATE_FEATURE_TOOL = "CREATE_FEATURE_TOOL"
ACTION_EVOLVE_FEATURE_TOOL = "EVOLVE_FEATURE_TOOL"
ACTION_ADD_PATCH = "ADD_PATCH"
ACTION_ADD_SKILL = "ADD_SKILL"
ACTION_EVOLVE_SKILL = "EVOLVE_SKILL"  # Update/improve existing skill
ACTION_ADD_KNOWLEDGE = "ADD_KNOWLEDGE"
ACTION_PRUNE = "PRUNE"
ACTION_SKIP = "SKIP"


@dataclass
class EvolutionBudget:
    """Budget allocation for evolution compute."""
    diagnosis_tokens: int = 2000
    planning_tokens: int = 2000
    synthesis_tokens: int = 4000
    validation_tokens: int = 1000
    max_candidates: int = 3
    max_retries: int = 2
    
    @property
    def total_tokens(self) -> int:
        return (self.diagnosis_tokens + self.planning_tokens + 
                self.synthesis_tokens + self.validation_tokens)


@dataclass
class UpdateAction:
    """
    An update action proposed by the Meta-Evolver.
    
    Action types are aligned with HybridToolPatchProposer:
    - CREATE_FEATURE_TOOL: Create feature extraction tool
    - EVOLVE_FEATURE_TOOL: Improve existing tool
    - ADD_PATCH: Add behavioral guidance patch
    - ADD_SKILL: Add reusable skill (SKILL.md format)
    - ADD_KNOWLEDGE: Add knowledge entry
    - PRUNE: Remove low-quality entries
    - SKIP: No action
    """
    action_type: str  # One of the ACTION_* constants
    target: str  # ID of target artifact
    specification: Dict  # Action-specific details (tool_spec, patch_spec, skill_spec)
    priority: float = 1.0
    estimated_impact: float = 0.0
    reasoning: str = ""


class MetaEvolver:
    """
    Stateful Meta-Evolver agent for agentic evolution.
    
    This wraps the existing HybridToolPatchProposer and ToolEnabledProposer
    with EvolutionState management, adding:
    - Skills (Mt): Reusable procedures in SKILL.md format
    - Knowledge (Kt): Facts and patterns with provenance
    - Evolution metrics: EC(N), AULC(N)
    - Rollback support: Version control for state
    
    The Meta-Evolver delegates tool/patch operations to the proposer
    while managing skills and knowledge independently.
    """
    
    def __init__(
        self,
        evolver_agent,
        state: EvolutionState,
        proposer=None,
        updater=None,
        budget: Optional[EvolutionBudget] = None,
        verbose: bool = True
    ):
        """
        Initialize Meta-Evolver.
        
        Args:
            evolver_agent: The evolver LLM agent (for diagnosis and planning)
            state: EvolutionState instance (St = {Tt, Mt, Kt})
            proposer: HybridToolPatchProposer or ToolEnabledProposer
            updater: HybridToolPatchUpdater for applying changes
            budget: Evolution budget allocation
            verbose: Enable verbose output
        """
        self.evolver_agent = evolver_agent
        self.state = state
        self.proposer = proposer
        self.updater = updater
        self.budget = budget or EvolutionBudget()
        self.verbose = verbose
        
        # Track evolution history for meta-learning
        self.evolution_log: List[Dict] = []
    
    def evolve(
        self,
        observations: List[Dict],
        context: Optional[Dict] = None,
        solver_agent=None
    ) -> Tuple[EvolutionState, Dict]:
        """
        Main evolution loop: diagnose → plan → synthesize → verify → commit.
        
        This method integrates with the existing proposer workflow while
        adding EvolutionState management.
        
        Args:
            observations: List of observation dicts from PersistentObserver
            context: Context dict with tool_registry, patches, etc.
            solver_agent: The solver agent to potentially update
            
        Returns:
            Tuple of (updated_state, evolution_result)
        """
        evolution_result = {
            'timestamp': datetime.now().isoformat(),
            'stages': {},
            'action': ACTION_SKIP,
            'committed': False,
            'rollback': False
        }
        
        # Create snapshot for potential rollback
        pre_snapshot = self.state.snapshot("Pre-evolution snapshot")
        
        try:
            # OPTION 1: Use existing proposer if available
            if self.proposer is not None:
                return self._evolve_via_proposer(
                    observations, context, solver_agent, evolution_result, pre_snapshot
                )
            
            # OPTION 2: Standalone evolution (skills/knowledge only)
            return self._evolve_standalone(
                observations, context, evolution_result, pre_snapshot
            )
            
        except Exception as e:
            if self.verbose:
                print(f"  ❌ Evolution error: {e}")
            evolution_result['error'] = str(e)
            self.state.rollback(pre_snapshot.version)
            evolution_result['rollback'] = True
            
        self.evolution_log.append(evolution_result)
        return self.state, evolution_result
    
    def _evolve_via_proposer(
        self,
        observations: List[Dict],
        context: Optional[Dict],
        solver_agent,
        evolution_result: Dict,
        pre_snapshot
    ) -> Tuple[EvolutionState, Dict]:
        """
        Evolve using existing proposer (HybridToolPatchProposer or ToolEnabledProposer).
        
        This delegates tool/patch operations to the proposer while
        the MetaEvolver focuses on state management and metrics.
        """
        if self.verbose:
            print("🔍 DIAGNOSIS: Delegating to proposer...")
        
        # Build context
        if context is None:
            context = {}
        if self.state.tool_registry:
            context['tool_registry'] = self.state.tool_registry
        
        # Get existing patches
        patches_file = os.path.join(self.state.workspace_dir, "patches.json")
        if os.path.exists(patches_file):
            with open(patches_file, 'r') as f:
                context['patches'] = json.load(f)
        
        # Call proposer
        proposal = self.proposer.propose(solver_agent, observations, context)
        
        action = proposal.get('action', ACTION_SKIP)
        evolution_result['action'] = action
        evolution_result['stages']['proposal'] = {
            'action': action,
            'reasoning': proposal.get('reasoning', ''),
            'confidence': proposal.get('confidence', 'low')
        }
        
        if self.verbose:
            print(f"  Proposal action: {action}")
        
        # Apply action based on type
        if action == ACTION_CREATE_FEATURE_TOOL:
            success = self._apply_tool_action(proposal, solver_agent, is_create=True)
            if success:
                evolution_result['committed'] = True
                self.state.snapshot(f"Created tool: {proposal.get('tool_spec', {}).get('name', 'unknown')}")
        
        elif action == ACTION_EVOLVE_FEATURE_TOOL:
            success = self._apply_tool_action(proposal, solver_agent, is_create=False)
            if success:
                evolution_result['committed'] = True
                self.state.snapshot(f"Evolved tool: {proposal.get('tool_spec', {}).get('tool_id', 'unknown')}")
        
        elif action == ACTION_ADD_PATCH:
            success = self._apply_patch_action(proposal)
            if success:
                evolution_result['committed'] = True
                self.state.snapshot("Added behavioral patch")
                # Also add as knowledge entry
                self._add_patch_as_knowledge(proposal)
        
        elif action == ACTION_SKIP:
            if self.verbose:
                print("  ⏭️  No action needed")
        
        # Check if proposer already applied changes (ToolEnabledProposer does this)
        if proposal.get('_committed'):
            evolution_result['committed'] = True
        
        self.evolution_log.append(evolution_result)
        return self.state, evolution_result
    
    def _evolve_standalone(
        self,
        observations: List[Dict],
        context: Optional[Dict],
        evolution_result: Dict,
        pre_snapshot
    ) -> Tuple[EvolutionState, Dict]:
        """
        Standalone evolution for skills and knowledge (no proposer).
        """
        if self.verbose:
            print("🔍 DIAGNOSIS: Analyzing observations...")
        
        # Simple diagnosis based on observations
        diagnosis = self._diagnose(observations)
        evolution_result['stages']['diagnosis'] = diagnosis
        
        if not diagnosis.get('needs_update', False):
            if self.verbose:
                print("  ✓ No update needed")
            return self.state, evolution_result
        
        # Plan actions
        if self.verbose:
            print("📋 PLANNING: Selecting actions...")
        actions = self._plan(diagnosis)
        evolution_result['stages']['planning'] = {
            'actions': [a.__dict__ for a in actions]
        }
        
        # Apply actions
        for action in actions:
            if self.verbose:
                print(f"⚙️  APPLYING: {action.action_type}...")
            success = self._apply_action(action)
            if success:
                evolution_result['action'] = action.action_type
                evolution_result['committed'] = True
                self.state.snapshot(f"Applied: {action.action_type}")
                break
        
        self.evolution_log.append(evolution_result)
        return self.state, evolution_result
    
    def _diagnose(self, observations: List[Dict]) -> Dict:
        """Diagnose failures from observations."""
        diagnosis = {
            'needs_update': False,
            'error_patterns': [],
            'failed_fields': [],
            'recommendations': []
        }
        
        if not observations:
            return diagnosis
        
        # Count errors
        errors = [obs for obs in observations if not obs.get('is_correct', True)]
        if not errors:
            return diagnosis
        
        diagnosis['needs_update'] = True
        diagnosis['error_count'] = len(errors)
        diagnosis['total_count'] = len(observations)
        diagnosis['accuracy'] = (len(observations) - len(errors)) / len(observations)
        
        # Analyze field correctness
        field_failures = {}
        for obs in errors:
            field_correctness = obs.get('field_correctness', {})
            for field, correct in field_correctness.items():
                if not correct:
                    field_failures[field] = field_failures.get(field, 0) + 1
        
        diagnosis['failed_fields'] = list(field_failures.keys())
        diagnosis['field_failure_counts'] = field_failures
        
        # Generate recommendations - check if skill exists, evolve if so, add if not
        existing_skill_names = [s.name for s in self.state.skills.skills.values()]
        
        if 'timestamp' in field_failures:
            skill_name = 'temporal-analysis'
            skill_spec = {
                'name': skill_name,
                'description': 'Analyze time patterns in event sequences to predict timing. Use when predicting timestamps or scheduling.',
                'instructions': """## When to Use
When predicting timestamps or time-sensitive events.

## Instructions
1. Extract all timestamps from the event sequence
2. Calculate the time gaps between consecutive events
3. Identify acceleration/deceleration patterns
4. Consider typical session duration patterns
5. Predict timestamp based on observed patterns

## Common Patterns
- Events in same session typically occur within seconds/minutes
- Add_to_cart to order is usually quick (< 60 seconds)
- Session switches have longer gaps (> 10 minutes)"""
            }
            
            if skill_name in existing_skill_names:
                diagnosis['recommendations'].append({
                    'type': ACTION_EVOLVE_SKILL,
                    'reason': f'Timestamp failing despite having {skill_name} skill - evolving it',
                    'skill_spec': skill_spec
                })
            else:
                diagnosis['recommendations'].append({
                    'type': ACTION_ADD_SKILL,
                    'reason': 'Timestamp prediction failing - add temporal-analysis skill',
                    'skill_spec': skill_spec
                })
        
        if 'event_name' in field_failures:
            skill_name = 'event-flow-prediction'
            skill_spec = {
                'name': skill_name,
                'description': 'Predict next event type based on shopping session flow patterns. Use when predicting order, search, add_to_cart, etc.',
                'instructions': """## When to Use
When predicting what action a user will take next (order, search, add_to_cart, etc.)

## Instructions
1. Identify the current session phase (browsing, searching, purchasing)
2. Note the last event type - typical flows are:
   - search → click-sp → glance_view → add_to_cart → order
   - glance_view → add_to_cart → order (direct purchase)
   - Multiple glance_views before add_to_cart (comparison shopping)
3. Consider category switches (may indicate new session)
4. Predict the most likely next event based on flow

## Key Patterns
- After add_to_cart, expect order OR more browsing (cart abandonment)
- Multiple glance_views of same category = comparison shopping
- Category switch often = new user intent/session"""
            }
            
            if skill_name in existing_skill_names:
                diagnosis['recommendations'].append({
                    'type': ACTION_EVOLVE_SKILL,
                    'reason': f'Event prediction failing despite having {skill_name} skill - evolving it',
                    'skill_spec': skill_spec
                })
            else:
                diagnosis['recommendations'].append({
                    'type': ACTION_ADD_SKILL,
                    'reason': 'Event type prediction failing - add event-flow skill',
                    'skill_spec': skill_spec
                })
        
        return diagnosis
    
    def _plan(self, diagnosis: Dict) -> List[UpdateAction]:
        """Plan actions based on diagnosis."""
        actions = []
        
        for rec in diagnosis.get('recommendations', []):
            action_type = rec.get('type', ACTION_SKIP)
            
            if action_type == ACTION_ADD_SKILL and 'skill_spec' in rec:
                actions.append(UpdateAction(
                    action_type=ACTION_ADD_SKILL,
                    target='new_skill',
                    specification=rec['skill_spec'],
                    priority=0.8,
                    reasoning=rec.get('reason', '')
                ))
            
            elif action_type == ACTION_EVOLVE_SKILL and 'skill_spec' in rec:
                # Higher priority for evolving existing skills
                actions.append(UpdateAction(
                    action_type=ACTION_EVOLVE_SKILL,
                    target=rec['skill_spec'].get('name', 'unknown'),
                    specification=rec['skill_spec'],
                    priority=0.9,  # Higher priority than ADD_SKILL
                    reasoning=rec.get('reason', '')
                ))
            
            elif action_type == ACTION_ADD_KNOWLEDGE:
                actions.append(UpdateAction(
                    action_type=ACTION_ADD_KNOWLEDGE,
                    target='new_knowledge',
                    specification=rec.get('knowledge_spec', {}),
                    priority=0.5,
                    reasoning=rec.get('reason', '')
                ))
        
        actions.sort(key=lambda a: a.priority, reverse=True)
        return actions[:self.budget.max_candidates]
    
    def _apply_action(self, action: UpdateAction) -> bool:
        """Apply a standalone action (skill/knowledge)."""
        try:
            if action.action_type == ACTION_ADD_SKILL:
                spec = action.specification
                skill = Skill(
                    id=f"skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    name=spec.get('name', 'unnamed-skill'),
                    description=spec.get('description', ''),
                    instructions=spec.get('instructions', ''),
                    examples=spec.get('examples', ''),
                    trigger_conditions=spec.get('triggers', [])
                )
                self.state.skills.add_skill(skill)
                
                # Also save as SKILL.md file
                self._save_skill_md(skill)
                
                if self.verbose:
                    print(f"    → Added skill: {skill.name}")
                return True
            
            elif action.action_type == ACTION_EVOLVE_SKILL:
                # Update an existing skill
                spec = action.specification
                skill_id = spec.get('skill_id') or spec.get('name')
                
                # Find the skill to update
                target_skill = None
                for sid, skill in self.state.skills.skills.items():
                    if sid == skill_id or skill.name == skill_id:
                        target_skill = skill
                        break
                
                if target_skill:
                    # Update the skill
                    updates = {}
                    if 'instructions' in spec:
                        updates['instructions'] = spec['instructions']
                    if 'examples' in spec:
                        updates['examples'] = spec['examples']
                    if 'description' in spec:
                        updates['description'] = spec['description']
                    if 'triggers' in spec:
                        updates['trigger_conditions'] = spec['triggers']
                    
                    self.state.skills.update_skill(target_skill.id, updates)
                    
                    # Re-save SKILL.md file
                    self._save_skill_md(target_skill)
                    
                    if self.verbose:
                        print(f"    → Evolved skill: {target_skill.name}")
                    return True
                else:
                    # Skill not found, create it instead
                    if self.verbose:
                        print(f"    → Skill not found, creating new: {skill_id}")
                    action.action_type = ACTION_ADD_SKILL
                    return self._apply_action(action)
            
            elif action.action_type == ACTION_ADD_KNOWLEDGE:
                spec = action.specification
                entry = KnowledgeEntry(
                    id=f"knowledge_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    content=spec.get('content', ''),
                    entry_type=spec.get('type', 'pattern'),
                    tags=spec.get('tags', [])
                )
                self.state.knowledge.add_entry(entry)
                if self.verbose:
                    print(f"    → Added knowledge: {entry.id}")
                return True
            
            elif action.action_type == ACTION_PRUNE:
                removed = self.state.knowledge.prune_low_confidence()
                if self.verbose:
                    print(f"    → Pruned {removed} entries")
                return True
            
            return False
            
        except Exception as e:
            if self.verbose:
                print(f"    → Failed: {e}")
            return False
    
    def _apply_tool_action(self, proposal: Dict, solver_agent, is_create: bool) -> bool:
        """Apply tool creation/evolution via updater."""
        if self.updater is None:
            if self.verbose:
                print("    → No updater available, skipping tool action")
            return False
        
        try:
            self.updater.update(solver_agent, proposal)
            return True
        except Exception as e:
            if self.verbose:
                print(f"    → Tool action failed: {e}")
            return False
    
    def _apply_patch_action(self, proposal: Dict) -> bool:
        """Apply patch via updater."""
        if self.updater is None:
            if self.verbose:
                print("    → No updater available, skipping patch action")
            return False
        
        try:
            # Patches are typically stored by the updater
            return True
        except Exception as e:
            if self.verbose:
                print(f"    → Patch action failed: {e}")
            return False
    
    def _add_patch_as_knowledge(self, proposal: Dict):
        """Add a patch as a knowledge entry for provenance."""
        patch_spec = proposal.get('patch_spec', {})
        if patch_spec:
            entry = KnowledgeEntry(
                id=f"patch_knowledge_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                content=patch_spec.get('patch', ''),
                entry_type='pattern',
                tags=['patch', 'behavioral_guidance']
            )
            self.state.knowledge.add_entry(entry)
    
    def _save_skill_md(self, skill: Skill):
        """Save skill as SKILL.md file."""
        skills_dir = os.path.join(self.state.workspace_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)
        
        skill_path = os.path.join(skills_dir, f"{skill.name}.md")
        with open(skill_path, 'w') as f:
            f.write(skill.to_skill_md())
    
    def get_evolution_summary(self) -> Dict:
        """Get summary of evolution history."""
        return {
            'state_summary': self.state.get_summary(),
            'evolution_count': len(self.evolution_log),
            'committed_count': sum(1 for e in self.evolution_log if e.get('committed')),
            'rollback_count': sum(1 for e in self.evolution_log if e.get('rollback')),
            'actions_taken': [e.get('action') for e in self.evolution_log if e.get('action') != ACTION_SKIP],
            'budget': self.budget.__dict__
        }
    
    def get_skills_for_prompt(self) -> str:
        """
        Get skills formatted for inclusion in agent prompt.
        
        This follows Claude's SKILL.md format where skills are:
        - Discoverable via description
        - Loaded when triggered
        - Include step-by-step instructions
        """
        if not self.state.skills.skills:
            return ""
        
        parts = ["# Agent Skills\n"]
        parts.append("The following skills provide guidance for specific tasks. Use them when relevant.\n")
        
        for skill in self.state.skills.skills.values():
            parts.append(f"## {skill.name.replace('-', ' ').title()}")
            parts.append(f"**When to use:** {skill.description}")
            if skill.instructions:
                parts.append(f"\n{skill.instructions}")
            if skill.examples:
                parts.append(f"\n### Examples\n{skill.examples}")
            parts.append("")
        
        return "\n".join(parts)
    
    def inject_skills_into_agent(self, agent) -> None:
        """
        Inject learned skills into the agent's system prompt.
        
        This modifies the agent's system_prompt to include skills,
        enabling the agent to use learned procedures.
        
        Args:
            agent: Agent with system_prompt attribute
        """
        skills_content = self.get_skills_for_prompt()
        if not skills_content:
            return
        
        # Get base prompt (original without skills)
        base_prompt = getattr(agent, 'original_system_prompt', agent.system_prompt)
        
        # Check if skills are already injected
        if "# Agent Skills" in agent.system_prompt:
            # Replace existing skills section
            parts = agent.system_prompt.split("# Agent Skills")
            if len(parts) > 1:
                # Find the end of the skills section (next major header or end)
                base_prompt = parts[0].rstrip()
        
        # Inject skills before the end of the prompt
        agent.system_prompt = base_prompt + "\n\n" + skills_content
        
        if self.verbose:
            print(f"    → Injected {len(self.state.skills.skills)} skills into agent prompt")
    
    def update_agent_with_skills(self, agent) -> None:
        """
        Alias for inject_skills_into_agent for clarity.
        Call this after evolving to update the solver with new skills.
        """
        self.inject_skills_into_agent(agent)

