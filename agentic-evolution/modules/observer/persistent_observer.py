"""
Persistent Observer with File-Based Logging.

This observer writes all observations to disk for long-term memory and analysis.
Each batch is saved as a separate JSONL file for efficient access.
"""

import os
import json
from datetime import datetime, date
from typing import List, Dict, Any, Optional
from .observer import Observer


class SafeJSONEncoder(json.JSONEncoder):
    """
    Custom JSON encoder that handles non-serializable objects gracefully.
    
    This prevents crashes when tool outputs contain datetime objects or
    other non-JSON-serializable types.
    """
    
    def default(self, obj):
        # Handle datetime objects
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        
        # Handle bytes
        if isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        
        # Handle sets
        if isinstance(obj, set):
            return list(obj)
        
        # Handle any other non-serializable object
        try:
            return super().default(obj)
        except TypeError:
            # Last resort: convert to string representation
            return f"<{type(obj).__name__}: {str(obj)[:100]}>"


class PersistentObserver(Observer):
    """
    Observer that persists observations to filesystem for long-term analysis.
    
    Key features:
    - Writes each batch to a separate JSONL file
    - Maintains summary statistics for quick access
    - Supports field-level correctness tracking
    - Enables historical analysis by Proposer
    
    File structure:
        workspace_dir/
            observations/
                batch_001.jsonl
                batch_002.jsonl
                ...
            summary_stats.json
    """
    
    def __init__(self, workspace_dir: str):
        """
        Initialize persistent observer.
        
        Args:
            workspace_dir: Root directory for storing observations
        """
        self.workspace_dir = workspace_dir
        self.observations_dir = os.path.join(workspace_dir, "observations")
        os.makedirs(self.observations_dir, exist_ok=True)
        
        self.current_batch_id = 0
        self._initialize_summary_stats()
    
    def _initialize_summary_stats(self):
        """Initialize or load summary statistics."""
        summary_file = os.path.join(self.workspace_dir, "summary_stats.json")
        
        if os.path.exists(summary_file):
            with open(summary_file, 'r') as f:
                self.summary_stats = json.load(f)
        else:
            self.summary_stats = {
                "total_samples": 0,
                "total_batches": 0,
                "batch_accuracies": [],
                "field_accuracies_over_time": {},
                "error_type_counts": {},
                "created_at": datetime.utcnow().isoformat()
            }
            self._save_summary_stats()
    
    def observe(self, agent, observation_data, batch_id: Optional[int] = None):
        """
        Create observations and persist to disk.
        
        Args:
            agent: The agent to evaluate
            observation_data: List of dicts with "question", "answer", etc.
            batch_id: Optional batch ID for organizing observations
            
        Returns:
            List of observation dicts
        """
        # Use provided batch_id or auto-increment
        if batch_id is None:
            batch_id = self.current_batch_id
        else:
            self.current_batch_id = batch_id
        
        observations = []
        
        for item in observation_data:
            # Extract data
            sample_id = item.get("id", "unknown")
            input_text = item.get("question", "")
            answer_text = item.get("answer", "")
            
            # Get agent output
            output_text = agent.call(input_text)
            
            # Compute correctness
            is_correct = self._check_correctness(output_text, answer_text)
            field_correctness = self._check_field_correctness(output_text, answer_text)
            
            # Extract execution trace (tool calls, reasoning, etc.)
            execution_trace = self._extract_execution_trace(agent)
            
            # Create observation object
            obs = {
                "batch_id": batch_id,
                "sample_id": sample_id,
                "timestamp": datetime.utcnow().isoformat(),
                "input": input_text,
                "output": output_text,
                "answer": answer_text,
                "is_correct": is_correct,
                "field_correctness": field_correctness,
                # Preserve additional metadata
                "customer_id": item.get("customer_id"),
                "target_event_type": item.get("target_event_type"),
                # NEW: Add execution trace for analysis
                "execution_trace": execution_trace,
            }
            
            observations.append(obs)
        
        # Persist to disk
        self._write_batch_to_disk(observations, batch_id)
        
        # Update summary stats
        self._update_summary_stats(observations, batch_id)
        
        # Increment for next batch
        self.current_batch_id += 1
        
        return observations
    
    def _write_batch_to_disk(self, observations: List[Dict], batch_id: int):
        """Write observations to batch file in JSONL format."""
        batch_file = os.path.join(
            self.observations_dir, 
            f"batch_{batch_id:03d}.jsonl"
        )
        
        # Check if file exists to determine write mode
        # Use 'a' (append) if file exists, 'w' (write) if new
        file_exists = os.path.exists(batch_file)
        mode = 'a' if file_exists else 'w'
        
        with open(batch_file, mode) as f:
            for obs in observations:
                # Use SafeJSONEncoder to handle datetime and other non-serializable objects
                # This prevents crashes when tools return datetime objects
                f.write(json.dumps(obs, cls=SafeJSONEncoder) + '\n')
    
    def _update_summary_stats(self, observations: List[Dict], batch_id: int):
        """Update running statistics for quick access by Proposer."""
        # Calculate batch accuracy
        num_correct = sum(1 for obs in observations if obs.get("is_correct", False))
        batch_accuracy = num_correct / len(observations) if observations else 0.0
        
        # Update batch statistics
        self.summary_stats["total_samples"] += len(observations)
        self.summary_stats["total_batches"] += 1
        self.summary_stats["batch_accuracies"].append({
            "batch_id": batch_id,
            "accuracy": batch_accuracy,
            "num_samples": len(observations),
            "num_correct": num_correct,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        # Calculate field-level accuracy for this batch
        field_stats = self._compute_field_stats(observations)
        batch_id_str = str(batch_id)
        
        if "field_accuracies_over_time" not in self.summary_stats:
            self.summary_stats["field_accuracies_over_time"] = {}
        
        self.summary_stats["field_accuracies_over_time"][batch_id_str] = field_stats
        
        # Count error types
        for obs in observations:
            if not obs.get("is_correct", True):
                error_sig = self._get_error_signature(obs)
                if error_sig not in self.summary_stats["error_type_counts"]:
                    self.summary_stats["error_type_counts"][error_sig] = 0
                self.summary_stats["error_type_counts"][error_sig] += 1
        
        # Save to disk
        self._save_summary_stats()
    
    def _compute_field_stats(self, observations: List[Dict]) -> Dict[str, float]:
        """Compute field-level accuracy statistics."""
        field_correct = {}
        field_total = {}
        
        for obs in observations:
            field_correctness = obs.get("field_correctness", {})
            for field, is_correct in field_correctness.items():
                if field not in field_correct:
                    field_correct[field] = 0
                    field_total[field] = 0
                
                field_total[field] += 1
                if is_correct:
                    field_correct[field] += 1
        
        # Calculate accuracies
        field_accuracies = {}
        for field in field_total:
            if field_total[field] > 0:
                field_accuracies[field] = field_correct[field] / field_total[field]
            else:
                field_accuracies[field] = 0.0
        
        return field_accuracies
    
    def _save_summary_stats(self):
        """Save summary statistics to disk."""
        summary_file = os.path.join(self.workspace_dir, "summary_stats.json")
        with open(summary_file, 'w') as f:
            json.dump(self.summary_stats, f, indent=2)
    
    def _extract_execution_trace(self, agent) -> Dict[str, Any]:
        """
        Extract execution trace from agent for detailed analysis.
        
        This captures:
        - Tool calls made during execution
        - Multi-iteration dialogue (conversation history)
        - Reasoning/thinking steps at each iteration
        - Any errors or exceptions
        
        Args:
            agent: The agent that just executed
            
        Returns:
            Dict with execution trace information
        """
        trace = {
            "has_tools": False,
            "tool_calls": [],
            "num_tool_calls": 0,
            "num_iterations": 0,
            "conversation_history": [],  # NEW: capture reasoning at each step
            "errors": []
        }
        
        # Check if agent has tool execution capability
        if hasattr(agent, 'tool_executor') and agent.tool_executor is not None:
            trace["has_tools"] = True
            
            # Extract tool execution log from agent state
            if hasattr(agent, 'state') and 'tool_execution_log' in agent.state:
                tool_log = agent.state['tool_execution_log']
                
                if tool_log:
                    # Extract tool calls from log
                    for entry in tool_log:
                        if isinstance(entry, dict):
                            tool_call_info = {
                                "iteration": entry.get("iteration", 0),
                                "tool_name": entry.get("tool_name", "unknown"),
                                "arguments": entry.get("arguments", {}),
                                "result": entry.get("result"),
                                "success": entry.get("success", False),
                                "error": entry.get("error"),
                                # Include raw output for debugging
                                "raw_output": entry.get("raw_output", "")[:500],  # Limit size
                            }
                            trace["tool_calls"].append(tool_call_info)
                    
                    trace["num_tool_calls"] = len(trace["tool_calls"])
                    
                    # Count iterations (max iteration number + 1)
                    if trace["tool_calls"]:
                        max_iter = max(tc.get("iteration", 0) for tc in trace["tool_calls"])
                        trace["num_iterations"] = max_iter + 1
                
                # Clear the log for next call (avoid accumulation)
                agent.state['tool_execution_log'] = []
            
            # NEW: Extract conversation history (reasoning at each iteration)
            if hasattr(agent, 'state') and 'conversation_history' in agent.state:
                conv_history = agent.state['conversation_history']
                
                if conv_history:
                    for entry in conv_history:
                        conv_entry = {
                            "iteration": entry.get("iteration", 0),
                            "input": entry.get("input", "")[:500],  # Limit size (first iteration is full input)
                            "output": entry.get("output", "")[:1000],  # Agent's reasoning/response
                        }
                        trace["conversation_history"].append(conv_entry)
                
                # Clear for next call
                agent.state['conversation_history'] = []
        
        # Check for any stored errors or warnings
        if hasattr(agent, 'state'):
            if 'errors' in agent.state:
                trace["errors"] = agent.state.get('errors', [])
            
            # Extract reasoning/thoughts if available
            if 'reasoning' in agent.state:
                trace["reasoning"] = agent.state.get('reasoning', "")[:1000]  # Limit size
        
        return trace
    
    def _check_correctness(self, output: str, answer: str) -> bool:
        """
        Check if output matches answer.
        
        Uses simple normalization. Can be extended for:
        - Fuzzy matching
        - Semantic similarity
        - Task-specific metrics
        """
        # Normalize whitespace and case
        output_clean = ' '.join(output.strip().split()).lower()
        answer_clean = ' '.join(answer.strip().split()).lower()
        
        return output_clean == answer_clean
    
    def _check_field_correctness(self, output: str, answer: str) -> Dict[str, bool]:
        """
        Check correctness at field level for richer analysis.
        
        Parses prediction format: {timestamp, asin, brand_id, event_name, ...}
        """
        try:
            output_fields = self._parse_prediction_fields(output)
            answer_fields = self._parse_prediction_fields(answer)
            
            field_correctness = {}
            for field in output_fields:
                if field in answer_fields:
                    field_correctness[field] = (output_fields[field] == answer_fields[field])
                else:
                    field_correctness[field] = False
            
            return field_correctness
        except Exception:
            # If parsing fails, return empty dict
            return {}
    
    def _parse_prediction_fields(self, pred_str: str) -> Dict[str, str]:
        """
        Parse prediction string into field dict.
        
        Format: {timestamp, asin, brand_id, event_name, search_query, primary_tree, category_path}
        """
        # Remove braces and split by comma
        pred_str = pred_str.strip()
        if pred_str.startswith('{') and pred_str.endswith('}'):
            pred_str = pred_str[1:-1]
        
        parts = pred_str.split(',')
        
        # Map to field names
        field_names = [
            'timestamp', 'asin', 'brand_id', 'event_name',
            'search_query', 'primary_tree', 'category_path'
        ]
        
        fields = {}
        for i, field_name in enumerate(field_names):
            if i < len(parts):
                fields[field_name] = parts[i].strip()
            else:
                fields[field_name] = 'NULL'
        
        return fields
    
    def _get_error_signature(self, obs: Dict) -> str:
        """
        Extract error signature for categorization.
        
        This helps identify recurring error patterns.
        """
        field_correctness = obs.get("field_correctness", {})
        
        if not field_correctness:
            return "unknown_error"
        
        # Find incorrect fields
        incorrect_fields = [
            field for field, correct in field_correctness.items()
            if not correct
        ]
        
        if not incorrect_fields:
            return "unknown_error"
        
        # Sort for consistency
        incorrect_fields.sort()
        return f"incorrect_fields:{','.join(incorrect_fields)}"
    
    def get_summary_stats(self) -> Dict[str, Any]:
        """Get current summary statistics."""
        return self.summary_stats.copy()
    
    def get_batch_observations(self, batch_id: int) -> List[Dict]:
        """
        Load observations from a specific batch.
        
        Args:
            batch_id: The batch ID to load
            
        Returns:
            List of observation dicts
        """
        batch_file = os.path.join(
            self.observations_dir,
            f"batch_{batch_id:03d}.jsonl"
        )
        
        if not os.path.exists(batch_file):
            return []
        
        observations = []
        with open(batch_file, 'r') as f:
            for line in f:
                observations.append(json.loads(line))
        
        return observations

