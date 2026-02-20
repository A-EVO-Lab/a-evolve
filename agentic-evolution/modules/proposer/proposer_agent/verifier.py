"""
Verifier Module - Stage 4 of the Proposer Agent.

Verifies that applied changes work correctly:
1. Syntax validation of new tool files
2. Test execution with sample data
3. Full agent execution test
"""

import os
import sys
import json
import importlib.util
import traceback
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class VerificationResult:
    """Result of verification."""
    success: bool
    stage: str  # "syntax", "tool_test", "agent_test"
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    error_log: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "stage": self.stage,
            "message": self.message,
            "details": self.details,
            "error_log": self.error_log
        }


class VerifierModule:
    """
    Stage 4: Verification and Testing.
    
    This module:
    1. Validates syntax of new tool files
    2. Tests tool execution with sample input
    3. Tests full agent execution with a sample
    4. Returns detailed logs for debugging
    """
    
    def __init__(self, verbose: bool = True):
        """
        Initialize the verifier module.
        
        Args:
            verbose: Whether to print debug output
        """
        self.verbose = verbose
    
    def run(
        self,
        agent,
        applied_changes: List[Dict],
        observations: List[Dict],
        context: Dict[str, Any]
    ) -> VerificationResult:
        """
        Run Stage 4: Verification.
        
        Args:
            agent: The updated agent to test
            applied_changes: List of changes that were applied
            observations: Current batch observations (for sample test)
            context: Shared context with tool_registry, etc.
            
        Returns:
            VerificationResult with success status and logs
        """
        if self.verbose:
            print("\n[Verifier] ✅ Stage 4: Verification...")
        
        # Stage 4-pre: Check for any failed changes in apply stage
        failed_changes = [c for c in applied_changes if not c.get("success", True)]
        if failed_changes:
            if self.verbose:
                print(f"[Verifier]   ⚠️ Found {len(failed_changes)} failed change(s) from apply stage:")
                for fc in failed_changes:
                    print(f"[Verifier]     ❌ {fc.get('action', 'unknown')}: {fc.get('error', 'Unknown error')}")
            
            return VerificationResult(
                success=False,
                stage="apply_check",
                message=f"{len(failed_changes)} change(s) failed in apply stage",
                details={"failed_changes": failed_changes},
                error_log="\n".join([f"{c.get('action')}: {c.get('error')}" for c in failed_changes])
            )
        
        # Stage 4a: Validate syntax of new/updated tools
        syntax_result = self._verify_tool_syntax(applied_changes, context)
        if not syntax_result.success:
            return syntax_result
        
        # Stage 4b: Test tool execution
        tool_test_result = self._verify_tool_execution(applied_changes, observations, context)
        if not tool_test_result.success:
            return tool_test_result
        
        # Stage 4c: Test agent execution with sample
        agent_test_result = self._verify_agent_execution(agent, observations)
        if not agent_test_result.success:
            return agent_test_result
        
        # All verifications passed
        return VerificationResult(
            success=True,
            stage="complete",
            message="All verification stages passed",
            details={
                "syntax_check": syntax_result.to_dict(),
                "tool_test": tool_test_result.to_dict(),
                "agent_test": agent_test_result.to_dict()
            }
        )
    
    def _verify_tool_syntax(
        self,
        applied_changes: List[Dict],
        context: Dict[str, Any]
    ) -> VerificationResult:
        """Verify syntax of newly created/updated tools."""
        if self.verbose:
            print("[Verifier]   🔍 Checking tool syntax...")
        
        tool_registry = context.get("tool_registry")
        errors = []
        
        for change in applied_changes:
            if not change.get("success"):
                continue
            
            action = change.get("action", "")
            if action not in ["CREATE_TOOL", "EVOLVE_TOOL"]:
                continue
            
            file_path = change.get("file_path")
            tool_name = change.get("tool_name", "unknown")
            
            if not file_path or not os.path.exists(file_path):
                errors.append({
                    "tool": tool_name,
                    "error": f"Tool file not found: {file_path}",
                    "type": "file_missing"
                })
                continue
            
            # Read and compile the code
            try:
                with open(file_path, 'r') as f:
                    code = f.read()
                
                # Compile to check syntax
                compile(code, file_path, 'exec')
                
                if self.verbose:
                    print(f"[Verifier]     ✅ {tool_name}: Syntax OK")
                
            except SyntaxError as e:
                error_detail = {
                    "tool": tool_name,
                    "error": str(e),
                    "type": "syntax_error",
                    "line": e.lineno,
                    "offset": e.offset,
                    "text": e.text
                }
                errors.append(error_detail)
                
                if self.verbose:
                    print(f"[Verifier]     ❌ {tool_name}: Syntax error at line {e.lineno}")
            
            except Exception as e:
                errors.append({
                    "tool": tool_name,
                    "error": str(e),
                    "type": "compilation_error"
                })
        
        if errors:
            return VerificationResult(
                success=False,
                stage="syntax",
                message=f"Syntax errors in {len(errors)} tool(s)",
                details={"errors": errors},
                error_log=json.dumps(errors, indent=2)
            )
        
        return VerificationResult(
            success=True,
            stage="syntax",
            message="All tools have valid syntax",
            details={"tools_checked": len([c for c in applied_changes if c.get("action") in ["CREATE_TOOL", "EVOLVE_TOOL"]])}
        )
    
    def _verify_tool_execution(
        self,
        applied_changes: List[Dict],
        observations: List[Dict],
        context: Dict[str, Any]
    ) -> VerificationResult:
        """Verify that tools can execute with sample input."""
        if self.verbose:
            print("[Verifier]   🧪 Testing tool execution...")
        
        # Get sample input from observations
        sample_input = self._get_sample_input(observations)
        input_variants = self._build_input_variants(sample_input)
        
        tool_registry = context.get("tool_registry")
        tools_to_test = []
        
        # Always test newly created/evolved tools
        for change in applied_changes:
            if not change.get("success"):
                continue
            if change.get("action") in ["CREATE_TOOL", "EVOLVE_TOOL"]:
                tools_to_test.append({
                    "name": change.get("tool_name", "unknown"),
                    "file_path": change.get("file_path")
                })
        
        # Also test existing registered tools to catch regressions (e.g., legacy tools still used)
        if tool_registry:
            for t in tool_registry.list_tools():
                tools_to_test.append({
                    "name": t.get("name", "unknown"),
                    "file_path": t.get("file_path")
                })
        
        # Deduplicate by file_path
        deduped = {}
        for t in tools_to_test:
            fp = t.get("file_path")
            if fp and fp not in deduped:
                deduped[fp] = t
        tools_to_test = list(deduped.values())
        
        errors = []
        successes = []
        
        for tool in tools_to_test:
            file_path = tool.get("file_path")
            tool_name = tool.get("name", "unknown")
            
            if not file_path or not os.path.exists(file_path):
                continue
            
            # Try to load and execute the tool with multiple input variants
            result = self._test_tool(tool_name, file_path, input_variants)
            
            if result["success"]:
                successes.append({
                    "tool": tool_name,
                    "output_type": result.get("output_type"),
                    "output_keys": result.get("output_keys", []),
                    "variants_passed": result.get("variants_passed", 0),
                    "variants_failed": result.get("variants_failed", [])
                })
                if self.verbose:
                    print(f"[Verifier]     ✅ {tool_name}: Execution OK ({result.get('variants_passed', 0)} variants)")
            else:
                errors.append({
                    "tool": tool_name,
                    "error": result.get("error"),
                    "traceback": result.get("traceback"),
                    "variants_failed": result.get("variants_failed", [])
                })
                if self.verbose:
                    print(f"[Verifier]     ❌ {tool_name}: Execution failed - {result.get('error', 'Unknown')[:80]}")
        
        if errors:
            return VerificationResult(
                success=False,
                stage="tool_test",
                message=f"Execution errors in {len(errors)} tool(s)",
                details={"errors": errors, "successes": successes},
                error_log=json.dumps(errors, indent=2)
            )
        
        return VerificationResult(
            success=True,
            stage="tool_test",
            message=f"All {len(successes)} tools executed successfully",
            details={"successes": successes}
        )
    
    def _test_tool(
        self,
        tool_name: str,
        file_path: str,
        input_variants: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Test a single tool with multiple input variants."""
        try:
            # Load the module
            spec = importlib.util.spec_from_file_location(tool_name, file_path)
            if spec is None:
                return {"success": False, "error": "Could not load module spec"}
            
            module = importlib.util.module_from_spec(spec)
            sys.modules[tool_name] = module
            spec.loader.exec_module(module)
            
            # Get the function
            func = getattr(module, tool_name, None)
            if func is None:
                # Try to find any function in the module
                funcs = [n for n in dir(module) if callable(getattr(module, n)) and not n.startswith('_')]
                if funcs:
                    func = getattr(module, funcs[0])
                else:
                    return {"success": False, "error": f"Function '{tool_name}' not found"}
            
            import inspect
            sig = inspect.signature(func)
            params = list(sig.parameters.keys())
            
            # Choose variants based on first parameter semantics
            param_variants = input_variants
            if params:
                p0 = params[0].lower()
                if "events_data" in p0:
                    param_variants = [v for v in input_variants if v.get("name") == "raw_string"] or input_variants
                elif "events" in p0:
                    # Prefer list variants; fall back to raw string if no list available
                    preferred = [v for v in input_variants if v.get("name") in ("list_of_dicts", "list_of_strings")]
                    param_variants = preferred + [v for v in input_variants if v.get("name") == "raw_string"]
                    if not param_variants:
                        param_variants = input_variants
            
            variants_failed = []
            variants_passed = 0
            last_success_result = None
            
            for variant in param_variants:
                try:
                    call_args = {}
                    if params:
                        # Use the first param name
                        call_args[params[0]] = variant["value"]
                    result = func(**call_args) if call_args else func()
                    variants_passed += 1
                    last_success_result = result
                except Exception as ve:
                    variants_failed.append({
                        "variant_name": variant.get("name", "unknown"),
                        "error": str(ve)
                    })
            
            if variants_failed:
                return {
                    "success": False,
                    "error": f"{len(variants_failed)} variant(s) failed",
                    "variants_failed": variants_failed,
                    "traceback": None
                }
            
            # CRITICAL: Verify that the result is JSON-serializable
            # This catches issues like datetime objects that crash the observer
            if last_success_result is not None:
                serialization_issues = self._check_json_serializable(last_success_result)
                if serialization_issues:
                    if self.verbose:
                        print(f"[Verifier]     ⚠️ {tool_name}: Non-JSON-serializable output detected!")
                        for issue in serialization_issues[:5]:  # Show first 5
                            print(f"[Verifier]       - {issue}")
                    return {
                        "success": False,
                        "error": "Tool returns non-JSON-serializable data",
                        "non_serializable_fields": serialization_issues,
                        "fix_hint": "Convert datetime to .strftime() or .isoformat(), custom objects to dict"
                    }
            
            # Analyze result from the last successful call
            return {
                "success": True,
                "output_type": type(last_success_result).__name__ if last_success_result is not None else None,
                "output_keys": list(last_success_result.keys()) if isinstance(last_success_result, dict) else None,
                "result": last_success_result,
                "variants_passed": variants_passed,
                "variants_failed": variants_failed
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc()
            }
    
    def _check_json_serializable(self, obj: Any, path: str = "") -> List[str]:
        """
        Recursively check if an object is JSON-serializable.
        
        Returns a list of problematic fields with their types.
        Empty list means the object is fully serializable.
        """
        issues = []
        
        if obj is None:
            return issues
        
        # Check primitives
        if isinstance(obj, (str, int, float, bool)):
            return issues
        
        # Check dict
        if isinstance(obj, dict):
            for k, v in obj.items():
                current_path = f"{path}.{k}" if path else str(k)
                # Check if key is serializable
                if not isinstance(k, (str, int, float, bool)):
                    issues.append(f"{current_path} (key): {type(k).__name__}")
                # Check value
                try:
                    json.dumps(v)
                except (TypeError, ValueError):
                    issues.append(f"{current_path}: {type(v).__name__}")
                    # Recurse to find nested issues
                    issues.extend(self._check_json_serializable(v, current_path))
            return issues
        
        # Check list/tuple
        if isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                current_path = f"{path}[{i}]" if path else f"[{i}]"
                try:
                    json.dumps(item)
                except (TypeError, ValueError):
                    issues.append(f"{current_path}: {type(item).__name__}")
                    issues.extend(self._check_json_serializable(item, current_path))
            return issues
        
        # Any other type is not serializable
        issues.append(f"{path or 'root'}: {type(obj).__name__}")
        return issues
    
    def _verify_agent_execution(
        self,
        agent,
        observations: List[Dict]
    ) -> VerificationResult:
        """Verify that the agent can execute with a sample."""
        if self.verbose:
            print("[Verifier]   🤖 Testing agent execution...")
        
        if not observations:
            return VerificationResult(
                success=True,
                stage="agent_test",
                message="No observations to test (skipped)",
                details={}
            )
        
        # Pick a random sample
        import random
        sample = random.choice(observations)
        
        # Try multiple fields for input (different observation formats)
        # - Hybrid learner uses 'input'
        # - AppWorld uses 'task_description' or we can use task_id as test
        sample_input = (
            sample.get("input") or 
            sample.get("task_description") or
            sample.get("task_prompt") or
            ""
        )
        
        # If no input found, skip agent test (don't fail with empty content)
        if not sample_input or not sample_input.strip():
            if self.verbose:
                print(f"[Verifier]     ⚠️ No input found in observation (keys: {list(sample.keys())[:5]}...), skipping agent test")
            return VerificationResult(
                success=True,
                stage="agent_test",
                message="Skipped agent test - no input field in observation",
                details={"observation_keys": list(sample.keys()), "sample_id": sample.get("sample_id") or sample.get("task_id")}
            )
        
        try:
            # Call the agent
            response = agent.call(sample_input)
            
            # Check if response is valid (not empty, not an error)
            if not response:
                return VerificationResult(
                    success=False,
                    stage="agent_test",
                    message="Agent returned empty response",
                    details={"sample_id": sample.get("sample_id")}
                )
            
            # Check for obvious error indicators
            lower_resp = response.lower()
            
            # Check for Python traceback errors
            if "error" in lower_resp[:100] and "traceback" in lower_resp:
                return VerificationResult(
                    success=False,
                    stage="agent_test",
                    message="Agent response contains error traceback",
                    details={"response_preview": response[:500]},
                    error_log=response[:2000]
                )
            
            # Check for API call errors (e.g., "Error calling Claude API: 400")
            if "error calling" in lower_resp[:200] and ("400" in response[:200] or "401" in response[:200] or "500" in response[:200]):
                return VerificationResult(
                    success=False,
                    stage="agent_test",
                    message="Agent call returned API error",
                    details={"response_preview": response[:500]},
                    error_log=response[:2000]
                )
            
            # Check for invalid_request_error from Claude
            if "invalid_request_error" in lower_resp or "messages.0: all messages must have non-empty content" in lower_resp:
                return VerificationResult(
                    success=False,
                    stage="agent_test",
                    message="Agent call returned invalid request error (empty content)",
                    details={"response_preview": response[:500]},
                    error_log=response[:2000]
                )
            
            if self.verbose:
                print(f"[Verifier]     ✅ Agent execution successful")
                print(f"[Verifier]     Response preview: {response}...")
            
            return VerificationResult(
                success=True,
                stage="agent_test",
                message="Agent executed successfully",
                details={
                    "sample_id": sample.get("sample_id"),
                    "response_length": len(response),
                    "response_preview": response[:200]
                }
            )
            
        except Exception as e:
            return VerificationResult(
                success=False,
                stage="agent_test",
                message=f"Agent execution failed: {str(e)}",
                details={"sample_id": sample.get("sample_id")},
                error_log=traceback.format_exc()
            )
    
    def _get_sample_input(self, observations: List[Dict]) -> str:
        """Get sample input from observations for tool testing."""
        if not observations:
            # Return a default test input
            return "{2025-01-01 10:00:00, B123, 456, glance_view, NULL, us-test, /test/path}"
        
        # Get input from first observation
        sample = observations[0]
        input_data = sample.get("input", "")
        
        if isinstance(input_data, str):
            return input_data
        else:
            return json.dumps(input_data, default=str)
    
    def _build_input_variants(self, sample_input: str) -> List[Dict[str, Any]]:
        """
        Build multiple input variants to stress-test tool argument parsing.
        Includes:
          - raw string
          - list of raw event strings
          - list of parsed dicts (best-effort)
        """
        variants = []
        
        # Raw string
        variants.append({"name": "raw_string", "value": sample_input})
        
        # Extract event lines (split by newline / commas between braces)
        event_lines = []
        for line in sample_input.splitlines():
            line = line.strip()
            if not line:
                continue
            # Remove trailing commas
            if line.endswith(","):
                line = line[:-1]
            if line.startswith("{") and line.endswith("}"):
                event_lines.append(line)
        
        # If no lines detected, try to split by "}," pattern
        if not event_lines and "}," in sample_input:
            chunks = sample_input.split("},")
            for c in chunks:
                c = c.strip()
                if not c:
                    continue
                if not c.endswith("}"):
                    c = c + "}"
                if c.startswith("{") and c.endswith("}"):
                    event_lines.append(c)
        
        if event_lines:
            variants.append({"name": "list_of_strings", "value": event_lines})
            
            # Try to parse into dicts
            parsed_dicts = []
            for ev in event_lines:
                try:
                    inner = ev[1:-1]
                    parts = [p.strip() for p in inner.split(",")]
                    # Pad to length 7
                    while len(parts) < 7:
                        parts.append("NULL")
                    parsed_dicts.append({
                        "timestamp": parts[0] if parts[0] != "NULL" else None,
                        "asin": parts[1] if parts[1] != "NULL" else None,
                        "brand_id": parts[2] if parts[2] != "NULL" else None,
                        "event_name": parts[3] if parts[3] != "NULL" else None,
                        "search_query": parts[4] if parts[4] != "NULL" else None,
                        "primary_tree": parts[5] if parts[5] != "NULL" else None,
                        "category_path": parts[6] if parts[6] != "NULL" else None
                    })
                except Exception:
                    continue
            
            if parsed_dicts:
                variants.append({"name": "list_of_dicts", "value": parsed_dicts})
        
        return variants
    
    def quick_verify(
        self,
        file_path: str,
        tool_name: str
    ) -> VerificationResult:
        """
        Quick verification of a single tool file.
        
        Args:
            file_path: Path to the tool file
            tool_name: Name of the tool function
            
        Returns:
            VerificationResult
        """
        if not os.path.exists(file_path):
            return VerificationResult(
                success=False,
                stage="quick_verify",
                message=f"File not found: {file_path}"
            )
        
        try:
            # Read and compile
            with open(file_path, 'r') as f:
                code = f.read()
            
            compile(code, file_path, 'exec')
            
            # Try to load
            spec = importlib.util.spec_from_file_location(tool_name, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Check function exists
            func = getattr(module, tool_name, None)
            if func is None:
                return VerificationResult(
                    success=False,
                    stage="quick_verify",
                    message=f"Function '{tool_name}' not found in module"
                )
            
            return VerificationResult(
                success=True,
                stage="quick_verify",
                message="Quick verification passed"
            )
            
        except SyntaxError as e:
            return VerificationResult(
                success=False,
                stage="quick_verify",
                message=f"Syntax error at line {e.lineno}: {e.msg}",
                error_log=str(e)
            )
        except Exception as e:
            return VerificationResult(
                success=False,
                stage="quick_verify",
                message=str(e),
                error_log=traceback.format_exc()
            )

