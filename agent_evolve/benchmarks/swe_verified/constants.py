"""Error marker strings and default configuration for SWE-bench evaluation."""

# Error markers from swebench.harness.constants
APPLY_PATCH_FAIL = ">>>>> Patch Apply Failed"
RESET_FAILED = ">>>>> Reset Failed"
TESTS_ERROR = ">>>>> Tests Errored"
TESTS_TIMEOUT = ">>>>> Tests Timed Out"

# Additional error marker
ENVIRONMENT_RESET_FAILED = "Failed to reset task environment"

# All error markers as a tuple for easy checking
ERROR_MARKERS = (
    APPLY_PATCH_FAIL,
    RESET_FAILED,
    TESTS_ERROR,
    TESTS_TIMEOUT,
    ENVIRONMENT_RESET_FAILED,
)

# Default eval timeout in seconds
DEFAULT_EVAL_TIMEOUT = 300

# Conda environment name used in SWE-bench containers
CONDA_ENV = "testbed"

# Working directory inside the container
REPO_DIRECTORY = "/testbed"
