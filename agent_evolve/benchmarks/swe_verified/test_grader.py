"""Test output grading for SWE-bench evaluation.

Parses test runner output and grades against FAIL_TO_PASS and PASS_TO_PASS
test lists. Zero dependency on swebench, inspect_ai, or inspect_evals.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .constants import ERROR_MARKERS
from .test_parsers import MAP_REPO_TO_PARSER


@dataclass
class GradeResult:
    """Result of grading test output."""

    score: float  # 1.0 or 0.0
    passed: bool  # True if score == 1.0
    fail_to_pass_results: dict[str, str]  # test_name -> "PASSED"/"FAILED"
    pass_to_pass_results: dict[str, str]  # test_name -> "PASSED"/"FAILED"
    explanation: str  # Structured detail string
    error: str | None  # Error message if error markers found


def check_error_markers(test_output: str) -> str | None:
    """Check test output for error markers.

    Returns the error description string if any marker is found, None otherwise.
    """
    error_string_search = {marker: marker in test_output for marker in ERROR_MARKERS}
    if any(error_string_search.values()):
        return (
            f"The tests did not run correctly. "
            f"Output from searching for error strings:\n\n{error_string_search}"
            f"\n\nOutput from tests:\n\n{test_output}"
        )
    return None


def grade_test_output(
    test_output: str,
    repo: str,
    fail_to_pass: list[str],
    pass_to_pass: list[str],
) -> GradeResult:
    """Parse and grade test output against expected test lists.

    Args:
        test_output: Combined stdout+stderr from eval script.
        repo: Repository name for selecting the correct parser.
        fail_to_pass: Test cases expected to transition FAIL->PASS.
        pass_to_pass: Test cases expected to remain PASS.

    Returns:
        GradeResult with score, per-test results, and explanation.
    """
    # 1. Check error markers first
    error_desc = check_error_markers(test_output)
    if error_desc is not None:
        return GradeResult(
            score=0.0,
            passed=False,
            fail_to_pass_results={k: "FAILED" for k in fail_to_pass},
            pass_to_pass_results={k: "FAILED" for k in pass_to_pass},
            explanation=error_desc,
            error=error_desc,
        )

    # 2. Look up parser
    parser = MAP_REPO_TO_PARSER.get(repo)
    if parser is None:
        error_msg = f"No parser for repo: {repo}"
        return GradeResult(
            score=0.0,
            passed=False,
            fail_to_pass_results={k: "FAILED" for k in fail_to_pass},
            pass_to_pass_results={k: "FAILED" for k in pass_to_pass},
            explanation=error_msg,
            error=error_msg,
        )

    # 3. Parse test output
    try:
        test_output_parsed = parser(test_output)
    except Exception as exc:
        error_msg = f"Parser error for repo {repo}: {exc}"
        return GradeResult(
            score=0.0,
            passed=False,
            fail_to_pass_results={k: "FAILED" for k in fail_to_pass},
            pass_to_pass_results={k: "FAILED" for k in pass_to_pass},
            explanation=error_msg,
            error=error_msg,
        )

    # 4. Initialize all tests as "FAILED"
    fail_to_pass_results = {k: "FAILED" for k in fail_to_pass}
    pass_to_pass_results = {k: "FAILED" for k in pass_to_pass}

    # 5. Update with parsed results
    for k, v in test_output_parsed.items():
        if k in pass_to_pass_results:
            pass_to_pass_results[k] = v
        elif k in fail_to_pass_results:
            fail_to_pass_results[k] = v

    # 6. Score 1.0 iff ALL tests are "PASSED"
    passed_all = all(
        v == "PASSED" for v in fail_to_pass_results.values()
    ) and all(v == "PASSED" for v in pass_to_pass_results.values())
    score = 1.0 if passed_all else 0.0

    # 7. Build structured explanation with sorted results (failures first)
    sorted_pass_to_pass = dict(
        sorted(pass_to_pass_results.items(), key=lambda x: x[1] == "PASSED")
    )
    sorted_fail_to_pass = dict(
        sorted(fail_to_pass_results.items(), key=lambda x: x[1] == "PASSED")
    )

    explanation = (
        f"PASS_TO_PASS:\n\n{json.dumps(sorted_pass_to_pass, indent=2)}"
        f"\n\nFAIL_TO_PASS:\n\n{json.dumps(sorted_fail_to_pass, indent=2)}\n\n"
    )

    return GradeResult(
        score=score,
        passed=passed_all,
        fail_to_pass_results=fail_to_pass_results,
        pass_to_pass_results=pass_to_pass_results,
        explanation=explanation,
        error=None,
    )
