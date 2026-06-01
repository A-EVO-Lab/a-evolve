"""MCP API key registry — loads, merges, and serves API keys from multiple sources."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default server-to-key mapping
# ---------------------------------------------------------------------------

_SERVER_KEYS_YAML = Path(__file__).parent / "server_keys.yaml"


def _load_default_server_key_map() -> dict[str, list[str]]:
    """Load the server-to-key mapping from server_keys.yaml."""
    if _SERVER_KEYS_YAML.is_file():
        try:
            data = yaml.safe_load(_SERVER_KEYS_YAML.read_text()) or {}
            return {k: (v if isinstance(v, list) else []) for k, v in data.items()}
        except Exception as e:
            logger.warning("Failed to load %s: %s; using empty map", _SERVER_KEYS_YAML, e)
    return {}


DEFAULT_SERVER_KEY_MAP: dict[str, list[str]] = _load_default_server_key_map()


# ---------------------------------------------------------------------------
# Internal data model
# ---------------------------------------------------------------------------

@dataclass
class _KeyEntry:
    """Tracks a loaded key's name, value, and source for debug logging."""

    name: str    # e.g. "BRAVE_API_KEY"
    value: str   # the actual secret value
    source: str  # "env", "dotenv", or "aws_sm"


# ---------------------------------------------------------------------------
# KeyRegistry
# ---------------------------------------------------------------------------

class KeyRegistry:
    """Loads, merges, and provides API keys from multiple sources.

    Sources (highest to lowest priority):
      1. Process environment variables
      2. Local ``.env`` file
      3. AWS Secrets Manager

    Use :meth:`load` to populate the registry, then query with
    :meth:`get_keys_for_servers` or :meth:`has_keys_for_servers`.
    """

    def __init__(
        self,
        env_file_path: str | Path | None = None,
        aws_secret_name: str | None = None,
        aws_region: str | None = None,
        server_key_map_path: str | Path | None = None,
    ) -> None:
        self._env_file_path = Path(env_file_path) if env_file_path else None
        self._aws_secret_name = aws_secret_name
        self._aws_region = aws_region
        self._server_key_map_path = (
            Path(server_key_map_path) if server_key_map_path else None
        )
        # Internal store: env-var name → _KeyEntry
        self._keys: dict[str, _KeyEntry] = {}
        # Cache for AWS Secrets Manager results within a single load() call
        self._aws_cache: dict[str, dict[str, str]] = {}

    @classmethod
    def from_config(cls, config: EvolveConfig) -> KeyRegistry:
        """Factory that reads paths/settings from EvolveConfig.extra and MCP_ENV_FILE env var."""
        from agent_evolve.config import EvolveConfig  # noqa: F811 — deferred to avoid circular imports

        env_file = config.extra.get("mcp_env_file") or os.environ.get("MCP_ENV_FILE")
        return cls(
            env_file_path=env_file,
            aws_secret_name=config.extra.get("mcp_aws_secret_name"),
            aws_region=config.extra.get("mcp_aws_region"),
            server_key_map_path=config.extra.get("mcp_server_key_map"),
        )


    # ------------------------------------------------------------------
    # Server-key map
    # ------------------------------------------------------------------

    def get_server_key_map(self) -> dict[str, list[str]]:
        """Return the server-to-key mapping (custom YAML merged over defaults).

        If a custom YAML file is configured and exists, its entries are
        merged on top of :data:`DEFAULT_SERVER_KEY_MAP`.  Missing or
        unreadable files fall back to the defaults silently.
        """
        merged: dict[str, list[str]] = dict(DEFAULT_SERVER_KEY_MAP)

        if self._server_key_map_path is None:
            return merged

        path = self._server_key_map_path
        if not path.is_file():
            logger.debug("Custom server-key map not found at %s; using defaults", path)
            return merged

        try:
            with open(path) as fh:
                raw: Any = yaml.safe_load(fh)
        except Exception:
            logger.warning("Failed to read server-key map at %s; using defaults", path)
            return merged

        if not isinstance(raw, dict):
            logger.warning(
                "Server-key map at %s is not a YAML mapping; using defaults", path
            )
            return merged

        for server, keys in raw.items():
            if isinstance(keys, list):
                merged[str(server)] = [str(k) for k in keys]
            else:
                logger.warning(
                    "Ignoring non-list value for server %r in %s", server, path
                )

        return merged

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_loaded_key_names(self) -> set[str]:
        """Return the set of env-var names that have been loaded (no values)."""
        return set(self._keys.keys())
    # ------------------------------------------------------------------
    # .env file loading
    # ------------------------------------------------------------------

    def _load_dotenv(self, path: Path) -> dict[str, str]:
        """Parse a ``.env`` file and return a dict of KEY=VALUE pairs.

        - Lines starting with ``#`` are treated as comments and skipped.
        - Blank / whitespace-only lines are skipped.
        - Lines without an ``=`` sign are malformed — skipped with a
          WARNING that includes the line number (no values logged).
        - If the file does not exist, a DEBUG message is logged and an
          empty dict is returned.
        - If the file cannot be read due to a permission error, a
          WARNING is logged and an empty dict is returned.
        """
        result: dict[str, str] = {}

        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug("No .env file found at %s; zero keys loaded from dotenv", path)
            return result
        except PermissionError:
            logger.warning("Permission denied reading .env file at %s; proceeding without .env keys", path)
            return result

        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()

            # Skip blank lines and comments
            if not line or line.startswith("#"):
                continue

            # Must contain '=' to be valid
            if "=" not in line:
                logger.warning(
                    "Malformed line %d in %s (no '=' found); skipping",
                    line_no,
                    path,
                )
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            if not key:
                logger.warning(
                    "Malformed line %d in %s (empty key); skipping",
                    line_no,
                    path,
                )
                continue

            value = value.strip()
            # Strip surrounding quotes (single or double)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            result[key] = value

        return result


    # ------------------------------------------------------------------
    # Process environment variable loading
    # ------------------------------------------------------------------

    def _load_env_vars(self) -> dict[str, str]:
        """Read process env vars matching keys in the server-key map.

        Iterates all env var names referenced in the server-key map and
        checks ``os.environ`` for each.  Returns a dict of found
        name → value pairs.
        """
        result: dict[str, str] = {}
        server_key_map = self.get_server_key_map()

        # Collect all unique env var names from the map
        all_var_names: set[str] = set()
        for var_names in server_key_map.values():
            all_var_names.update(var_names)

        for var_name in sorted(all_var_names):
            value = os.environ.get(var_name)
            if value is not None:
                result[var_name] = value

        return result

    # ------------------------------------------------------------------
    # AWS Secrets Manager loading
    # ------------------------------------------------------------------

    def _load_aws_secrets(self, secret_name: str, region: str) -> dict[str, str]:
        """Retrieve API keys from AWS Secrets Manager.

        Uses ``boto3`` (imported lazily) to call ``get_secret_value`` and
        parses the ``SecretString`` field as a JSON object of env-var
        name → value pairs.

        Returns an empty dict on any error (not found, access denied,
        invalid JSON, network issues).  Results are cached in
        ``self._aws_cache`` so repeated calls within a single
        :meth:`load` invocation do not re-fetch.
        """
        cache_key = f"{secret_name}@{region}"
        if cache_key in self._aws_cache:
            return self._aws_cache[cache_key]

        try:
            import boto3  # lazy import — boto3 is optional (mcp extra)
        except ImportError:
            logger.error(
                "boto3 is not installed; cannot load secrets from AWS Secrets Manager. "
                "Install the 'mcp' extra to enable AWS support."
            )
            self._aws_cache[cache_key] = {}
            return {}

        try:
            client = boto3.client("secretsmanager", region_name=region)
            response = client.get_secret_value(SecretId=secret_name)
        except Exception as exc:
            exc_type = type(exc).__name__
            if exc_type == "ResourceNotFoundException":
                logger.error(
                    "AWS secret '%s' not found in region '%s'",
                    secret_name,
                    region,
                )
            elif exc_type == "AccessDeniedException":
                logger.error(
                    "Access denied retrieving AWS secret '%s' in region '%s'; "
                    "check IAM permissions for secretsmanager:GetSecretValue",
                    secret_name,
                    region,
                )
            else:
                logger.error(
                    "Failed to retrieve AWS secret '%s' in region '%s': %s",
                    secret_name,
                    region,
                    exc,
                )
            self._aws_cache[cache_key] = {}
            return {}

        secret_string = response.get("SecretString")
        if not secret_string:
            logger.error(
                "AWS secret '%s' has no SecretString field",
                secret_name,
            )
            self._aws_cache[cache_key] = {}
            return {}

        try:
            parsed = json.loads(secret_string)
        except json.JSONDecodeError:
            logger.error(
                "AWS secret '%s' contains invalid JSON; expected a JSON object "
                "of env var names to values",
                secret_name,
            )
            self._aws_cache[cache_key] = {}
            return {}

        if not isinstance(parsed, dict):
            logger.error(
                "AWS secret '%s' JSON is not an object; expected a JSON object "
                "of env var names to values",
                secret_name,
            )
            self._aws_cache[cache_key] = {}
            return {}

        result: dict[str, str] = {
            str(k): str(v) for k, v in parsed.items()
        }
        self._aws_cache[cache_key] = result
        return result

    # ------------------------------------------------------------------
    # Load & merge
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load keys from all configured sources and merge by priority.

        Priority (highest → lowest): process env vars > .env file > AWS SM.

        Lower-priority sources are loaded first so that higher-priority
        sources overwrite them during the merge.
        """
        self._keys.clear()
        self._aws_cache.clear()

        # --- Lowest priority: AWS Secrets Manager ----------------------
        aws_keys: dict[str, str] = {}
        if self._aws_secret_name and self._aws_region:
            aws_keys = self._load_aws_secrets(self._aws_secret_name, self._aws_region)
        for name, value in aws_keys.items():
            self._keys[name] = _KeyEntry(name=name, value=value, source="aws_sm")
            logger.debug("Loaded key %s from aws_sm", name)

        # --- Medium priority: .env file --------------------------------
        if self._env_file_path is not None:
            dotenv_keys = self._load_dotenv(self._env_file_path)
        else:
            dotenv_keys = {}
        for name, value in dotenv_keys.items():
            self._keys[name] = _KeyEntry(name=name, value=value, source="dotenv")
            logger.debug("Loaded key %s from dotenv", name)

        # --- Highest priority: process environment variables -----------
        env_keys = self._load_env_vars()
        for name, value in env_keys.items():
            self._keys[name] = _KeyEntry(name=name, value=value, source="env")
            logger.debug("Loaded key %s from env", name)

    # ------------------------------------------------------------------
    # Public query methods
    # ------------------------------------------------------------------

    def get_keys_for_servers(self, server_names: list[str]) -> dict[str, str]:
        """Return env var dict for the given MCP server names.

        Only includes keys that have non-empty values in the registry.
        Uses prefix matching: if a task server name like
        ``google-workspace_list`` starts with a known map key like
        ``google-workspace``, the map entry is used.
        """
        server_key_map = self.get_server_key_map()
        result: dict[str, str] = {}

        for server in server_names:
            required_vars = self._resolve_server_keys(server, server_key_map)
            for var_name in required_vars:
                entry = self._keys.get(var_name)
                if entry is not None and entry.value:
                    result[var_name] = entry.value

        return result
    @staticmethod
    def _resolve_server_keys(
        server: str, server_key_map: dict[str, list[str]]
    ) -> list[str]:
        """Resolve a task server name to its required env var names.

        Tries exact match first, then falls back to longest-prefix match.
        This handles cases like ``google-workspace_list`` matching the
        ``google-workspace`` key map entry.
        """
        # Exact match
        if server in server_key_map:
            return server_key_map[server]

        # Prefix match — pick the longest matching key
        best_match = ""
        for map_key in server_key_map:
            if server.startswith(map_key) and len(map_key) > len(best_match):
                best_match = map_key

        return server_key_map.get(best_match, [])

    def has_keys_for_servers(self, server_names: list[str]) -> tuple[bool, list[str]]:
        """Check if all required keys are available.

        Returns ``(all_available, list_of_missing_var_names)``.
        A key is considered missing if it is not loaded or has an empty value.
        Uses prefix matching (see :meth:`_resolve_server_keys`).
        """
        server_key_map = self.get_server_key_map()
        missing: list[str] = []

        for server in server_names:
            required_vars = self._resolve_server_keys(server, server_key_map)
            for var_name in required_vars:
                entry = self._keys.get(var_name)
                if entry is None or not entry.value:
                    if var_name not in missing:
                        missing.append(var_name)

        return (len(missing) == 0, missing)


def redact_secrets(text: str, secret_values: set[str]) -> str:
    """Replace any occurrence of a secret value in text with '***REDACTED***'."""
    result = text
    for secret in secret_values:
        if secret and len(secret) >= 4:  # don't redact very short strings
            result = result.replace(secret, "***REDACTED***")
    return result


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

CREDENTIAL_ERROR_PATTERN = re.compile(
    r"unauthorized|forbidden|api[_\s]?key|authentication\s+failed|"
    r"\b401\b|\b403\b|access[_\s]?denied|invalid[_\s]?token|"
    r"credentials?\s+required",
    re.IGNORECASE,
)


def classify_error(error_text: str) -> str:
    """Return 'missing_key' if error matches credential patterns, else 'agent_error'."""
    if CREDENTIAL_ERROR_PATTERN.search(error_text):
        return "missing_key"
    return "agent_error"
