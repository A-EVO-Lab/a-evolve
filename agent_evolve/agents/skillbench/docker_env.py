"""Docker environment management for SkillBench containers.

Builds images from per-task Dockerfiles, runs containers, executes
commands inside them via ``docker exec``, and runs verification
(``test.sh`` -> ``reward.txt``).
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

BUILD_TIMEOUT = 600  # seconds
EXEC_TIMEOUT = 900   # seconds


@dataclass
class VerificationResult:
    """Structured verification result parsed from test.sh + reward.txt."""

    passed: bool
    reward_float: float
    pass_binary: bool
    eval_output: str
    verifier_tail: str
    failure_class: str


def _tail_text(text: str, *, max_lines: int = 120, max_chars: int = 6000) -> str:
    lines = text.splitlines()
    tail = "\n".join(lines[-max_lines:])
    if len(tail) > max_chars:
        tail = tail[-max_chars:]
    return tail


def _classify_verifier_failure(
    *,
    eval_output: str,
    command_rc: int,
    reward_found: bool,
    reward_parse_failed: bool,
) -> str:
    text = (eval_output or "").lower()
    if command_rc == -1 or "timed out" in text or "timeout" in text:
        return "verifier_timeout"
    if not reward_found:
        return "reward_missing"
    if reward_parse_failed:
        return "reward_parse_error"
    if "assertionerror" in text or re.search(r"\bassert\b", text):
        return "assertion"
    if "modulenotfounderror" in text or "no module named" in text:
        return "module_missing"
    if "filenotfounderror" in text or "no such file or directory" in text:
        return "file_missing"
    if "failed" in text and "pytest" in text:
        return "test_fail"
    if "traceback" in text or "exception" in text or "error:" in text:
        return "runtime_exception"
    return "verifier_fail"


def build_image(dockerfile_dir: str, tag: str | None = None,
                timeout: int = BUILD_TIMEOUT) -> str:
    """Build a Docker image from *dockerfile_dir* and return the tag.

    The directory must contain a ``Dockerfile``.
    """
    dockerfile_dir = str(dockerfile_dir)
    if tag is None:
        tag = f"skillbench-{Path(dockerfile_dir).parent.name}:{int(time.time())}"

    cmd = ["docker", "build", "-t", tag, dockerfile_dir]
    logger.info("Building image %s from %s ...", tag, dockerfile_dir)
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Docker build failed for {dockerfile_dir}:\n{result.stderr[-2000:]}"
        )
    logger.info("Built image %s", tag)
    return tag


class SkillBenchContainer:
    """Manages a single SkillBench Docker container."""

    def __init__(
        self,
        image: str,
        container_name: str | None = None,
        cpus: int = 1,
        memory: str = "4g",
    ):
        self.image = image
        self.container_name = container_name or f"sb-{int(time.time())}"
        self.cpus = cpus
        self.memory = memory
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the container in detached mode with a long-lived sleep."""
        subprocess.run(
            ["docker", "rm", "-f", self.container_name], capture_output=True,
        )
        cmd = [
            "docker", "run", "-d",
            "--name", self.container_name,
            f"--cpus={self.cpus}",
            f"--memory={self.memory}",
            self.image,
            "sleep", "infinity",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to start container {self.container_name}: {result.stderr}"
            )
        self._running = True
        logger.info("Container %s started from %s", self.container_name, self.image)

    def stop(self) -> None:
        """Stop and remove the container."""
        if self._running:
            subprocess.run(
                ["docker", "rm", "-f", self.container_name], capture_output=True,
            )
            self._running = False
            logger.info("Container %s stopped.", self.container_name)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    # ── Command execution ────────────────────────────────────────────

    def exec_command(
        self, cmd: str, timeout: int = EXEC_TIMEOUT, workdir: str | None = None,
    ) -> tuple[str, str, int]:
        """Run *cmd* inside the container and return (stdout, stderr, returncode)."""
        docker_cmd = ["docker", "exec"]
        if workdir:
            docker_cmd.extend(["-w", workdir])
        docker_cmd.extend([
            self.container_name,
            "bash", "-ic", cmd,
        ])
        try:
            result = subprocess.run(
                docker_cmd, capture_output=True, text=True, timeout=timeout,
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", f"Command timed out after {timeout}s", -1

    # ── File operations ──────────────────────────────────────────────

    def read_file(self, path: str) -> str:
        """Read a file from inside the container."""
        stdout, stderr, rc = self.exec_command(f"cat {path!r}", timeout=30)
        if rc != 0:
            raise FileNotFoundError(f"Cannot read {path}: {stderr.strip()}")
        return stdout

    def write_file(self, path: str, content: str) -> None:
        """Write content to a file inside the container."""
        escaped = content.replace("\\", "\\\\").replace("'", "'\\''")
        cmd = f"mkdir -p $(dirname {path!r}) && printf '%s' '{escaped}' > {path!r}"
        _, stderr, rc = self.exec_command(cmd, timeout=30)
        if rc != 0:
            raise RuntimeError(f"Cannot write {path}: {stderr.strip()}")

    def copy_into(self, src_host: str, dst_container: str) -> None:
        """Copy a host file/directory into the container."""
        result = subprocess.run(
            ["docker", "cp", src_host, f"{self.container_name}:{dst_container}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker cp failed: {result.stderr.strip()}")

    # ── Verification ─────────────────────────────────────────────────

    def copy_tests(self, test_sh_path: str, test_py_path: str | None) -> None:
        """Copy **all** test files into the container at ``/tests/``.

        Many tasks include extra fixture files (expected outputs, ground-truth
        data, helper scripts) alongside ``test.sh`` / ``test_outputs.py``.
        The verifier will fail with assertion errors if these are missing.
        """
        self.exec_command("mkdir -p /tests /logs/verifier", timeout=10)

        tests_dir: Path | None = None
        if test_sh_path:
            tests_dir = Path(test_sh_path).parent
        elif test_py_path:
            tests_dir = Path(test_py_path).parent

        if tests_dir and tests_dir.is_dir():
            for entry in sorted(tests_dir.iterdir()):
                if entry.name == "__pycache__":
                    continue
                self.copy_into(str(entry), f"/tests/{entry.name}")
            if test_sh_path:
                self.exec_command("chmod +x /tests/test.sh", timeout=10)

    def run_verification(self, timeout: int = 900) -> VerificationResult:
        """Execute ``test.sh`` and parse reward/evaluation diagnostics."""
        stdout, stderr, rc = self.exec_command(
            "cd /root && bash /tests/test.sh 2>&1",
            timeout=timeout,
        )
        eval_output = stdout + stderr

        reward_found = False
        reward_parse_failed = False
        reward_float = 0.0
        try:
            reward_raw = self.read_file("/logs/verifier/reward.txt").strip()
            reward_found = True
            reward_float = float(reward_raw)
            if reward_float < 0.0:
                reward_float = 0.0
            if reward_float > 1.0:
                reward_float = 1.0
        except FileNotFoundError:
            eval_output += "\n[reward.txt not found]"
        except ValueError:
            reward_parse_failed = True
            eval_output += "\n[reward.txt parse failed]"

        pass_binary = reward_float >= 1.0
        passed = pass_binary
        failure_class = "none" if passed else _classify_verifier_failure(
            eval_output=eval_output,
            command_rc=rc,
            reward_found=reward_found,
            reward_parse_failed=reward_parse_failed,
        )
        verifier_tail = _tail_text(eval_output)

        return VerificationResult(
            passed=passed,
            reward_float=reward_float,
            pass_binary=pass_binary,
            eval_output=eval_output,
            verifier_tail=verifier_tail,
            failure_class=failure_class,
        )
