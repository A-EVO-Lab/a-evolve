"""Static mapping of repository versions to their test specs.

Ported from swebench.harness.constants.MAP_REPO_VERSION_TO_SPECS.
Contains only the three keys needed for self-contained eval script generation:
  - test_cmd: the test runner command string
  - eval_commands: repo-specific setup commands (list of strings)
  - install: the install command string (empty string if none)
"""

from typing import Any

# ---------------------------------------------------------------------------
# Test command constants (matching swebench naming conventions)
# ---------------------------------------------------------------------------
TEST_PYTEST = "pytest -rA"
TEST_PYTEST_VERBOSE = "pytest -rA --tb=long"
TEST_ASTROPY_PYTEST = "pytest -rA -vv -o console_output_style=classic --tb=no"
TEST_DJANGO = (
    "./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1"
)
TEST_DJANGO_NO_PARALLEL = "./tests/runtests.py --verbosity 2"
TEST_SEABORN = "pytest --no-header -rA"
TEST_SEABORN_VERBOSE = "pytest -rA --tb=long"
TEST_SPHINX = "tox --current-env -epy39 -v --"
TEST_SYMPY = (
    "PYTHONWARNINGS='ignore::UserWarning,ignore::SyntaxWarning' bin/test -C --verbose"
)

# ---------------------------------------------------------------------------
# MAP_REPO_VERSION_TO_SPECS
# ---------------------------------------------------------------------------
# Structure:
#   MAP_REPO_VERSION_TO_SPECS[repo][version] = {
#       "test_cmd": str,
#       "eval_commands": list[str],
#       "install": str,
#   }
# ---------------------------------------------------------------------------

MAP_REPO_VERSION_TO_SPECS: dict[str, dict[str, dict[str, Any]]] = {
    # ------------------------------------------------------------------
    # django/django
    # ------------------------------------------------------------------
    "django/django": {
        "2.0": {
            "test_cmd": TEST_DJANGO,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.1": {
            "test_cmd": TEST_DJANGO,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.2": {
            "test_cmd": TEST_DJANGO,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.0": {
            "test_cmd": TEST_DJANGO,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.1": {
            "test_cmd": TEST_DJANGO,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.2": {
            "test_cmd": TEST_DJANGO,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "4.0": {
            "test_cmd": TEST_DJANGO,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "4.1": {
            "test_cmd": TEST_DJANGO,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "4.2": {
            "test_cmd": TEST_DJANGO,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "5.0": {
            "test_cmd": TEST_DJANGO,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
    },
    # ------------------------------------------------------------------
    # scikit-learn/scikit-learn  (install is ALWAYS empty string)
    # ------------------------------------------------------------------
    "scikit-learn/scikit-learn": {
        "0.20": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "",
        },
        "0.21": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "",
        },
        "0.22": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "",
        },
        "1.3": {
            "test_cmd": TEST_PYTEST_VERBOSE,
            "eval_commands": [],
            "install": "",
        },
        "1.4": {
            "test_cmd": TEST_PYTEST_VERBOSE,
            "eval_commands": [],
            "install": "",
        },
        "1.5": {
            "test_cmd": TEST_PYTEST_VERBOSE,
            "eval_commands": [],
            "install": "",
        },
    },
    # ------------------------------------------------------------------
    # matplotlib/matplotlib
    # ------------------------------------------------------------------
    "matplotlib/matplotlib": {
        "3.0": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.1": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.2": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.3": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.4": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.5": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.6": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.7": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.8": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.9": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
    },
    # ------------------------------------------------------------------
    # sympy/sympy
    # ------------------------------------------------------------------
    "sympy/sympy": {
        "1.0": {
            "test_cmd": TEST_SYMPY,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.1": {
            "test_cmd": TEST_SYMPY,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.2": {
            "test_cmd": TEST_SYMPY,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.4": {
            "test_cmd": TEST_SYMPY,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.5": {
            "test_cmd": TEST_SYMPY,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.6": {
            "test_cmd": TEST_SYMPY,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.7": {
            "test_cmd": TEST_SYMPY,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.8": {
            "test_cmd": TEST_SYMPY,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.9": {
            "test_cmd": TEST_SYMPY,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.10": {
            "test_cmd": TEST_SYMPY,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.11": {
            "test_cmd": TEST_SYMPY,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.12": {
            "test_cmd": TEST_SYMPY,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.13": {
            "test_cmd": TEST_SYMPY,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
    },
    # ------------------------------------------------------------------
    # astropy/astropy
    # ------------------------------------------------------------------
    "astropy/astropy": {
        "1.3": {
            "test_cmd": TEST_ASTROPY_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.0": {
            "test_cmd": TEST_ASTROPY_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.1": {
            "test_cmd": TEST_ASTROPY_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "4.0": {
            "test_cmd": TEST_ASTROPY_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "5.0": {
            "test_cmd": TEST_ASTROPY_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "5.1": {
            "test_cmd": TEST_ASTROPY_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "5.2": {
            "test_cmd": TEST_ASTROPY_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "5.3": {
            "test_cmd": TEST_ASTROPY_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "6.0": {
            "test_cmd": TEST_ASTROPY_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
    },
    # ------------------------------------------------------------------
    # pytest-dev/pytest
    # ------------------------------------------------------------------
    "pytest-dev/pytest": {
        "4.4": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "4.5": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "4.6": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "5.0": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "5.1": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "5.2": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "5.3": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "5.4": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "6.0": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "6.2": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "6.3": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "7.0": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "7.1": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "7.2": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "7.4": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "8.0": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "8.1": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "8.2": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "8.3": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
    },
    # ------------------------------------------------------------------
    # sphinx-doc/sphinx
    # ------------------------------------------------------------------
    "sphinx-doc/sphinx": {
        "3.0": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "3.1": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "3.2": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "3.3": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "3.4": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "3.5": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "4.0": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "4.1": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "4.2": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "4.3": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "4.4": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "4.5": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "5.0": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "5.1": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "5.2": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "5.3": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "6.2": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "7.0": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "7.1": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "7.2": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "7.3": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "7.4": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "8.0": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
        "8.1": {
            "test_cmd": TEST_SPHINX,
            "eval_commands": [],
            "install": "python -m pip install -e .[test]",
        },
    },
    # ------------------------------------------------------------------
    # psf/requests
    # ------------------------------------------------------------------
    "psf/requests": {
        "0.14": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.1": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "1.2": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.0": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.2": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.3": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.4": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.5": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.7": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.8": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.9": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.10": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.17": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.22": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.24": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.25": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.26": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.27": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
    },
    # ------------------------------------------------------------------
    # pydata/xarray
    # ------------------------------------------------------------------
    "pydata/xarray": {
        "0.12": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "0.18": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "0.19": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "0.20": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2022.03": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2022.06": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2022.09": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2023.07": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2024.05": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
    },
    # ------------------------------------------------------------------
    # pylint-dev/pylint
    # ------------------------------------------------------------------
    "pylint-dev/pylint": {
        "2.8": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.9": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.10": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.11": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.13": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.14": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.15": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.16": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.17": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.0": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.1": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.2": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.3": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "4.0": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
    },
    # ------------------------------------------------------------------
    # pallets/flask
    # ------------------------------------------------------------------
    "pallets/flask": {
        "0.12": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.0": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.1": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.2": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "2.3": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.0": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
        "3.1": {
            "test_cmd": TEST_PYTEST,
            "eval_commands": [],
            "install": "python -m pip install -e .",
        },
    },
    # ------------------------------------------------------------------
    # mwaskom/seaborn
    # ------------------------------------------------------------------
    "mwaskom/seaborn": {
        "0.11": {
            "test_cmd": TEST_SEABORN,
            "eval_commands": [],
            "install": "python -m pip install -e .[dev]",
        },
        "0.12": {
            "test_cmd": TEST_SEABORN,
            "eval_commands": [],
            "install": "python -m pip install -e .[dev]",
        },
        "0.13": {
            "test_cmd": TEST_SEABORN,
            "eval_commands": [],
            "install": "python -m pip install -e .[dev]",
        },
    },
}
