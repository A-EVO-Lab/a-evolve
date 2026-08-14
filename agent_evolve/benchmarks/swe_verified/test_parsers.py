"""Test output parsers for SWE-bench evaluation.

Uses swebench's battle-tested parsers when available, with local fallbacks
that handle ANSI codes and other edge cases.
"""

from __future__ import annotations

import importlib
import logging
import re
from collections.abc import Callable

logger = logging.getLogger(__name__)


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences and control characters."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    escapes = "".join([chr(c) for c in range(1, 32)])
    return text.translate(str.maketrans("", "", escapes))


# ---------------------------------------------------------------------------
# Local fallback parsers
# ---------------------------------------------------------------------------

def parse_pytest_output(test_output: str) -> dict[str, str]:
    """Parse pytest output, handling ANSI codes from tox/pytest."""
    test_output = _strip_ansi(test_output)
    results: dict[str, str] = {}
    for line in test_output.splitlines():
        line = line.strip()
        match = re.match(
            r"^(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED)\s+(.+?)(?:\s+-\s+.*)?$",
            line,
        )
        if match:
            status_str = match.group(1)
            test_name = match.group(2).strip()
            if status_str in ("PASSED", "XFAIL"):
                results[test_name] = "PASSED"
            else:
                results[test_name] = "FAILED"
    return results


def parse_django_test_output(test_output: str) -> dict[str, str]:
    """Parse Django test runner output."""
    test_output = _strip_ansi(test_output)
    results: dict[str, str] = {}
    for line in test_output.splitlines():
        line = line.strip()
        match = re.match(
            r"^(.*?)\s+\.\.\.\s+"
            r"(ok|FAIL|ERROR|skipped|expected failure|unexpected success).*$",
            line,
        )
        if match:
            test_name = match.group(1).strip()
            status_str = match.group(2)
            if status_str == "ok":
                results[test_name] = "PASSED"
            elif status_str in ("FAIL", "ERROR"):
                results[test_name] = "FAILED"
    return results


# ---------------------------------------------------------------------------
# Local fallback map
# ---------------------------------------------------------------------------

_LOCAL_PARSERS: dict[str, Callable[[str], dict[str, str]]] = {
    "django/django": parse_django_test_output,
    "scikit-learn/scikit-learn": parse_pytest_output,
    "matplotlib/matplotlib": parse_pytest_output,
    "sympy/sympy": parse_pytest_output,
    "astropy/astropy": parse_pytest_output,
    "pytest-dev/pytest": parse_pytest_output,
    "sphinx-doc/sphinx": parse_pytest_output,
    "psf/requests": parse_pytest_output,
    "pydata/xarray": parse_pytest_output,
    "pylint-dev/pylint": parse_pytest_output,
    "pallets/flask": parse_pytest_output,
    "mwaskom/seaborn": parse_pytest_output,
}


# ---------------------------------------------------------------------------
# swebench integration (lazy import to avoid prohibited top-level import)
# ---------------------------------------------------------------------------

def _try_load_swebench_parsers() -> tuple[dict | None, type | None]:
    """Lazily load swebench parsers. Returns (parser_map, TestSpec) or (None, None)."""
    try:
        grading = importlib.import_module("swebench.harness.grading")
        return grading.MAP_REPO_TO_PARSER, grading.TestSpec
    except (ImportError, AttributeError):
        return None, None


def _normalize_swebench_results(raw: dict[str, str]) -> dict[str, str]:
    """Normalize swebench parser output to our PASSED/FAILED format."""
    results: dict[str, str] = {}
    for test_name, status in raw.items():
        if status in ("PASSED", "XFAIL"):
            results[test_name] = "PASSED"
        else:
            results[test_name] = "FAILED"
    return results


def _build_parser_map() -> dict[str, Callable[[str], dict[str, str]]]:
    """Build the unified parser map, preferring swebench when available."""
    result = dict(_LOCAL_PARSERS)

    swebench_parsers, TestSpec = _try_load_swebench_parsers()
    if swebench_parsers is None or TestSpec is None:
        logger.debug("swebench not available, using local parsers only")
        return result

    logger.debug("swebench available, augmenting parser map")

    for repo, swebench_parser in swebench_parsers.items():
        # Create a wrapper that adapts swebench's (log, test_spec) signature
        # to our (test_output) -> dict signature
        def _make_wrapper(parser_fn, repo_name):
            def wrapped(test_output: str) -> dict[str, str]:
                dummy_spec = TestSpec(
                    instance_id="", repo=repo_name, version="",
                    repo_script_list=[], eval_script_list=[], env_script_list=[],
                    arch="x86_64", FAIL_TO_PASS=[], PASS_TO_PASS=[],
                    language="python", docker_specs={}, namespace=None,
                )
                raw = parser_fn(test_output, dummy_spec)
                return _normalize_swebench_results(raw)
            return wrapped

        result[repo] = _make_wrapper(swebench_parser, repo)

    return result


# Public API — used by test_grader.py
MAP_REPO_TO_PARSER: dict[str, Callable[[str], dict[str, str]]] = _build_parser_map()
