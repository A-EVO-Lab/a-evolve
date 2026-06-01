"""Docker environment management for SWE-bench containers.

Ported from CodeDojo/swe-agent/swe_agent/docker_env.py.
"""

from __future__ import annotations

import logging
import subprocess
import time
import uuid

logger = logging.getLogger(__name__)


class SWEBenchContainer:
    """Manages a SWE-bench Docker container lifecycle."""

    def __init__(self, image_name: str, container_name: str | None = None):
        self.image_name = image_name
        self.container_name = container_name or f"swe-agent-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        self._running = False

    def start(self) -> str:
        subprocess.run(["docker", "rm", "-f", self.container_name], capture_output=True)
        result = subprocess.run(
            ["docker", "run", "-d", "--name", self.container_name, self.image_name, "sleep", "infinity"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start container: {result.stderr}")
        self._running = True
        logger.info("Container '%s' started from image '%s'", self.container_name, self.image_name)

        # Ensure /testbed exists — SWE-bench Pro uses /app instead of /testbed
        check = subprocess.run(
            ["docker", "exec", self.container_name, "test", "-d", "/testbed"],
            capture_output=True,
        )
        if check.returncode != 0:
            # No /testbed — symlink from /app if it exists (SWE-bench Pro layout)
            subprocess.run(
                ["docker", "exec", self.container_name, "bash", "-c",
                 "[ -d /app ] && ln -s /app /testbed || true"],
                capture_output=True,
            )

        return self.container_name

    def exec(self, command: str, workdir: str = "/testbed", timeout: int = 300) -> str:
        result = subprocess.run(
            ["docker", "exec", "-w", workdir, self.container_name, "bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return output

    def get_diff(self) -> str:
        return self.exec("git diff", workdir="/testbed")

    def stop(self):
        if self._running:
            subprocess.run(["docker", "rm", "-f", self.container_name], capture_output=True)
            self._running = False
            logger.info("Container '%s' stopped and removed.", self.container_name)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


def pull_image(image_name: str) -> bool:
    """Pull a Docker image if not already present locally."""
    result = subprocess.run(["docker", "image", "inspect", image_name], capture_output=True)
    if result.returncode == 0:
        return True
    logger.info("Pulling image %s...", image_name)
    result = subprocess.run(
        ["docker", "pull", image_name],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        logger.error("Failed to pull %s: %s", image_name, result.stderr.strip())
        return False
    return True
