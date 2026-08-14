"""Eval script generation for SWE-bench evaluation.

Generates a bash script per instance that activates the conda environment,
runs repo-specific setup commands, resets test files to the base commit,
applies the test patch, and executes the repo-specific test command.

Ported from inspect_evals/src/inspect_evals/swe_bench/scorers.py
"""

import re
import shlex
from textwrap import dedent

from .constants import CONDA_ENV, REPO_DIRECTORY
from .repo_specs import MAP_REPO_VERSION_TO_SPECS


def get_test_directives(repo: str, test_patch: str) -> list[str]:
    """Extract test directives from a test patch in the format expected by the repo's test runner.

    Parses the diff to find modified files, then converts them to the format
    each repo's test runner expects. For Django, this means converting file
    paths like ``tests/queries/test_query.py`` to module-style labels like
    ``queries.test_query``. For pytest-based repos, file paths are used as-is.

    Mirrors the behavior of ``swebench.harness.utils.get_test_directives``.

    Args:
        repo: Repository name (e.g. ``"django/django"``).
        test_patch: The unified diff string from the dataset.

    Returns:
        A list of test directives suitable for the repo's test runner.
    """
    directives = re.findall(r"--- a/(.*)", test_patch)

    if repo == "django/django":
        # Django's runtests.py expects module-style labels:
        #   tests/queries/test_query.py -> queries.test_query
        converted = []
        for d in directives:
            # Strip leading tests/ prefix
            if d.startswith("tests/"):
                d = d[len("tests/"):]
            # Remove .py extension
            if d.endswith(".py"):
                d = d[:-3]
            # Convert path separators to dots
            d = d.replace("/", ".")
            converted.append(d)
        return converted

    if repo == "sympy/sympy":
        # Sympy's test runner expects file paths relative to repo root
        # e.g. sympy/core/tests/test_basic.py -> sympy/core/tests/test_basic.py
        return directives

    # For pytest-based repos (sphinx, scikit-learn, matplotlib, etc.),
    # file paths work directly as test targets
    return directives


def generate_eval_script(
    test_patch: str,
    repo: str,
    version: str,
    base_commit: str,
) -> str:
    """Generate a bash eval script for a SWE-bench instance.

    The generated script follows the reference implementation template exactly:
    ``set -uo pipefail -x``, conda activation, repo-specific setup commands,
    re-activation, install commands (empty for scikit-learn), ``git checkout``
    to reset test files, ``git apply`` of the test patch, run test command with
    test directives, and a final reset of test files.

    Args:
        test_patch: The test patch diff from the dataset.
        repo: Repository name (e.g. ``"django/django"``).
        version: Version string (e.g. ``"3.0"``).
        base_commit: The base commit hash.

    Returns:
        A bash script string ready to execute in the container.

    Raises:
        KeyError: If *repo*/*version* is not found in
            ``MAP_REPO_VERSION_TO_SPECS``.
    """
    spec = MAP_REPO_VERSION_TO_SPECS[repo][version]

    test_command = spec["test_cmd"]
    repo_specific_setup_command = spec.get("eval_commands", [])
    repo_specific_install_command = spec.get("install", "")

    # Special case: scikit-learn install is always skipped
    if repo == "scikit-learn/scikit-learn":
        repo_specific_install_command = ""

    test_patch_files = re.findall(r"--- a/(.*)", test_patch)
    test_files = get_test_directives(repo, test_patch)

    newline = "\n"
    eval_script = dedent(
        f"""\
#!/bin/bash
set -uo pipefail -x

cd {REPO_DIRECTORY}
set +x
source /opt/miniconda3/bin/activate
conda activate {CONDA_ENV}
set -x

{newline.join(repo_specific_setup_command)}

cd {REPO_DIRECTORY}
set +x
source /opt/miniconda3/bin/activate
conda activate {CONDA_ENV}
set -x

{repo_specific_install_command}

git checkout {base_commit} {" ".join(test_patch_files)}

echo {shlex.quote(test_patch)} > /tmp/test_patch.diff
git apply --check /tmp/test_patch.diff
git apply /tmp/test_patch.diff

set +x
{test_command} {" ".join(test_files)} || true

git checkout {base_commit} {" ".join(test_patch_files)}
"""
    )
    return eval_script
