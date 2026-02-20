"""
AppWorld Observer - Score-based observation with trajectory logging.

Observes agent execution in AppWorld tasks, tracking:
- Task completion score (0.0-1.0)
- Full execution trajectory
- API usage patterns
- Errors and failures
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field, asdict

from modules.observer.persistent_observer import PersistentObserver


@dataclass
class AppWorldObservation:
    """Single observation from an AppWorld task execution."""
    task_id: str
    timestamp: str
    score: float  # 0.0-1.0
    task_completed: bool
    num_steps: int
    trajectory: List[Dict]  # Full message history
    api_calls: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    error_details: List[Dict[str, Any]] = field(default_factory=list)
    
    # Metadata
    experiment_name: str = ""
    run_id: int = 0
    
    def to_dict(self) -> Dict:
        return asdict(self)


class AppWorldObserver(PersistentObserver):
    """
    Observer for AppWorld with score-based evaluation.
    
    Unlike exact-match or semantic evaluation, AppWorld uses
    task completion score from the environment itself.
    """
    
    def __init__(
        self,
        workspace_dir: str,
        verbose: bool = True
    ):
        """
        Initialize AppWorld observer.
        
        Args:
            workspace_dir: Directory for observation logs
            verbose: Enable verbose output
        """
        super().__init__(workspace_dir=workspace_dir)
        
        self.verbose = verbose
        self.task_type = "appworld"
        
        # In-memory observations list (PersistentObserver doesn't have this)
        self.observations: List[Dict] = []
        
        # Score-based statistics
        self.score_stats = {
            'total_tasks': 0,
            'completed_tasks': 0,
            'perfect_tasks': 0,
            'partial_tasks': 0,
            'failed_tasks': 0,
            'total_score': 0.0,
            'total_steps': 0
        }
        
        # API usage tracking
        self.api_usage: Dict[str, int] = {}
        
        # Error patterns
        self.error_patterns: List[Dict] = []
    
    def _write_observation(self, obs_dict: Dict):
        """Write a single observation to disk."""
        import json
        from modules.observer.persistent_observer import SafeJSONEncoder
        
        # Write to batch file
        batch_file = os.path.join(
            self.observations_dir,
            f"batch_{self.current_batch_id:03d}.jsonl"
        )
        
        with open(batch_file, 'a') as f:
            f.write(json.dumps(obs_dict, cls=SafeJSONEncoder) + '\n')

    
    def observe_task(
        self,
        task_id: str,
        trajectory: List[Dict],
        score: float,
        task_completed: bool,
        experiment_name: str = "",
        run_id: int = 0,
        **metadata
    ) -> AppWorldObservation:
        """
        Record an AppWorld task observation.
        
        Args:
            task_id: AppWorld task ID
            trajectory: Full message history (system, user, assistant messages)
            score: Task completion score (0.0-1.0)
            task_completed: Whether task was marked complete
            experiment_name: Name of the experiment
            run_id: Run identifier for multiple trials
        """
        # Extract API calls from trajectory
        api_calls = self._extract_api_calls(trajectory)
        
        # Extract errors from trajectory
        error_details = self._extract_error_details(trajectory)
        errors = self._extract_errors(trajectory, error_details)
        
        # Create observation
        obs = AppWorldObservation(
            task_id=task_id,
            timestamp=datetime.now().isoformat(),
            score=score,
            task_completed=task_completed,
            num_steps=len([m for m in trajectory if m.get('role') == 'assistant']),
            trajectory=trajectory,
            api_calls=api_calls,
            errors=errors,
            error_details=error_details,
            experiment_name=experiment_name,
            run_id=run_id
        )
        
        # Store observation
        self.observations.append(obs.to_dict())
        self._write_observation(obs.to_dict())
        
        # Update statistics
        self._update_stats(obs)
        
        if self.verbose:
            status = "✓" if score == 1.0 else ("◐" if score > 0 else "✗")
            print(f"  {status} Task {task_id}: score={score:.2f}, steps={obs.num_steps}")
        
        return obs
    
    def _extract_api_calls(self, trajectory: List[Dict]) -> List[str]:
        """Extract API calls from code blocks in trajectory."""
        import re
        api_calls = []
        
        for msg in trajectory:
            if msg.get('role') == 'assistant':
                content = msg.get('content', '')
                # Match apis.app.method patterns
                matches = re.findall(r'apis\.(\w+)\.(\w+)', content)
                for app, method in matches:
                    api_call = f"{app}.{method}"
                    api_calls.append(api_call)
                    # Update global API usage
                    self.api_usage[api_call] = self.api_usage.get(api_call, 0) + 1
        
        return list(set(api_calls))
    
    def _extract_errors(self, trajectory: List[Dict], error_details: Optional[List[Dict[str, Any]]] = None) -> List[str]:
        """Extract error messages from trajectory."""
        import re
        errors = []
        if error_details:
            for detail in error_details:
                message = detail.get('message') or detail.get('type')
                if message:
                    errors.append(str(message)[:200])
        
        for msg in trajectory:
            if msg.get('role') == 'user':
                content = msg.get('content', '')
                # Look for common error patterns
                if 'Error' in content or 'error' in content:
                    # Extract first line of error
                    error_line = content.split('\n')[0][:200]
                    errors.append(error_line)
                if 'Traceback' in content:
                    errors.append('Python exception occurred')
        
        # De-duplicate while preserving order
        seen = set()
        deduped = []
        for err in errors:
            if err not in seen:
                seen.add(err)
                deduped.append(err)
        return deduped

    def _extract_error_details(self, trajectory: List[Dict]) -> List[Dict[str, Any]]:
        """Extract structured error details from trajectory outputs."""
        import re
        details: List[Dict[str, Any]] = []
        seen = set()
        
        def _add(detail: Dict[str, Any]):
            key = (
                detail.get('type'),
                detail.get('status_code'),
                detail.get('app_name'),
                detail.get('api_name'),
                detail.get('name')
            )
            if key not in seen:
                seen.add(key)
                details.append(detail)
        
        for msg in trajectory:
            if msg.get('role') != 'user':
                continue
            content = str(msg.get('content', ''))
            if not content:
                continue
            
            # HTTP status codes from AppWorld errors
            for match in re.finditer(r'Response status code is (\d+)', content):
                code = int(match.group(1))
                _add({
                    "type": "http_status",
                    "status_code": code,
                    "message": f"Response status code is {code}"
                })
            
            # Missing API names
            api_match = re.search(r"No API named '([^']+)' found in the ([^ ]+) app", content)
            if api_match:
                _add({
                    "type": "api_not_found",
                    "app_name": api_match.group(2),
                    "api_name": api_match.group(1),
                    "message": api_match.group(0)
                })
            
            # Missing app names
            app_match = re.search(r"No app named '([^']+)' found", content)
            if app_match:
                _add({
                    "type": "app_not_found",
                    "app_name": app_match.group(1),
                    "message": app_match.group(0)
                })
            
            # NameError (e.g., TOOL_CALL: ...)
            name_match = re.search(r"NameError: name '([^']+)' is not defined", content)
            if name_match:
                _add({
                    "type": "name_error",
                    "name": name_match.group(1),
                    "message": name_match.group(0)
                })
            
            if "TOOL_CALL:" in content:
                _add({
                    "type": "tool_call_literal",
                    "message": "TOOL_CALL text executed as Python"
                })
        
        return details
    
    def _update_stats(self, obs: AppWorldObservation):
        """Update cumulative statistics."""
        self.score_stats['total_tasks'] += 1
        self.score_stats['total_score'] += obs.score
        self.score_stats['total_steps'] += obs.num_steps
        
        if obs.task_completed:
            self.score_stats['completed_tasks'] += 1
        if obs.score >= 1.0:
            self.score_stats['perfect_tasks'] += 1
        elif obs.score > 0:
            self.score_stats['partial_tasks'] += 1
        else:
            self.score_stats['failed_tasks'] += 1
        
        # Track error patterns
        if obs.errors and obs.score < 1.0:
            self.error_patterns.append({
                'task_id': obs.task_id,
                'errors': obs.errors,
                'score': obs.score
            })
    
    def get_statistics(self) -> Dict:
        """Get observation statistics."""
        total = self.score_stats['total_tasks']
        avg_score = self.score_stats['total_score'] / total if total > 0 else 0
        avg_steps = self.score_stats['total_steps'] / total if total > 0 else 0
        
        return {
            'total_tasks': total,
            'completed_tasks': self.score_stats['completed_tasks'],
            'perfect_tasks': self.score_stats['perfect_tasks'],
            'partial_tasks': self.score_stats['partial_tasks'],
            'failed_tasks': self.score_stats['failed_tasks'],
            'average_score': avg_score,
            'average_steps': avg_steps,
            'completion_rate': self.score_stats['completed_tasks'] / total if total > 0 else 0,
            'top_apis': sorted(self.api_usage.items(), key=lambda x: -x[1])[:10]
        }
    
    def get_failures(self, n: int = 10) -> List[Dict]:
        """Get recent failed observations for proposer analysis."""
        failures = [
            obs for obs in self.observations 
            if obs.get('score', 1.0) < 1.0
        ]
        return failures[-n:] if len(failures) > n else failures
    
    def summarize_failures(self, n: int = 10) -> str:
        """Summarize recent failures for proposer."""
        failures = self.get_failures(n)
        
        if not failures:
            return "No failures recorded."
        
        parts = [f"Recent failures ({len(failures)} tasks):\n"]
        
        for i, obs in enumerate(failures, 1):
            task_id = obs.get('task_id', 'unknown')
            score = obs.get('score', 0)
            errors = obs.get('errors', [])
            apis = obs.get('api_calls', [])
            
            parts.append(f"\n{i}. Task: {task_id} (score: {score:.2f})")
            if errors:
                parts.append(f"   Errors: {'; '.join(errors[:2])}")
            if apis:
                parts.append(f"   APIs used: {', '.join(apis[:5])}")
        
        return "\n".join(parts)
    
    def get_error_patterns(self) -> List[Dict]:
        """Analyze common error patterns."""
        from collections import Counter
        
        error_counter = Counter()
        for pattern in self.error_patterns:
            for error in pattern['errors']:
                # Normalize error message
                normalized = error.split(':')[0].strip()
                error_counter[normalized] += 1
        
        return [
            {'error': error, 'count': count}
            for error, count in error_counter.most_common(10)
        ]
