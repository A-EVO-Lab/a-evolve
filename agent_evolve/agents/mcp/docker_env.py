"""Docker environment management for MCP-Atlas containers.

Starts the MCP-Atlas Docker image with port 1984 exposed and waits
for the agent-environment HTTP service to become ready.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time

import requests

logger = logging.getLogger(__name__)

DEFAULT_PORT = 1984
STARTUP_TIMEOUT = 600  # seconds to wait for the service (all servers may need to initialize)


class McpAtlasContainer:
    """Manages an MCP-Atlas Docker container lifecycle."""

    def __init__(
        self,
        image_name: str,
        container_name: str | None = None,
        port: int = 0,
        env_vars: dict[str, str] | None = None,
    ):
        self.image_name = image_name
        self.container_name = container_name or f"mcp-atlas-{int(time.time())}-{os.urandom(4).hex()}"
        self.port = port or self._find_free_port()
        self.env_vars = env_vars or {}
        self.base_url = f"http://localhost:{self.port}"
        self._running = False

    @staticmethod
    def _find_free_port() -> int:
        """Find a free port by binding to port 0 and reading the assignment."""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def start(self) -> str:
        """Start the container with port mapping and wait for readiness."""
        subprocess.run(
            ["docker", "rm", "-f", self.container_name], capture_output=True
        )
        cmd = [
            "docker", "run", "-d",
            "--name", self.container_name,
            "-p", f"{self.port}:1984",
        ]
        for key, value in self.env_vars.items():
            if value and value.strip():
                cmd.extend(["-e", f"{key}={value}"])
        cmd.append(self.image_name)
        logger.debug("Docker command: %s", " ".join(cmd[:8]) + " ...")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start container: {result.stderr}")

        # Verify the container is actually running
        check = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", self.container_name],
            capture_output=True, text=True,
        )
        if check.stdout.strip() != "true":
            logs = subprocess.run(
                ["docker", "logs", "--tail", "30", self.container_name],
                capture_output=True, text=True,
            )
            raise RuntimeError(
                f"Container '{self.container_name}' is not running. "
                f"Logs:\n{logs.stdout}\n{logs.stderr}"
            )

        self._running = True
        logger.info(
            "Container '%s' started from '%s' on port %d",
            self.container_name, self.image_name, self.port,
        )
        self._wait_ready()
        return self.container_name

    def _wait_ready(self) -> None:
        """Poll the service until it responds or timeout."""
        deadline = time.time() + STARTUP_TIMEOUT
        url = f"{self.base_url}/enabled-servers"
        logger.info("Waiting for MCP-Atlas service on %s ...", self.base_url)
        attempt = 0
        last_error = ""
        while time.time() < deadline:
            attempt += 1
            try:
                r = requests.get(url, timeout=5)
                if r.ok:
                    data = r.json()
                    online = data.get("online", 0)
                    total = data.get("total", 0)
                    logger.info(
                        "MCP-Atlas ready: %d/%d servers online", online, total
                    )
                    return
                else:
                    last_error = f"HTTP {r.status_code}"
            except requests.ConnectionError:
                last_error = "ConnectionError"
            except requests.Timeout:
                last_error = "Timeout"
            except Exception as e:
                last_error = str(e)

            if attempt % 10 == 0:
                # Check if container is still running
                check = subprocess.run(
                    ["docker", "inspect", "-f", "{{.State.Running}}", self.container_name],
                    capture_output=True, text=True,
                )
                running = check.stdout.strip()
                logger.info(
                    "Still waiting (attempt %d, last_error=%s, container_running=%s)",
                    attempt, last_error, running,
                )
                if running != "true":
                    logs = subprocess.run(
                        ["docker", "logs", "--tail", "20", self.container_name],
                        capture_output=True, text=True,
                    )
                    raise RuntimeError(
                        f"Container '{self.container_name}' died during startup. "
                        f"Logs:\n{logs.stdout}\n{logs.stderr}"
                    )
            time.sleep(3)
        raise RuntimeError(
            f"MCP-Atlas service not ready after {STARTUP_TIMEOUT}s (last_error={last_error})"
        )

    def stop(self) -> None:
        """Stop and remove the container."""
        if self._running:
            subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                capture_output=True,
            )
            self._running = False
            logger.info("Container '%s' stopped.", self.container_name)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


def pull_image(image_name: str) -> bool:
    """Pull a Docker image if not already present locally."""
    result = subprocess.run(
        ["docker", "image", "inspect", image_name], capture_output=True
    )
    if result.returncode == 0:
        return True
    logger.info("Pulling image %s ...", image_name)
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
