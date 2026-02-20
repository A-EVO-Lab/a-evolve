"""
Analysis Tools for Proposer Agent.

These tools allow the Proposer to analyze historical observations
stored in the filesystem, enabling global perspective and deep reflection.
"""

import os
import json
import re
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict


class ProposerAnalysisTools:
    """
    Collection of analysis tools for Proposer to investigate learning history.
    
    These tools enable:
    - Searching through observation logs (grep-like)
    - Reading specific batch data
    - Computing statistics over time
    - Identifying persistent error patterns
    - Tracking tool/patch effectiveness
    """
    
    def __init__(self, workspace_dir: str):
        """
        Initialize analysis tools.
        
        Args:
            workspace_dir: Root directory where observations are stored
        """
        self.workspace_dir = workspace_dir
        self.observations_dir = os.path.join(workspace_dir, "observations")
        self.summary_stats_file = os.path.join(workspace_dir, "summary_stats.json")
    
    def grep_observations(
        self,
        pattern: str,
        case_sensitive: bool = False,
        max_results: int = 50
    ) -> Dict[str, Any]:
        """
        Search all observation logs for a pattern (grep-like functionality).
        
        Args:
            pattern: Search pattern (supports regex)
            case_sensitive: Whether to match case
            max_results: Maximum results to return
            
        Returns:
            Dict with matches and summary
        """
        if not os.path.exists(self.observations_dir):
            return {
                "error": "Observations directory not found",
                "num_matches": 0,
                "matches": []
            }
        
        matches = []
        flags = 0 if case_sensitive else re.IGNORECASE
        
        try:
            pattern_re = re.compile(pattern, flags)
        except re.error as e:
            return {
                "error": f"Invalid regex pattern: {e}",
                "num_matches": 0,
                "matches": []
            }
        
        # Search through all batch files
        batch_files = sorted([
            f for f in os.listdir(self.observations_dir)
            if f.endswith('.jsonl')
        ])
        
        for batch_file in batch_files:
            batch_path = os.path.join(self.observations_dir, batch_file)
            
            with open(batch_path, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        obs = json.loads(line)
                        obs_str = json.dumps(obs)
                        
                        if pattern_re.search(obs_str):
                            matches.append({
                                "batch_file": batch_file,
                                "batch_id": obs.get("batch_id"),
                                "sample_id": obs.get("sample_id"),
                                "line": line_num,
                                "is_correct": obs.get("is_correct"),
                                "snippet": obs_str[:200] + "..." if len(obs_str) > 200 else obs_str
                            })
                            
                            if len(matches) >= max_results:
                                break
                    except json.JSONDecodeError:
                        continue
            
            if len(matches) >= max_results:
                break
        
        # Compute summary
        batch_ids = set(m["batch_id"] for m in matches if m.get("batch_id") is not None)
        num_incorrect = sum(1 for m in matches if m.get("is_correct") is False)
        
        return {
            "num_matches": len(matches),
            "num_batches_affected": len(batch_ids),
            "num_incorrect": num_incorrect,
            "matches": matches,
            "summary": f"Found {len(matches)} matches across {len(batch_ids)} batches ({num_incorrect} incorrect)"
        }
    
    def read_batch_file(self, batch_id: int) -> Dict[str, Any]:
        """
        Read all observations from a specific batch.
        
        Args:
            batch_id: The batch ID to read
            
        Returns:
            Dict with batch observations and statistics
        """
        batch_file = os.path.join(
            self.observations_dir,
            f"batch_{batch_id:03d}.jsonl"
        )
        
        if not os.path.exists(batch_file):
            return {
                "error": f"Batch {batch_id} not found",
                "observations": []
            }
        
        observations = []
        with open(batch_file, 'r') as f:
            for line in f:
                try:
                    obs = json.loads(line)
                    observations.append(obs)
                except json.JSONDecodeError:
                    continue
        
        # Compute batch statistics
        num_correct = sum(1 for obs in observations if obs.get("is_correct", False))
        accuracy = num_correct / len(observations) if observations else 0.0
        
        # Field-level accuracy
        field_stats = self._compute_field_stats(observations)
        
        return {
            "batch_id": batch_id,
            "num_samples": len(observations),
            "num_correct": num_correct,
            "accuracy": accuracy,
            "field_accuracies": field_stats,
            "observations": observations
        }
    
    def compute_statistics(
        self,
        metric: str = "accuracy",
        time_range: str = "all"
    ) -> Dict[str, Any]:
        """
        Compute statistics over observations.
        
        Args:
            metric: Metric to compute ("accuracy", "field_accuracy", "error_rate")
            time_range: Time range ("all", "last_N_batches")
            
        Returns:
            Dict with computed statistics
        """
        if not os.path.exists(self.summary_stats_file):
            return {
                "error": "Summary statistics file not found",
                "metric": metric
            }
        
        with open(self.summary_stats_file, 'r') as f:
            summary = json.load(f)
        
        if metric == "accuracy":
            return self._compute_accuracy_stats(summary, time_range)
        elif metric == "field_accuracy":
            return self._compute_field_accuracy_stats(summary, time_range)
        elif metric == "error_rate":
            return self._compute_error_rate_stats(summary, time_range)
        else:
            return {
                "error": f"Unknown metric: {metric}",
                "available_metrics": ["accuracy", "field_accuracy", "error_rate"]
            }
    
    def _compute_accuracy_stats(
        self,
        summary: Dict,
        time_range: str
    ) -> Dict[str, Any]:
        """Compute accuracy statistics."""
        batch_accs = summary.get("batch_accuracies", [])
        
        if not batch_accs:
            return {"error": "No batch accuracy data available"}
        
        # Select batches based on time_range
        if time_range == "all":
            selected = batch_accs
        elif time_range.startswith("last_"):
            try:
                n = int(time_range.split("_")[1])
                selected = batch_accs[-n:]
            except (ValueError, IndexError):
                selected = batch_accs
        else:
            selected = batch_accs
        
        if not selected:
            return {"error": "No data for specified range"}
        
        accuracies = [b["accuracy"] for b in selected]
        
        # Compute trend
        if len(accuracies) >= 2:
            trend_direction = "improving" if accuracies[-1] > accuracies[0] else "declining"
            if abs(accuracies[-1] - accuracies[0]) < 0.02:
                trend_direction = "stable"
        else:
            trend_direction = "unknown"
        
        return {
            "metric": "accuracy",
            "time_range": time_range,
            "num_batches": len(accuracies),
            "mean": sum(accuracies) / len(accuracies),
            "min": min(accuracies),
            "max": max(accuracies),
            "latest": accuracies[-1],
            "first": accuracies[0],
            "trend": accuracies,
            "trend_direction": trend_direction,
            "improvement": accuracies[-1] - accuracies[0] if len(accuracies) >= 2 else 0
        }
    
    def _compute_field_accuracy_stats(
        self,
        summary: Dict,
        time_range: str
    ) -> Dict[str, Any]:
        """Compute field-level accuracy statistics."""
        field_accs_over_time = summary.get("field_accuracies_over_time", {})
        
        if not field_accs_over_time:
            return {"error": "No field accuracy data available"}
        
        # Aggregate field accuracies
        field_aggregates = defaultdict(list)
        
        for batch_id_str, field_accs in field_accs_over_time.items():
            for field, acc in field_accs.items():
                field_aggregates[field].append(acc)
        
        # Compute statistics for each field
        field_stats = {}
        for field, accs in field_aggregates.items():
            field_stats[field] = {
                "mean": sum(accs) / len(accs),
                "min": min(accs),
                "max": max(accs),
                "latest": accs[-1],
                "trend": accs[-5:] if len(accs) > 5 else accs  # Last 5 batches
            }
        
        return {
            "metric": "field_accuracy",
            "time_range": time_range,
            "fields": field_stats
        }
    
    def _compute_error_rate_stats(
        self,
        summary: Dict,
        time_range: str
    ) -> Dict[str, Any]:
        """Compute error rate statistics."""
        error_counts = summary.get("error_type_counts", {})
        
        if not error_counts:
            return {
                "metric": "error_rate",
                "total_errors": 0,
                "error_types": {}
            }
        
        total_errors = sum(error_counts.values())
        
        # Sort by frequency
        sorted_errors = sorted(
            error_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        return {
            "metric": "error_rate",
            "total_errors": total_errors,
            "num_error_types": len(error_counts),
            "top_errors": [
                {"type": err_type, "count": count, "percentage": count / total_errors * 100}
                for err_type, count in sorted_errors[:10]
            ],
            "all_errors": dict(sorted_errors)
        }
    
    def analyze_error_patterns(
        self,
        min_occurrences: int = 3,
        min_batches: int = 2
    ) -> Dict[str, Any]:
        """
        Find recurring error patterns across batches.
        
        Args:
            min_occurrences: Minimum total occurrences to report
            min_batches: Minimum number of batches to appear in
            
        Returns:
            Dict with persistent error patterns
        """
        if not os.path.exists(self.observations_dir):
            return {"error": "Observations directory not found"}
        
        error_patterns = defaultdict(lambda: {
            "count": 0,
            "batches": set(),
            "examples": []
        })
        
        # Scan all batch files
        batch_files = sorted([
            f for f in os.listdir(self.observations_dir)
            if f.endswith('.jsonl')
        ])
        
        for batch_file in batch_files:
            batch_path = os.path.join(self.observations_dir, batch_file)
            
            with open(batch_path, 'r') as f:
                for line in f:
                    try:
                        obs = json.loads(line)
                        
                        if not obs.get("is_correct", True):
                            # Get error signature
                            error_sig = self._get_error_signature(obs)
                            batch_id = obs.get("batch_id", "unknown")
                            
                            error_patterns[error_sig]["count"] += 1
                            error_patterns[error_sig]["batches"].add(batch_id)
                            
                            # Store example (limit to 3)
                            if len(error_patterns[error_sig]["examples"]) < 3:
                                error_patterns[error_sig]["examples"].append({
                                    "batch_id": batch_id,
                                    "sample_id": obs.get("sample_id"),
                                    "output": obs.get("output", "")[:100],
                                    "expected": obs.get("answer", "")[:100]
                                })
                    except json.JSONDecodeError:
                        continue
        
        # Filter by criteria
        persistent_errors = {}
        for pattern, data in error_patterns.items():
            if (data["count"] >= min_occurrences and 
                len(data["batches"]) >= min_batches):
                persistent_errors[pattern] = {
                    "count": data["count"],
                    "num_batches": len(data["batches"]),
                    "batches": sorted(list(data["batches"])),
                    "examples": data["examples"],
                    "severity": "high" if data["count"] > 10 else "medium"
                }
        
        return {
            "total_error_types": len(persistent_errors),
            "patterns": persistent_errors,
            "summary": f"Found {len(persistent_errors)} persistent error patterns"
        }
    
    def _get_error_signature(self, obs: Dict) -> str:
        """Extract error signature for categorization."""
        error_details = obs.get("error_details", [])
        if error_details:
            detail = error_details[0]
            detail_type = detail.get("type", "unknown")
            if detail_type == "http_status":
                return f"http_status:{detail.get('status_code', 'unknown')}"
            if detail_type == "api_not_found":
                app_name = detail.get("app_name", "unknown")
                api_name = detail.get("api_name", "unknown")
                return f"api_not_found:{app_name}.{api_name}"
            if detail_type == "app_not_found":
                return f"app_not_found:{detail.get('app_name', 'unknown')}"
            if detail_type == "name_error":
                return f"name_error:{detail.get('name', 'unknown')}"
            if detail_type == "tool_call_literal":
                return "tool_call_literal"
            return detail_type

        field_correctness = obs.get("field_correctness", {})
        
        if not field_correctness:
            # Fall back to text errors if available
            errors = obs.get("errors", [])
            if errors:
                return errors[0].split(':')[0].strip()
            return "unknown_error"
        
        incorrect_fields = [
            field for field, correct in field_correctness.items()
            if not correct
        ]
        
        if not incorrect_fields:
            return "unknown_error"
        
        incorrect_fields.sort()
        return f"incorrect:{','.join(incorrect_fields)}"
    
    def _compute_field_stats(self, observations: List[Dict]) -> Dict[str, float]:
        """Compute field-level accuracy for a set of observations."""
        field_correct = defaultdict(int)
        field_total = defaultdict(int)
        
        for obs in observations:
            field_correctness = obs.get("field_correctness", {})
            for field, is_correct in field_correctness.items():
                field_total[field] += 1
                if is_correct:
                    field_correct[field] += 1
        
        field_accuracies = {}
        for field in field_total:
            if field_total[field] > 0:
                field_accuracies[field] = field_correct[field] / field_total[field]
        
        return field_accuracies
    
    def analyze_api_sequences(
        self,
        min_occurrences: int = 2,
        focus_api: Optional[str] = None,
        include_errors: bool = True
    ) -> Dict[str, Any]:
        """
        Analyze API call sequence patterns (A → B) from observations.
        
        Identifies common patterns like:
        - "After login, typically call show_*"
        - "Before complete_task, usually query X"
        - "Error recovery sequences"
        
        Args:
            min_occurrences: Minimum times a pattern must occur to be included
            focus_api: If provided, only analyze sequences involving this API
            include_errors: Whether to include error-related sequences
            
        Returns:
            Dict with sequence pattern analysis
        """
        from collections import Counter
        
        if not os.path.exists(self.observations_dir):
            return {"error": "Observations directory not found"}
        
        # Collect all sequences
        sequence_pairs = []  # (A, B) pairs
        post_error_sequences = []  # Sequences after errors
        success_sequences = []
        failure_sequences = []
        
        batch_files = sorted([
            f for f in os.listdir(self.observations_dir)
            if f.endswith('.jsonl')
        ])
        
        for batch_file in batch_files:
            batch_path = os.path.join(self.observations_dir, batch_file)
            
            with open(batch_path, 'r') as f:
                for line in f:
                    try:
                        obs = json.loads(line)
                        api_calls = obs.get('api_calls', [])
                        is_success = obs.get('score', 0) >= 0.5
                        errors = obs.get('errors', [])
                        
                        # Extract consecutive pairs
                        for i in range(len(api_calls) - 1):
                            a, b = api_calls[i], api_calls[i+1]
                            
                            # Filter by focus API if specified
                            if focus_api and focus_api not in a and focus_api not in b:
                                continue
                            
                            pair = (a, b)
                            sequence_pairs.append(pair)
                            
                            if is_success:
                                success_sequences.append(pair)
                            else:
                                failure_sequences.append(pair)
                        
                        # Track post-error patterns
                        if include_errors and errors:
                            for i, call in enumerate(api_calls):
                                if i > 0:  # Has a preceding call
                                    post_error_sequences.append((api_calls[i-1], call))
                                    
                    except json.JSONDecodeError:
                        continue
        
        # Count frequencies
        pair_counts = Counter(sequence_pairs)
        success_counts = Counter(success_sequences)
        failure_counts = Counter(failure_sequences)
        
        # Filter by min_occurrences
        filtered_pairs = {
            pair: count 
            for pair, count in pair_counts.items() 
            if count >= min_occurrences
        }
        
        # Compute success rate per sequence
        sequence_success_rates = {}
        for pair, count in filtered_pairs.items():
            succ = success_counts.get(pair, 0)
            fail = failure_counts.get(pair, 0)
            total = succ + fail
            if total > 0:
                sequence_success_rates[f"{pair[0]} → {pair[1]}"] = {
                    "count": count,
                    "success_rate": succ / total,
                    "success_count": succ,
                    "failure_count": fail
                }
        
        # Find "after X" patterns
        after_patterns = defaultdict(list)
        for (a, b), count in filtered_pairs.items():
            after_patterns[a].append({"next_api": b, "count": count})
        
        # Sort after_patterns by count
        for api in after_patterns:
            after_patterns[api] = sorted(
                after_patterns[api], 
                key=lambda x: x['count'], 
                reverse=True
            )
        
        # Identify high-success vs high-failure sequences
        high_success = [
            (seq, data) for seq, data in sequence_success_rates.items()
            if data['success_rate'] >= 0.8 and data['count'] >= min_occurrences
        ]
        high_failure = [
            (seq, data) for seq, data in sequence_success_rates.items()
            if data['success_rate'] <= 0.3 and data['count'] >= min_occurrences
        ]
        
        return {
            "total_unique_sequences": len(filtered_pairs),
            "total_sequence_instances": len(sequence_pairs),
            "top_sequences": [
                {"sequence": f"{a} → {b}", "count": c}
                for (a, b), c in sorted(filtered_pairs.items(), key=lambda x: -x[1])[:15]
            ],
            "after_patterns": dict(after_patterns),
            "high_success_sequences": sorted(high_success, key=lambda x: -x[1]['count'])[:10],
            "high_failure_sequences": sorted(high_failure, key=lambda x: -x[1]['count'])[:10],
            "sequence_success_rates": sequence_success_rates,
            "summary": f"Found {len(filtered_pairs)} recurring API sequence patterns"
        }
    
    def get_tool_registry(self) -> Dict[str, Any]:
        """
        Read tool registry to understand available tools.
        
        Returns:
            Dict with tool information
        """
        registry_file = os.path.join(self.workspace_dir, "registry.json")
        
        if not os.path.exists(registry_file):
            return {
                "error": "Tool registry not found",
                "tools": []
            }
        
        with open(registry_file, 'r') as f:
            registry = json.load(f)
        
        tools = registry.get("tools", [])
        
        return {
            "num_tools": len(tools),
            "tools": tools
        }
    
    def get_patches(self) -> Dict[str, Any]:
        """
        Read patches file to understand current patches.
        
        Returns:
            Dict with patch information
        """
        patches_file = os.path.join(self.workspace_dir, "patches.json")
        
        if not os.path.exists(patches_file):
            return {
                "error": "Patches file not found",
                "patches": []
            }
        
        with open(patches_file, 'r') as f:
            patches = json.load(f)
        
        return {
            "num_patches": len(patches),
            "patches": patches
        }
    
    def read_tool_code(self, tool_name: str) -> Dict[str, Any]:
        """
        Read the full source code of a generated tool.
        
        This is essential for the Proposer to understand what a tool actually does
        before deciding to evolve it.
        
        Args:
            tool_name: Name of the tool to read
            
        Returns:
            Dict with tool metadata and full source code
        """
        registry_data = self.get_tool_registry()
        
        if "error" in registry_data:
            return {"error": registry_data["error"]}
        
        # Find the tool
        tool_info = None
        for tool in registry_data.get("tools", []):
            if tool.get("name") == tool_name or tool.get("id", "").endswith(tool_name):
                tool_info = tool
                break
        
        if not tool_info:
            return {
                "error": f"Tool '{tool_name}' not found",
                "available_tools": [t.get("name") for t in registry_data.get("tools", [])]
            }
        
        # Read the source code
        file_path = tool_info.get("file_path", "")
        code = ""
        
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    code = f.read()
            except Exception as e:
                code = f"# Error reading code: {str(e)}"
        else:
            code = "# Code file not found"
        
        return {
            "name": tool_info.get("name"),
            "description": tool_info.get("description"),
            "parameters": tool_info.get("parameters"),
            "return_type": tool_info.get("return_type"),
            "code": code,
            "file_path": file_path,
            "usage_count": tool_info.get("usage_count", 0),
            "success_rate": tool_info.get("success_rate", 1.0),
            "created_at": tool_info.get("created_at")
        }
    
    def test_tool_execution(
        self,
        tool_name: str,
        test_input: Dict[str, Any],
        timeout: int = 5
    ) -> Dict[str, Any]:
        """
        Test a tool with sample input to verify it works correctly.
        
        This allows the Proposer to validate that a tool:
        1. Can be loaded without errors
        2. Returns non-empty results for valid input
        3. Doesn't throw exceptions
        
        Args:
            tool_name: Name of the tool to test
            test_input: Input parameters to pass to the tool
            timeout: Maximum execution time in seconds
            
        Returns:
            Dict with execution result, including output and any errors
        """
        import importlib.util
        import sys
        import signal
        
        # Get tool info
        tool_data = self.read_tool_code(tool_name)
        if "error" in tool_data and tool_data.get("code") is None:
            return {"error": tool_data["error"], "success": False}
        
        file_path = tool_data.get("file_path", "")
        if not file_path or not os.path.exists(file_path):
            return {"error": f"Tool file not found: {file_path}", "success": False}
        
        try:
            # Load the module dynamically
            spec = importlib.util.spec_from_file_location(tool_name, file_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[tool_name] = module
            spec.loader.exec_module(module)
            
            # Get the function
            func = getattr(module, tool_name, None)
            if func is None:
                return {
                    "error": f"Function '{tool_name}' not found in module",
                    "success": False,
                    "available_functions": [name for name in dir(module) if not name.startswith('_')]
                }
            
            # Execute with timeout
            result = func(**test_input)
            
            # Analyze result quality
            result_quality = self._analyze_tool_result(result)
            
            return {
                "success": True,
                "result": result,
                "result_type": type(result).__name__,
                "result_quality": result_quality,
                "is_empty": result_quality.get("is_empty", True),
                "error": None
            }
            
        except Exception as e:
            import traceback
            return {
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
                "result": None
            }
    
    def _analyze_tool_result(self, result: Any) -> Dict[str, Any]:
        """
        Analyze the quality of a tool's output.
        
        Checks for common issues like:
        - Empty results ({}, [], None)
        - Missing expected fields
        - Error indicators in the result
        """
        if result is None:
            return {"is_empty": True, "reason": "Result is None"}
        
        if isinstance(result, dict):
            if len(result) == 0:
                return {"is_empty": True, "reason": "Result is empty dict {}"}
            
            # Check if it's an error result
            if "error" in result:
                return {
                    "is_empty": False,
                    "has_error": True,
                    "error_message": result.get("error"),
                    "reason": "Result contains error field"
                }
            
            # Count meaningful values
            meaningful_keys = [k for k, v in result.items() if v is not None and v != "" and v != [] and v != {}]
            
            return {
                "is_empty": len(meaningful_keys) == 0,
                "num_fields": len(result),
                "meaningful_fields": len(meaningful_keys),
                "field_names": list(result.keys())[:10],  # Show first 10 keys
                "reason": "Dict with data" if meaningful_keys else "All fields empty"
            }
        
        if isinstance(result, list):
            if len(result) == 0:
                return {"is_empty": True, "reason": "Result is empty list []"}
            return {
                "is_empty": False,
                "num_items": len(result),
                "reason": f"List with {len(result)} items"
            }
        
        if isinstance(result, str):
            if len(result.strip()) == 0:
                return {"is_empty": True, "reason": "Result is empty string"}
            return {
                "is_empty": False,
                "length": len(result),
                "reason": f"String with {len(result)} characters"
            }
        
        # For other types (numbers, bools, etc.)
        return {
            "is_empty": False,
            "type": type(result).__name__,
            "reason": f"Primitive value: {result}"
        }
    
    def analyze_tool_usage_in_batch(self, batch_id: int) -> Dict[str, Any]:
        """
        Analyze how tools were used in a specific batch.
        
        This provides critical feedback on:
        - Which tools were called
        - What inputs they received
        - What outputs they produced
        - Whether the outputs were actually useful
        
        Args:
            batch_id: The batch to analyze
            
        Returns:
            Detailed analysis of tool usage in that batch
        """
        batch_data = self.read_batch_file(batch_id)
        
        if "error" in batch_data:
            return batch_data
        
        tool_usage_analysis = {
            "batch_id": batch_id,
            "total_samples": len(batch_data.get("observations", [])),
            "tool_calls": [],
            "tools_used": {},
            "empty_results_count": 0,
            "error_results_count": 0,
            "summary": {}
        }
        
        for obs in batch_data.get("observations", []):
            exec_trace = obs.get("execution_trace", {})
            tool_calls = exec_trace.get("tool_calls", [])
            
            for tc in tool_calls:
                tool_name = tc.get("tool_name", "unknown")
                tool_result = tc.get("result", {})
                tool_input = tc.get("input", {})
                
                # Analyze result quality
                result_quality = self._analyze_tool_result(tool_result)
                
                call_analysis = {
                    "sample_id": obs.get("sample_id"),
                    "tool_name": tool_name,
                    "input_summary": str(tool_input)[:200] if tool_input else "No input",
                    "result": tool_result,
                    "result_quality": result_quality,
                    "sample_correct": obs.get("is_correct", False)
                }
                
                tool_usage_analysis["tool_calls"].append(call_analysis)
                
                # Update tool-level summary
                if tool_name not in tool_usage_analysis["tools_used"]:
                    tool_usage_analysis["tools_used"][tool_name] = {
                        "call_count": 0,
                        "empty_results": 0,
                        "error_results": 0,
                        "samples_correct": 0,
                        "samples_incorrect": 0
                    }
                
                tool_summary = tool_usage_analysis["tools_used"][tool_name]
                tool_summary["call_count"] += 1
                
                if result_quality.get("is_empty"):
                    tool_summary["empty_results"] += 1
                    tool_usage_analysis["empty_results_count"] += 1
                
                if result_quality.get("has_error"):
                    tool_summary["error_results"] += 1
                    tool_usage_analysis["error_results_count"] += 1
                
                if obs.get("is_correct"):
                    tool_summary["samples_correct"] += 1
                else:
                    tool_summary["samples_incorrect"] += 1
        
        # Generate summary
        tool_usage_analysis["summary"] = {
            "total_tool_calls": len(tool_usage_analysis["tool_calls"]),
            "unique_tools": list(tool_usage_analysis["tools_used"].keys()),
            "empty_result_rate": (
                tool_usage_analysis["empty_results_count"] / len(tool_usage_analysis["tool_calls"])
                if tool_usage_analysis["tool_calls"] else 0
            ),
            "tools_needing_attention": [
                name for name, data in tool_usage_analysis["tools_used"].items()
                if data["empty_results"] > 0 or data["error_results"] > 0
            ]
        }
        
        return tool_usage_analysis
    
    # ==================== Skill Analysis Tools ====================
    
    def get_skills(self) -> Dict[str, Any]:
        """
        List all skills from skills directory.
        
        Returns:
            Dict with skill list and metadata
        """
        skills_dir = os.path.join(self.workspace_dir, "skills")
        
        if not os.path.exists(skills_dir):
            return {
                "error": "Skills directory not found",
                "num_skills": 0,
                "skills": []
            }
        
        skills = []
        for filename in os.listdir(skills_dir):
            if filename.endswith('.md'):
                skill_path = os.path.join(skills_dir, filename)
                skill_data = self._parse_skill_file(skill_path)
                if skill_data:
                    skills.append({
                        "name": skill_data.get("name"),
                        "description": skill_data.get("description", "")[:100],
                        "triggers": skill_data.get("triggers", []),
                        "version": skill_data.get("version", 1),
                        "file": filename
                    })
        
        return {
            "num_skills": len(skills),
            "skills": skills,
            "skills_dir": skills_dir
        }
    
    def read_skill_content(self, skill_name: str) -> Dict[str, Any]:
        """
        Read the full content of a specific skill.
        
        Args:
            skill_name: Name of the skill to read
            
        Returns:
            Dict with full skill content
        """
        skills_dir = os.path.join(self.workspace_dir, "skills")
        
        # Try exact filename match
        safe_name = skill_name.replace(" ", "_").replace("/", "_").lower()
        skill_file = os.path.join(skills_dir, f"{safe_name}.md")
        
        if not os.path.exists(skill_file):
            # Try to find by listing
            if os.path.exists(skills_dir):
                available = [f[:-3] for f in os.listdir(skills_dir) if f.endswith('.md')]
                return {
                    "error": f"Skill '{skill_name}' not found",
                    "available_skills": available
                }
            return {"error": "Skills directory not found"}
        
        return self._parse_skill_file(skill_file)
    
    def _parse_skill_file(self, filepath: str) -> Dict[str, Any]:
        """Parse a skill markdown file."""
        try:
            with open(filepath, 'r') as f:
                content = f.read()
        except Exception as e:
            return {"error": f"Failed to read skill: {e}"}
        
        # Extract skill name from # header
        name_match = re.search(r'^# (.+)$', content, re.MULTILINE)
        skill_name = name_match.group(1).strip() if name_match else os.path.basename(filepath)[:-3]
        
        # Extract description
        desc_match = re.search(r'\*\*Description:\*\* ([^\n]+)', content)
        description = desc_match.group(1) if desc_match else ''
        
        # Extract triggers
        triggers_match = re.search(r'\*\*When to use:\*\* ([^\n]+)', content)
        triggers = triggers_match.group(1).split(', ') if triggers_match else []
        
        # Extract instructions
        instr_match = re.search(r'## Instructions\s*\n(.*?)(?=\n---|\Z)', content, re.DOTALL)
        instructions = instr_match.group(1).strip() if instr_match else ''
        
        # Extract version
        version_match = re.search(r'\*Version: (\d+)\*', content)
        version = int(version_match.group(1)) if version_match else 1
        
        return {
            "name": skill_name,
            "description": description,
            "triggers": triggers,
            "instructions": instructions,
            "version": version,
            "file_path": filepath,
            "content_length": len(content)
        }
    
    # ==================== Knowledge Analysis Tools ====================
    
    def get_knowledge(self) -> Dict[str, Any]:
        """
        List all knowledge entries from knowledge.json.
        
        Returns:
            Dict with knowledge entries and metadata
        """
        knowledge_file = os.path.join(self.workspace_dir, "knowledge.json")
        
        if not os.path.exists(knowledge_file):
            return {
                "error": "Knowledge file not found",
                "num_entries": 0,
                "entries": []
            }
        
        try:
            with open(knowledge_file, 'r') as f:
                knowledge = json.load(f)
        except Exception as e:
            return {"error": f"Failed to read knowledge: {e}"}
        
        # Summarize entries
        entries_summary = []
        for entry in knowledge:
            entries_summary.append({
                "id": entry.get("id"),
                "topic": entry.get("topic"),
                "type": entry.get("type", "general"),
                "content_preview": str(entry.get("content", ""))[:100]
            })
        
        return {
            "num_entries": len(knowledge),
            "entries": entries_summary,
            "knowledge_file": knowledge_file
        }
    
    def read_knowledge_entry(self, topic: str = None, entry_id: str = None) -> Dict[str, Any]:
        """
        Read a specific knowledge entry by topic or ID.
        
        Args:
            topic: Topic to search for
            entry_id: Entry ID to search for
            
        Returns:
            Dict with full knowledge entry
        """
        knowledge_file = os.path.join(self.workspace_dir, "knowledge.json")
        
        if not os.path.exists(knowledge_file):
            return {"error": "Knowledge file not found"}
        
        try:
            with open(knowledge_file, 'r') as f:
                knowledge = json.load(f)
        except Exception as e:
            return {"error": f"Failed to read knowledge: {e}"}
        
        # Search by ID or topic
        for entry in knowledge:
            if entry_id and entry.get("id") == entry_id:
                return entry
            if topic and topic.lower() in entry.get("topic", "").lower():
                return entry
        
        available_topics = [e.get("topic") for e in knowledge if e.get("topic")]
        return {
            "error": f"Knowledge entry not found",
            "available_topics": available_topics
        }
    
    def analyze_skill_coverage(self) -> Dict[str, Any]:
        """
        Analyze skill coverage against observed error patterns.
        
        Returns:
            Dict with coverage analysis and gap recommendations
        """
        # Get current skills
        skills_data = self.get_skills()
        skills = {s["name"]: s for s in skills_data.get("skills", [])}
        
        # Get error patterns
        error_patterns = self.analyze_error_patterns(min_occurrences=2, min_batches=1)
        patterns = error_patterns.get("patterns", {})
        
        # Analyze coverage
        covered_patterns = []
        uncovered_patterns = []
        
        for pattern, data in patterns.items():
            # Check if any skill addresses this pattern
            is_covered = False
            covering_skill = None
            
            for skill_name, skill_info in skills.items():
                triggers = skill_info.get("triggers", [])
                # Simple keyword matching
                pattern_lower = pattern.lower()
                for trigger in triggers:
                    if any(word in pattern_lower for word in trigger.lower().split()):
                        is_covered = True
                        covering_skill = skill_name
                        break
                if is_covered:
                    break
            
            if is_covered:
                covered_patterns.append({
                    "pattern": pattern,
                    "count": data["count"],
                    "skill": covering_skill
                })
            else:
                uncovered_patterns.append({
                    "pattern": pattern,
                    "count": data["count"],
                    "severity": data.get("severity", "medium")
                })
        
        return {
            "num_skills": len(skills),
            "num_patterns": len(patterns),
            "covered_patterns": len(covered_patterns),
            "uncovered_patterns": len(uncovered_patterns),
            "coverage_rate": len(covered_patterns) / len(patterns) if patterns else 1.0,
            "gaps": uncovered_patterns[:5],  # Top 5 gaps
            "recommendations": [
                f"Consider adding skill for: {p['pattern']}" 
                for p in sorted(uncovered_patterns, key=lambda x: x["count"], reverse=True)[:3]
            ]
        }
