"""
Tool-Enabled Proposer - Proposer as a Coding Agent.

This proposer acts as an autonomous agent that analyzes, plans, applies,
and verifies improvements to another AI agent. It uses a modular 4-stage
workflow with version control and repair capabilities.

Architecture:
    ToolEnabledProposer (this file)
        ├── DiagnosisModule (Stage 1) - Analyze errors and identify root causes
        ├── PlanModule (Stage 2) - Generate actionable to-do list
        ├── ApplyModule (Stage 3) - Apply changes with version control
        └── VerifierModule (Stage 4) - Verify changes work correctly
"""

import os
import json
from typing import List, Dict, Any, Optional
from collections import deque

from .proposer import Proposer
from .proposer_agent.diagnosis import DiagnosisModule
from .proposer_agent.appworld_diagnosis import AppWorldDiagnosisModule
from .proposer_agent.plan import PlanModule, TodoItem, TodoStatus
from .proposer_agent.apply import ApplyModule
from .proposer_agent.verifier import VerifierModule
from modules.skills import SkillLoader


class ToolEnabledProposer(Proposer):
    """
    Proposer that uses a modular 4-stage workflow to improve an AI agent.
    
    This proposer:
    1. DIAGNOSIS: Analyzes errors with tools to understand root causes
    2. PLAN: Generates a to-do list with specific actions
    3. APPLY: Applies changes with version control (revertable)
    4. VERIFY: Tests that changes work, with repair loop if needed
    
    Key Features:
    - Modular design with separate modules for each stage
    - Version control with snapshots and revert capability
    - Repair loop for failed verifications (max 1 retry)
    - Detailed logging for debugging
    """
    
    # MAX_REPAIR_ATTEMPTS = 3
    
    def __init__(
        self,
        workspace_dir: str,
        tool_builder=None,
        tool_registry=None,
        observation_window: int = 10,
        enable_tool_evolution: bool = True,
        enable_patches: bool = True,
        proposer_agent=None,
        verbose: bool = True
    ):
        """
        Initialize tool-enabled proposer.
        
        Args:
            workspace_dir: Directory for observations and version snapshots
            tool_builder: ToolBuilder instance (required for apply stage)
            tool_registry: ToolRegistry instance (required for apply stage)
            observation_window: Recent observations to keep in memory
            enable_tool_evolution: Allow tool creation/evolution
            enable_patches: Allow patch creation
            proposer_agent: LLM agent for making proposals (if None, uses training agent)
            verbose: Whether to print debug output
        """
        self.workspace_dir = workspace_dir
        self.observation_window = observation_window
        self.enable_tool_evolution = enable_tool_evolution
        self.enable_patches = enable_patches
        self.proposer_agent = proposer_agent
        self.verbose = verbose
        
        self.MAX_REPAIR_ATTEMPTS = 3

        # Recent observations for quick context
        self.recent_observations = deque(maxlen=observation_window)
        
        # Initialize sub-modules
        # self.diagnosis_module = DiagnosisModule(workspace_dir, verbose=verbose)
        self.diagnosis_module = AppWorldDiagnosisModule(workspace_dir, verbose=verbose)
        self.plan_module = PlanModule(verbose=verbose)
        self.apply_module = None  # Lazy init (needs tool_builder/registry)
        self.verifier_module = VerifierModule(verbose=verbose)
        
        # Store tool_builder and tool_registry for lazy init
        self._tool_builder = tool_builder
        self._tool_registry = tool_registry
        
        # Initialize SkillLoader (learn-claude-code pattern)
        skills_dir = os.path.join(workspace_dir, "skills")
        self.skill_loader = SkillLoader(skills_dir, verbose=verbose)
        
        # State tracking
        self.current_patches: List[Dict] = []
        self.improvement_history: List[Dict] = []
    
    def _ensure_apply_module(self):
        """Lazily initialize apply module when needed."""
        if self.apply_module is None:
            if self._tool_builder is None or self._tool_registry is None:
                raise ValueError("tool_builder and tool_registry are required for apply stage")
            
            self.apply_module = ApplyModule(
                tool_builder=self._tool_builder,
                tool_registry=self._tool_registry,
                workspace_dir=self.workspace_dir,
                verbose=self.verbose
            )
    
    def propose(self, agent, obs, context=None):
        """
        Propose improvements using 4-stage modular workflow.
        
        Stages:
        1. DIAGNOSIS: Analyze errors and identify root causes
        2. PLAN: Generate to-do list with specific actions
        3. APPLY: Apply changes with version control
        4. VERIFY: Test changes, repair if needed
        
        Args:
            agent: The agent being improved
            obs: Current batch observations (list of dicts)
            context: Context dict with tool_registry, patches, etc.
            
        Returns:
            Proposal dict with action, specs, and stage history
        """
        # Normalize observations to list
        if not isinstance(obs, list):
            obs = [obs]
        
        # Add to recent history
        for observation in obs:
            self.recent_observations.append(observation)
        
        # Prepare shared context
        shared_context = self._prepare_context(obs, context)
        
        # Use dedicated proposer agent or fall back to training agent
        proposer_agent = self.proposer_agent if self.proposer_agent else agent
        
        # Track stage history for debugging
        stage_history = []
        
        # ═══════════════════════════════════════════════════════════════════════
        # STAGE 1: DIAGNOSIS
        # ═══════════════════════════════════════════════════════════════════════
        if self.verbose:
            print("\n" + "="*60)
            print("[Proposer] 🔬 STAGE 1: DIAGNOSIS")
            print("="*60)
            print(f"  📊 Analyzing {len(obs)} observations...")
        
        diagnosis = self.diagnosis_module.run(proposer_agent, obs, shared_context)
        
        if self.verbose:
            print(f"  📋 Diagnosis result:")
            if isinstance(diagnosis, dict):
                # Support both AppWorld format (primary_cause) and generic format (primary_issue)
                primary = diagnosis.get('primary_cause') or diagnosis.get('primary_issue', 'unknown')
                root = diagnosis.get('error_category') or diagnosis.get('root_cause', 'unknown')
                rec = diagnosis.get('recommended_action') or diagnosis.get('recommendation', 'unknown')
                print(f"     - Primary issue: {primary}")
                print(f"     - Root cause: {root}")
                print(f"     - Recommendation: {rec}")
            else:
                print(f"     {str(diagnosis)[:200]}...")
        
        stage_history.append({
            "stage": 1,
            "name": "diagnosis",
            "result": diagnosis
        })
        
        # Check for early exit (high accuracy, SKIP recommendation)
        if self._should_skip(shared_context, diagnosis):
            if self.verbose:
                print("  ⏭️ Skipping - no action needed")
            return self._create_skip_proposal(diagnosis, stage_history)
        
        # ═══════════════════════════════════════════════════════════════════════
        # STAGE 2: PLAN
        # ═══════════════════════════════════════════════════════════════════════
        if self.verbose:
            print("\n" + "="*60)
            print("[Proposer] 📝 STAGE 2: PLAN")
            print("="*60)
            print("  🔍 Generating action plan...")
        
        todos = self.plan_module.run(proposer_agent, diagnosis, shared_context, obs)
        
        if self.verbose:
            print(f"  📋 Generated {len(todos)} action items:")
            for i, todo in enumerate(todos):
                action = todo.action_type if hasattr(todo, 'action_type') else 'unknown'
                desc = todo.description if hasattr(todo, 'description') else str(todo)[:100]
                print(f"     {i+1}. [{action}] {desc[:80]}...")
        
        stage_history.append({
            "stage": 2,
            "name": "plan",
            "result": {
                "num_todos": len(todos),
                "todos": [t.to_dict() for t in todos]
            }
        })
        
        # Check if plan is empty
        if not todos:
            if self.verbose:
                print("  ⏭️ Empty plan - skipping")
            return self._create_skip_proposal(diagnosis, stage_history, "No actionable items in plan")
        
        # ═══════════════════════════════════════════════════════════════════════
        # STAGE 3: APPLY
        # ═══════════════════════════════════════════════════════════════════════
        if self.verbose:
            print("\n" + "="*60)
            print("[Proposer] ⚙️ STAGE 3: APPLY")
            print("="*60)
            print(f"  🔧 Applying {len(todos)} changes...")
        
        self._ensure_apply_module()
        
        updated_agent, applied_changes = self.apply_module.run(
            agent, todos, self.current_patches, shared_context
        )
        
        if self.verbose:
            print(f"  📋 Applied changes:")
            for i, change in enumerate(applied_changes):
                change_type = change.get('type', 'unknown')
                success = '✓' if change.get('success') else '✗'
                name = change.get('name', change.get('description', 'unnamed'))[:50]
                print(f"     {i+1}. [{change_type}] {success} {name}")
        
        # Update patches from context
        self.current_patches = shared_context.get("patches", self.current_patches)
        
        stage_history.append({
            "stage": 3,
            "name": "apply",
            "result": {
                "num_changes": len(applied_changes),
                "changes": applied_changes
            }
        })
        
        # Check if any changes were applied
        successful_changes = [c for c in applied_changes if c.get("success")]
        if not successful_changes:
            if self.verbose:
                print("  ❌ No successful changes - skipping")
            return self._create_skip_proposal(diagnosis, stage_history, "No changes were successfully applied")
        
        # ═══════════════════════════════════════════════════════════════════════
        # STAGE 4: VERIFY
        # ═══════════════════════════════════════════════════════════════════════
        if self.verbose:
            print("\n" + "="*60)
            print("[Proposer] ✅ STAGE 4: VERIFY")
            print("="*60)
            print(f"  🧪 Verifying {len(successful_changes)} changes...")
        
        verification = self.verifier_module.run(
            updated_agent, applied_changes, obs, shared_context
        )
        
        if self.verbose:
            print(f"  📋 Verification result:")
            print(f"     - Success: {verification.success}")
            print(f"     - Message: {verification.message if verification.message else 'N/A'}")
        
        stage_history.append({
            "stage": 4,
            "name": "verify",
            "result": verification.to_dict()
        })
        
        # ═══════════════════════════════════════════════════════════════════════
        # REPAIR LOOP (if verification failed)
        # ═══════════════════════════════════════════════════════════════════════
        repair_attempt = 0
        
        while not verification.success and repair_attempt < self.MAX_REPAIR_ATTEMPTS:
            repair_attempt += 1
            
            if self.verbose:
                print(f"\n[Proposer] 🔧 Repair attempt {repair_attempt}/{self.MAX_REPAIR_ATTEMPTS}...")
            
            # Run repair diagnosis
            repair_diagnosis = self.diagnosis_module.run_repair_diagnosis(
                proposer_agent, obs, shared_context, verification.to_dict()
            )
            stage_history.append({
                "stage": f"4.{repair_attempt}a",
                "name": "repair_diagnosis",
                "result": repair_diagnosis
            })
            
            # Generate repair plan
            repair_todos = self.plan_module.run_repair_plan(
                proposer_agent, repair_diagnosis, todos, shared_context
            )
            stage_history.append({
                "stage": f"4.{repair_attempt}b",
                "name": "repair_plan",
                "result": {
                    "num_todos": len(repair_todos),
                    "todos": [t.to_dict() for t in repair_todos]
                }
            })
            
            if not repair_todos:
                break
            
            # Revert to previous state
            reverted_agent, reverted_patches = self.apply_module.revert(updated_agent)
            self.current_patches = reverted_patches
            
            # Apply repair
            updated_agent, applied_changes = self.apply_module.run(
                reverted_agent, repair_todos, reverted_patches, shared_context
            )
            
            # Update patches
            self.current_patches = shared_context.get("patches", self.current_patches)
            
            stage_history.append({
                "stage": f"4.{repair_attempt}c",
                "name": "repair_apply",
                "result": {
                    "num_changes": len(applied_changes),
                    "changes": applied_changes
                }
            })
            
            # Re-verify
            verification = self.verifier_module.run(
                updated_agent, applied_changes, obs, shared_context
            )
            
            stage_history.append({
                "stage": f"4.{repair_attempt}d",
                "name": "repair_verify",
                "result": verification.to_dict()
            })
            
            todos = repair_todos  # Update for final proposal
        
        # ═══════════════════════════════════════════════════════════════════════
        # FINAL DECISION: Commit or Partial Revert
        # ═══════════════════════════════════════════════════════════════════════
        
        # Categorize changes by type
        tool_changes = [c for c in applied_changes if c.get("action") in ["CREATE_TOOL", "EVOLVE_TOOL"] and c.get("success")]
        skill_knowledge_changes = [c for c in applied_changes if c.get("action") in ["ADD_SKILL", "CREATE_SKILL", "EVOLVE_SKILL", "ADD_KNOWLEDGE"] and c.get("success")]
        
        if verification.success:
            # All changes passed verification - commit everything
            self.apply_module.commit()
            
            if self.verbose:
                print("\n[Proposer] ✅ All stages passed - changes committed")
            
            # Build final proposal
            proposal = self._build_proposal(diagnosis, todos, stage_history, committed=True)
            proposal["_final_agent"] = updated_agent
            proposal["_validation_passed"] = True
            
        else:
            # Tool verification failed, but we should still keep skills/knowledge
            if self.verbose:
                print("\n[Proposer] ⚠️ Tool verification failed")
            
            if skill_knowledge_changes:
                # Commit skills/knowledge changes (they don't need tool verification)
                if self.verbose:
                    print(f"[Proposer] ✅ Keeping {len(skill_knowledge_changes)} skill/knowledge changes")
                
                # We keep skills/knowledge, only revert tool changes
                # The apply module has already saved skills/knowledge to disk
                # So we just need to mark them as committed
                self.apply_module.commit()  # Commit the snapshot (keeps skills/knowledge)
                
                if tool_changes:
                    if self.verbose:
                        print(f"[Proposer] ⏪ Reverting {len(tool_changes)} failed tool changes")
                    # Revert only tool files by deleting them
                    for tool_change in tool_changes:
                        tool_path = tool_change.get("file_path")
                        if tool_path and os.path.exists(tool_path):
                            try:
                                os.remove(tool_path)
                                if self.verbose:
                                    print(f"[Proposer]   Removed failed tool: {os.path.basename(tool_path)}")
                            except Exception as e:
                                if self.verbose:
                                    print(f"[Proposer]   Failed to remove tool: {e}")
                
                # Build proposal indicating partial success
                proposal = self._build_proposal(diagnosis, todos, stage_history, committed=True)
                proposal["_final_agent"] = updated_agent
                proposal["_validation_passed"] = False
                proposal["_partial_commit"] = True
                proposal["_committed_changes"] = [c.get("action") for c in skill_knowledge_changes]
                proposal["_reverted_changes"] = [c.get("action") for c in tool_changes]
                
            else:
                # No skill/knowledge changes, just tools - full revert
                if self.verbose:
                    print("[Proposer] ❌ No skill/knowledge changes to keep - reverting all")
                
                try:
                    reverted_agent, reverted_patches = self.apply_module.revert(updated_agent)
                    self.current_patches = reverted_patches
                except Exception as e:
                    if self.verbose:
                        print(f"[Proposer] ⚠️ Revert failed: {e}")
                
                proposal = self._create_skip_proposal(
                    diagnosis, stage_history, 
                    f"Verification failed: {verification.message}"
                )
                proposal["_validation_failure"] = verification.to_dict()
        
        # Record improvement attempt
        self.improvement_history.append({
            "batch_id": shared_context.get("current_batch_id"),
            "timestamp": obs[0].get("timestamp") if obs else None,
            "action": proposal.get("action"),
            "success": verification.success,
            "partial_commit": proposal.get("_partial_commit", False),
            "num_stages": len(stage_history)
        })
        
        return proposal
    
    def _prepare_context(self, obs: List[Dict], context: Optional[Dict]) -> Dict:
        """Prepare shared context for all stages."""
        # Load summary stats if available
        summary_stats = {}
        summary_file = os.path.join(self.workspace_dir, "summary_stats.json")
        if os.path.exists(summary_file):
            with open(summary_file, 'r') as f:
                summary_stats = json.load(f)
        
        num_batches = len(summary_stats.get("batch_accuracies", []))
        total_samples = summary_stats.get("total_samples", 0)
        
        # Current batch stats
        current_batch_id = obs[0].get("batch_id", num_batches) if obs else num_batches
        current_batch_correct = sum(1 for o in obs if o.get("is_correct", False))
        current_batch_accuracy = current_batch_correct / len(obs) if obs else 0.0
        
        # Extract from provided context
        tool_registry = context.get("tool_registry") if context else self._tool_registry
        patches = context.get("patches", self.current_patches) if context else self.current_patches
        skills = context.get("skills", {}) if context else {}
        knowledge = context.get("knowledge", []) if context else []
        improvement_history = context.get("improvement_history", self.improvement_history) if context else self.improvement_history
        
        # Try to load skills and knowledge from workspace if not in context
        if not skills:
            # Use SkillLoader for proper YAML frontmatter parsing (learn-claude-code pattern)
            self.skill_loader.reload()  # Refresh from disk
            skills = self.skill_loader.to_dict()
            if self.verbose and skills:
                print(f"[Proposer] 📚 Loaded {len(skills)} skills via SkillLoader")
        
        if not knowledge:
            knowledge_file = os.path.join(self.workspace_dir, "knowledge.json")
            if os.path.exists(knowledge_file):
                with open(knowledge_file, 'r') as f:
                    knowledge = json.load(f)
        
        # Extract reward signal from AppWorld scores (goal-directed optimization)
        scores = [o.get('score', 0.0) for o in obs if 'score' in o]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        score_improvement_needed = avg_score < 0.8
        
        # Track completion rate as secondary metric
        completed = sum(1 for o in obs if o.get('task_completed', False))
        completion_rate = completed / len(obs) if obs else 0.0
        
        return {
            "observations": obs,
            "summary_stats": summary_stats,
            "num_batches": num_batches,
            "total_samples": total_samples,
            "current_batch_id": current_batch_id,
            "current_batch_correct": current_batch_correct,
            "current_batch_accuracy": current_batch_accuracy,
            # Reward signals for goal-directed optimization
            "reward_signal": avg_score,
            "score_improvement_needed": score_improvement_needed,
            "completion_rate": completion_rate,
            # Resources
            "tool_registry": tool_registry,
            "patches": patches,
            "skills": skills,
            "knowledge": knowledge,
            "improvement_history": improvement_history,
            "num_tools": len(tool_registry.list_tools()) if tool_registry else 0,
            "num_patches": len(patches),
            "num_skills": len(skills),
            "num_knowledge": len(knowledge),
        }
    
    def _load_skills_from_directory(self, skills_dir: str) -> Dict[str, Any]:
        """Load all skill files from skills/ directory."""
        import re
        
        skills = {}
        
        try:
            for filename in os.listdir(skills_dir):
                if filename.endswith('.md'):
                    skill_path = os.path.join(skills_dir, filename)
                    skill_data = self._parse_skill_file(skill_path)
                    if skill_data and skill_data.get('name'):
                        skills[skill_data['name']] = skill_data
        except Exception as e:
            if self.verbose:
                print(f"[Proposer] ⚠️ Failed to load skills directory: {e}")
        
        return skills
    
    def _parse_skill_file(self, filepath: str) -> Dict[str, Any]:
        """Parse a single skill markdown file."""
        import re
        
        try:
            with open(filepath, 'r') as f:
                content = f.read()
        except Exception as e:
            return {}
        
        # Extract skill name from # header
        name_match = re.search(r'^# (.+)$', content, re.MULTILINE)
        skill_name = name_match.group(1).strip() if name_match else os.path.basename(filepath)[:-3]
        
        # Extract description
        desc_match = re.search(r'\*\*Description:\*\* ([^\n]+)', content)
        description = desc_match.group(1) if desc_match else ''
        
        # Extract triggers (when to use)
        triggers_match = re.search(r'\*\*When to use:\*\* ([^\n]+)', content)
        triggers = triggers_match.group(1).split(', ') if triggers_match else []
        
        # Extract instructions (everything after ## Instructions)
        instr_match = re.search(r'## Instructions\s*\n(.*?)(?=\n---|\Z)', content, re.DOTALL)
        instructions = instr_match.group(1).strip() if instr_match else ''
        
        # Extract version
        version_match = re.search(r'\*Version: (\d+)\*', content)
        version = int(version_match.group(1)) if version_match else 1
        
        return {
            'name': skill_name,
            'description': description,
            'triggers': triggers,
            'instructions': instructions,
            'version': version,
            'file_path': filepath
        }
    
    def _parse_skills_markdown(self, filepath: str) -> Dict[str, Any]:
        """Parse skills.md file and extract all skills (legacy support)."""
        import re
        
        try:
            with open(filepath, 'r') as f:
                content = f.read()
        except Exception as e:
            if self.verbose:
                print(f"[Proposer] ⚠️ Failed to read skills file: {e}")
            return {}
        
        skills = {}
        
        # Pattern to match skill sections: ## skill_name ... ---
        pattern = r'## ([^\n]+)\n(.*?)(?=---|\\Z)'
        
        matches = re.findall(pattern, content, re.DOTALL)
        
        for skill_name, skill_content in matches:
            skill_name = skill_name.strip()
            
            # Extract description
            desc_match = re.search(r'\*\*Description:\*\* ([^\n]+)', skill_content)
            description = desc_match.group(1) if desc_match else ''
            
            # Extract triggers (when to use)
            triggers_match = re.search(r'\*\*When to use:\*\* ([^\n]+)', skill_content)
            triggers = triggers_match.group(1).split(', ') if triggers_match else []
            
            # Extract instructions (everything after **Instructions:**)
            instr_match = re.search(r'\*\*Instructions:\*\*\n(.*)', skill_content, re.DOTALL)
            instructions = instr_match.group(1).strip() if instr_match else ''
            
            skills[skill_name] = {
                'name': skill_name,
                'description': description,
                'triggers': triggers,
                'instructions': instructions
            }
        
        return skills
    
    def _should_skip(self, context: Dict, diagnosis: Dict) -> bool:
        """Determine if we should skip improvements."""
        # High reward signal from AppWorld scores (goal-directed check)
        reward_signal = context.get("reward_signal", 0)
        if reward_signal > 0.9:
            if self.verbose:
                print(f"[Proposer] ⏭️ Skipping - high reward signal ({reward_signal:.2f})")
            return True
        
        # High accuracy with SKIP recommendation
        if context.get("current_batch_accuracy", 0) > 0.8:
            if diagnosis.get("recommended_action") == "SKIP":
                return True
        
        # Explicit SKIP recommendation
        if diagnosis.get("recommended_action") == "SKIP":
            return True
        
        return False
    
    def _create_skip_proposal(
        self,
        diagnosis: Dict,
        stage_history: List[Dict],
        reason: Optional[str] = None
    ) -> Dict:
        """Create a SKIP proposal."""
        return {
            "type": "hybrid",
            "action": "SKIP",
            "reasoning": reason or diagnosis.get("reasoning", "No improvement needed"),
            "confidence": "high" if diagnosis.get("recommended_action") == "SKIP" else "low",
            "_stage_history": stage_history,
            "_num_stages": len(stage_history),
            "_validation_passed": True
        }
    
    def _build_proposal(
        self,
        diagnosis: Dict,
        todos: List[TodoItem],
        stage_history: List[Dict],
        committed: bool
    ) -> Dict:
        """Build the final proposal from completed stages."""
        # Determine primary action from todos
        action = "SKIP"
        tool_spec = None
        skill_spec = None
        knowledge_spec = None
        
        for todo in todos:
            if todo.status == TodoStatus.COMPLETED:
                action = todo.action
                if todo.action in ["CREATE_TOOL", "EVOLVE_TOOL"]:
                    tool_spec = todo.spec
                elif todo.action in ["ADD_SKILL", "EVOLVE_SKILL"]:
                    skill_spec = todo.spec
                elif todo.action in ["ADD_KNOWLEDGE", "EVOLVE_KNOWLEDGE"]:
                    knowledge_spec = todo.spec
                # Backward compatibility: convert ADD_PATCH to ADD_SKILL
                elif todo.action == "ADD_PATCH":
                    action = "ADD_SKILL"  # Convert action
                    skill_spec = todo.spec.copy() if todo.spec else {}
                    skill_spec["scope"] = "tactical_patch"  # Mark as patch
                break  # Use first completed action
        
        proposal = {
            "type": "hybrid",
            "action": action,
            "reasoning": diagnosis.get("reasoning", ""),
            "confidence": diagnosis.get("confidence", "medium"),
            "_stage_history": stage_history,
            "_num_stages": len(stage_history),
            "_validation_passed": committed,
            "_committed": committed
        }
        
        if tool_spec:
            proposal["tool_spec"] = tool_spec
        if skill_spec:
            proposal["skill_spec"] = skill_spec
        if knowledge_spec:
            proposal["knowledge_spec"] = knowledge_spec
        
        return proposal
    
    def set_tool_builder(self, tool_builder):
        """Set the tool builder (for lazy initialization)."""
        self._tool_builder = tool_builder
        if self.apply_module:
            self.apply_module.tool_builder = tool_builder
    
    def set_tool_registry(self, tool_registry):
        """Set the tool registry (for lazy initialization)."""
        self._tool_registry = tool_registry
        if self.apply_module:
            self.apply_module.tool_registry = tool_registry
    
    def get_patches(self) -> List[Dict]:
        """Get current patches."""
        return self.current_patches
    
    def load_patches(self, patches: List[Dict]):
        """Load patches from external source."""
        self.current_patches = patches
    
    def get_plan_summary(self) -> Dict:
        """Get summary of current plan status."""
        return self.plan_module.get_plan_summary()
    
    def get_version_history(self) -> List[Dict]:
        """Get version control history."""
        if self.apply_module:
            return self.apply_module.get_version_history()
        return []
    
    def clear_history(self):
        """Clear recent observation history."""
        self.recent_observations.clear()
    
    def save_state(self, filepath: str):
        """Save proposer state to file."""
        state = {
            "patches": self.current_patches,
            "improvement_history": self.improvement_history,
            "plan_history": self.plan_module.plan_history
        }
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(state, f, indent=2)
    
    def load_state(self, filepath: str):
        """Load proposer state from file."""
        if not os.path.exists(filepath):
            return
        
        with open(filepath, 'r') as f:
            state = json.load(f)
        
        self.current_patches = state.get("patches", [])
        self.improvement_history = state.get("improvement_history", [])
        self.plan_module.plan_history = state.get("plan_history", [])
