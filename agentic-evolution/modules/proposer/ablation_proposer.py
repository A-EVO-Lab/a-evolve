"""
Ablation Proposer - Configurable proposer for ablation experiments.

This proposer allows disabling individual stages to measure their contribution:
- enable_diagnosis: If False, skip diagnosis and use trajectory directly
- enable_tool_calls: If False, LLM reflects without calling analysis tools
- enable_planning: If False, skip TODO generation, apply changes directly
- enable_verification: If False, commit without verification
"""

import os
import json
from typing import List, Dict, Optional, Any
from datetime import datetime

from .proposer_agent.appworld_diagnosis import AppWorldDiagnosisModule
from .proposer_agent.plan import PlanModule, TodoItem, TodoStatus
from .proposer_agent.apply import ApplyModule
from .proposer_agent.verifier import VerifierModule


class AblationProposer:
    """
    Ablation Proposer with configurable stages.
    
    Ablation modes:
    - no_diagnosis: Skip Stage 1, LLM generates fixes from raw trajectory
    - no_tools: Stage 1 without analysis tools (pure LLM reflection)
    - no_planning: Skip Stage 2, LLM generates changes directly
    - no_verifier: Skip Stage 4, commit without verification
    """
    
    MAX_REPAIR_ATTEMPTS = 3
    
    def __init__(
        self,
        workspace_dir: str,
        tool_builder=None,
        tool_registry=None,
        observation_window: int = 10,
        enable_tool_evolution: bool = True,
        enable_patches: bool = True,
        proposer_agent=None,
        verbose: bool = True,
        # Ablation flags
        enable_diagnosis: bool = True,
        enable_tool_calls: bool = True,
        enable_planning: bool = True,
        enable_verification: bool = True,
        ablation_mode: str = None  # Alternative: 'no_diagnosis', 'no_tools', 'no_planning', 'no_verifier'
    ):
        """
        Initialize ablation proposer.
        
        Args:
            workspace_dir: Directory for observations and version snapshots
            tool_builder: ToolBuilder instance (required for apply stage)
            tool_registry: ToolRegistry instance
            observation_window: Number of recent observations to consider
            enable_tool_evolution: Whether to create/evolve tools
            enable_patches: Whether to create patches
            proposer_agent: Optional separate agent for proposer (stronger model)
            verbose: Whether to print progress
            
            # Ablation flags
            enable_diagnosis: If False, skip diagnosis stage (no_diagnosis ablation)
            enable_tool_calls: If False, no tool calls in diagnosis (no_tools ablation)
            enable_planning: If False, skip planning stage (no_planning ablation)
            enable_verification: If False, skip verification stage (no_verifier ablation)
            ablation_mode: Alternative to flags - one of: 'no_diagnosis', 'no_tools', 'no_planning', 'no_verifier', 'full'
        """
        self.workspace_dir = workspace_dir
        self.tool_builder = tool_builder
        self.tool_registry = tool_registry
        self.observation_window = observation_window
        self.enable_tool_evolution = enable_tool_evolution
        self.enable_patches = enable_patches
        self.proposer_agent = proposer_agent
        self.verbose = verbose
        
        # Handle ablation mode string
        if ablation_mode:
            enable_diagnosis = ablation_mode != 'no_diagnosis'
            enable_tool_calls = ablation_mode != 'no_tools'
            enable_planning = ablation_mode != 'no_planning'
            enable_verification = ablation_mode != 'no_verifier'
        
        # Ablation configuration
        self.enable_diagnosis = enable_diagnosis
        self.enable_tool_calls = enable_tool_calls
        self.enable_planning = enable_planning
        self.enable_verification = enable_verification
        
        # Store ablation mode for logging
        self.ablation_mode = self._determine_ablation_mode()
        
        # Initialize modules
        self.diagnosis_module = AppWorldDiagnosisModule(
            workspace_dir=workspace_dir,
            enable_tools=enable_tool_calls,
            verbose=verbose
        )
        self.plan_module = PlanModule(verbose=verbose)
        self.apply_module = None  # Lazy init
        self.verifier_module = VerifierModule(verbose=verbose)
        
        # State
        self.current_patches = []
        self.improvement_history = []
        self.recent_observations = []
    
    def _determine_ablation_mode(self) -> str:
        """Determine ablation mode from flags."""
        if not self.enable_diagnosis:
            return 'no_diagnosis'
        elif not self.enable_tool_calls:
            return 'no_tools'
        elif not self.enable_planning:
            return 'no_planning'
        elif not self.enable_verification:
            return 'no_verifier'
        else:
            return 'full'
    
    def _ensure_apply_module(self):
        """Lazily initialize apply module."""
        if self.apply_module is None:
            if self.tool_builder is None:
                raise ValueError("tool_builder is required for apply stage")
            self.apply_module = ApplyModule(
                workspace_dir=self.workspace_dir,
                tool_builder=self.tool_builder,
                tool_registry=self.tool_registry,
                verbose=self.verbose
            )
    
    def propose(self, agent, obs: List[Dict], context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Propose improvements using configurable stages.
        
        Depending on ablation mode, some stages may be skipped or simplified.
        
        Args:
            agent: The agent to improve
            obs: List of observation dicts
            context: Context dict with tool_registry, patches, etc.
            
        Returns:
            Proposal dict with action, specs, and stage history
        """
        if self.verbose:
            print(f"\n[AblationProposer] 🔬 Mode: {self.ablation_mode.upper()}")
            print("=" * 60)
        
        # Ensure apply module is ready
        self._ensure_apply_module()
        
        # Track recent observations
        self.recent_observations.extend(obs[-self.observation_window:])
        self.recent_observations = self.recent_observations[-self.observation_window:]
        
        # Prepare shared context
        shared_context = self._prepare_context(obs, context)
        
        stage_history = []
        diagnosis = {}
        todos = []
        
        # Use proposer agent if available, otherwise use main agent
        working_agent = self.proposer_agent if self.proposer_agent else agent
        
        # ═══════════════════════════════════════════════════════════════════════
        # STAGE 1: DIAGNOSIS (can be skipped or simplified)
        # ═══════════════════════════════════════════════════════════════════════
        if self.enable_diagnosis:
            if self.verbose:
                tools_status = "WITH tools" if self.enable_tool_calls else "NO tools"
                print(f"\n[Ablation] ✅ STAGE 1: DIAGNOSIS ({tools_status})")
                print("=" * 60)
            
            diagnosis = self.diagnosis_module.run(working_agent, obs, shared_context)
            
            if self.verbose:
                primary = diagnosis.get('primary_cause') or diagnosis.get('primary_issue', 'unknown')
                print(f"  📋 Diagnosis result:")
                print(f"     - Primary issue: {primary}")
            
            stage_history.append({
                "stage": 1,
                "name": "diagnosis",
                "ablation": "enabled" if self.enable_tool_calls else "no_tools",
                "result": diagnosis
            })
        else:
            # Skip diagnosis - create minimal diagnosis from trajectory
            if self.verbose:
                print(f"\n[Ablation] ❌ STAGE 1: DIAGNOSIS (SKIPPED)")
                print("=" * 60)
            
            diagnosis = self._create_trajectory_summary(obs)
            
            stage_history.append({
                "stage": 1,
                "name": "diagnosis",
                "ablation": "skipped",
                "result": diagnosis
            })
        
        # Check if we should skip improvements
        if self._should_skip(shared_context, diagnosis):
            return self._create_skip_proposal(diagnosis, stage_history, "No improvements needed")
        
        # ═══════════════════════════════════════════════════════════════════════
        # STAGE 2: PLAN (can be skipped)
        # ═══════════════════════════════════════════════════════════════════════
        if self.enable_planning:
            if self.verbose:
                print(f"\n[Ablation] ✅ STAGE 2: PLAN")
                print("=" * 60)
            
            todos = self.plan_module.run(working_agent, diagnosis, shared_context)
            
            if self.verbose:
                print(f"  📋 Generated {len(todos)} to-do items")
            
            stage_history.append({
                "stage": 2,
                "name": "plan",
                "ablation": "enabled",
                "result": [t.to_dict() for t in todos]
            })
        else:
            # Skip planning - generate single direct action
            if self.verbose:
                print(f"\n[Ablation] ❌ STAGE 2: PLAN (SKIPPED)")
                print("=" * 60)
            
            todos = self._create_direct_action(working_agent, diagnosis, shared_context)
            
            stage_history.append({
                "stage": 2,
                "name": "plan",
                "ablation": "skipped",
                "result": [t.to_dict() for t in todos]
            })
        
        if not todos:
            return self._create_skip_proposal(diagnosis, stage_history, "No actions planned")
        
        # ═══════════════════════════════════════════════════════════════════════
        # STAGE 3: APPLY (always enabled)
        # ═══════════════════════════════════════════════════════════════════════
        if self.verbose:
            print(f"\n[Ablation] ✅ STAGE 3: APPLY")
            print("=" * 60)
        
        updated_agent, applied_changes = self.apply_module.run(
            working_agent, todos, shared_context.get("current_patches", []), shared_context
        )
        
        stage_history.append({
            "stage": 3,
            "name": "apply",
            "result": applied_changes
        })
        
        # Check if any changes were applied
        successful_changes = [c for c in applied_changes if c.get("success")]
        if not successful_changes:
            if self.verbose:
                print("  ❌ No successful changes - skipping")
            return self._create_skip_proposal(diagnosis, stage_history, "No changes applied")
        
        # ═══════════════════════════════════════════════════════════════════════
        # STAGE 4: VERIFY (can be skipped)
        # ═══════════════════════════════════════════════════════════════════════
        if self.enable_verification:
            if self.verbose:
                print(f"\n[Ablation] ✅ STAGE 4: VERIFY")
                print("=" * 60)
            
            verification = self.verifier_module.run(
                updated_agent, applied_changes, obs, shared_context
            )
            
            if self.verbose:
                print(f"  📋 Verification: {'✅ Passed' if verification.success else '❌ Failed'}")
            
            stage_history.append({
                "stage": 4,
                "name": "verify",
                "ablation": "enabled",
                "result": verification.to_dict()
            })
            
            # Handle verification result
            if verification.success:
                self.apply_module.commit()
                proposal = self._build_proposal(diagnosis, todos, stage_history, committed=True)
                proposal["_final_agent"] = updated_agent
                proposal["_validation_passed"] = True
            else:
                # Revert on failure
                self.apply_module.revert(updated_agent)
                proposal = self._create_skip_proposal(
                    diagnosis, stage_history,
                    f"Verification failed: {verification.message}"
                )
                proposal["_validation_failure"] = verification.to_dict()
        else:
            # Skip verification - commit directly
            if self.verbose:
                print(f"\n[Ablation] ❌ STAGE 4: VERIFY (SKIPPED)")
                print("=" * 60)
                print("  ⚠️ Committing without verification...")
            
            self.apply_module.commit()
            
            stage_history.append({
                "stage": 4,
                "name": "verify",
                "ablation": "skipped",
                "result": {"success": True, "message": "Verification skipped"}
            })
            
            proposal = self._build_proposal(diagnosis, todos, stage_history, committed=True)
            proposal["_final_agent"] = updated_agent
            proposal["_validation_passed"] = None  # Unknown - verification skipped
        
        # Record improvement attempt
        self.improvement_history.append({
            "ablation_mode": self.ablation_mode,
            "timestamp": datetime.now().isoformat(),
            "action": proposal.get("action"),
            "num_stages": len(stage_history)
        })
        
        return proposal
    
    def _create_trajectory_summary(self, obs: List[Dict]) -> Dict[str, Any]:
        """Create minimal diagnosis from trajectory when diagnosis is skipped."""
        errors = []
        for o in obs:
            trajectory = o.get('trajectory', [])
            for msg in trajectory:
                content = str(msg.get('content', ''))
                if 'error' in content.lower() or 'exception' in content.lower():
                    errors.append(content[:200])
        
        return {
            "primary_cause": "UNKNOWN",
            "error_category": "unknown",
            "recommended_action": "ADD_SKILL",
            "trajectory_summary": f"Found {len(errors)} potential errors",
            "errors": errors[:5],
            "source": "trajectory_only"
        }
    
    def _create_direct_action(
        self,
        agent,
        diagnosis: Dict,
        context: Dict
    ) -> List[TodoItem]:
        """Create direct action when planning is skipped."""
        # Generate a simple skill creation request
        recommended = diagnosis.get('recommended_action', 'ADD_SKILL')
        
        prompt = f"""Based on this diagnosis, generate ONE improvement action:

Diagnosis: {json.dumps(diagnosis, indent=2, default=str)[:2000]}

Output a JSON with:
- action: One of CREATE_SKILL, CREATE_TOOL, ADD_PATCH
- name: Name for the skill/tool/patch
- description: What it should do

JSON:"""
        
        response = agent.call(prompt)
        
        try:
            # Try to parse JSON from response
            import re
            json_match = re.search(r'\{[^{}]+\}', response, re.DOTALL)
            if json_match:
                action_data = json.loads(json_match.group())
                action = action_data.get('action', 'ADD_SKILL')
                
                return [TodoItem(
                    id="direct_action_1",
                    action=action,
                    description=action_data.get('description', 'Generated action'),
                    priority=1,
                    status=TodoStatus.PENDING,
                    spec=action_data
                )]
        except:
            pass
        
        # Fallback: create default skill action
        return [TodoItem(
            id="fallback_action_1",
            action="ADD_SKILL",
            description="Generated skill from diagnosis",
            priority=1,
            status=TodoStatus.PENDING,
            spec={"name": "auto_generated_skill", "triggers": ["error"]}
        )]
    
    def _prepare_context(self, obs: List[Dict], context: Optional[Dict]) -> Dict[str, Any]:
        """Prepare shared context for all stages."""
        shared_context = context.copy() if context else {}
        shared_context["workspace_dir"] = self.workspace_dir
        shared_context["enable_tool_evolution"] = self.enable_tool_evolution
        shared_context["enable_patches"] = self.enable_patches
        shared_context["current_patches"] = self.current_patches
        shared_context["ablation_mode"] = self.ablation_mode
        
        # Load skills if not provided
        if "current_skills" not in shared_context:
            skills_dir = os.path.join(self.workspace_dir, "skills")
            if os.path.exists(skills_dir):
                shared_context["current_skills"] = self._load_skills_from_directory(skills_dir)
            else:
                shared_context["current_skills"] = {}
        
        return shared_context
    
    def _load_skills_from_directory(self, skills_dir: str) -> Dict[str, Dict]:
        """Load skills from skills directory."""
        skills = {}
        if os.path.exists(skills_dir):
            for filename in os.listdir(skills_dir):
                if filename.endswith('.md'):
                    skill_name = filename[:-3]
                    filepath = os.path.join(skills_dir, filename)
                    try:
                        with open(filepath, 'r') as f:
                            content = f.read()
                            skills[skill_name] = {
                                "name": skill_name,
                                "content": content,
                                "file": filepath
                            }
                    except:
                        pass
        return skills
    
    def _should_skip(self, context: Dict, diagnosis: Dict) -> bool:
        """Determine if we should skip improvements."""
        if diagnosis.get("no_action_needed"):
            return True
        if diagnosis.get("primary_cause") == "NONE":
            return True
        return False
    
    def _create_skip_proposal(
        self,
        diagnosis: Dict,
        stage_history: List[Dict],
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a SKIP proposal."""
        return {
            "action": "SKIP",
            "reasoning": reason or "No improvements needed",
            "diagnosis": diagnosis,
            "stage_history": stage_history,
            "_final_agent": None,
            "_validation_passed": None
        }
    
    def _build_proposal(
        self,
        diagnosis: Dict,
        todos: List[TodoItem],
        stage_history: List[Dict],
        committed: bool
    ) -> Dict[str, Any]:
        """Build the final proposal from completed stages."""
        # Determine action from todos
        actions = [t.action for t in todos if t.status == TodoStatus.COMPLETED]
        primary_action = actions[0] if actions else "SKIP"
        
        return {
            "action": primary_action,
            "reasoning": diagnosis.get("recommended_action", "Improvement applied"),
            "diagnosis": diagnosis,
            "todos": [t.to_dict() for t in todos],
            "stage_history": stage_history,
            "committed": committed,
            "ablation_mode": self.ablation_mode
        }
    
    # Helper methods for compatibility
    def set_tool_builder(self, tool_builder):
        self.tool_builder = tool_builder
    
    def set_tool_registry(self, tool_registry):
        self.tool_registry = tool_registry
    
    def get_patches(self):
        return self.current_patches
    
    def load_patches(self, patches: List[Dict]):
        self.current_patches = patches
