"""Pluggable human interaction interfaces for the evolver.

When ``human_in_the_loop`` is enabled in the config, the evolver gains a
``request_human_action`` tool that routes messages to a human operator via
a configurable channel (terminal stdin, Telegram, etc.).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Protocol

logger = logging.getLogger(__name__)


class HumanInterface(Protocol):
    """Send a message to a human and wait for their response."""

    def send(self, message: str) -> None: ...

    def receive(self, timeout: float | None = None) -> str: ...

    def handle(self, message: str, action_type: str = "information") -> str:
        """Tool executor entry point: send message, block, return response."""
        self.send(f"[{action_type.upper()}] {message}")
        return self.receive()


class StdinInterface:
    """Local interactive interface (terminal, like Claude Code)."""

    def send(self, message: str) -> None:
        print(f"\n{'=' * 70}", file=sys.stderr)
        print("EVOLVER REQUEST:", file=sys.stderr)
        print(message, file=sys.stderr)
        print(f"{'=' * 70}", file=sys.stderr)

    def receive(self, timeout: float | None = None) -> str:
        return input("\n>>> Your response (or press ENTER to skip): ") or "(no response)"

    def handle(self, message: str, action_type: str = "information") -> str:
        logger.info("⏸️  WAITING FOR HUMAN REVIEW (stdin) — evolver is paused")
        self.send(f"[{action_type.upper()}] {message}")
        response = self.receive()
        logger.info("▶️  Human responded — evolver resuming")
        return response


class TelegramInterface:
    """Telegram bot interface for remote interaction.

    Uses only ``urllib.request`` — no external dependencies.
    """

    def __init__(self, bot_token: str, chat_id: int, timeout: float = 600):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self._last_update_id = 0

    def send(self, message: str) -> None:
        import urllib.request

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = json.dumps({
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }).encode()
        req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=30)
        except Exception as e:
            logger.error("Telegram send failed: %s", e)

    def receive(self, timeout: float | None = None) -> str:
        import urllib.request

        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            url = (
                f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
                f"?offset={self._last_update_id + 1}&timeout=30"
            )
            try:
                resp = json.loads(urllib.request.urlopen(url, timeout=35).read())
            except Exception as e:
                logger.warning("Telegram poll error: %s", e)
                time.sleep(5)
                continue
            for update in resp.get("result", []):
                self._last_update_id = update["update_id"]
                msg = update.get("message", {})
                if msg.get("chat", {}).get("id") == self.chat_id and msg.get("text"):
                    return msg["text"]
            time.sleep(1)
        return "(timeout - no human response)"

    def handle(self, message: str, action_type: str = "information") -> str:
        logger.info("⏸️  WAITING FOR HUMAN REVIEW (Telegram) — evolver is paused")
        self.send(f"[{action_type.upper()}] {message}")
        response = self.receive()
        logger.info("▶️  Human responded via Telegram — evolver resuming")
        return response


class SlackInterface:
    """Slack bot interface for group channel interaction.

    Uses only ``urllib.request`` — no external dependencies.
    Requires bot scopes: ``chat:write``, ``channels:history``, ``channels:read``.
    """

    def __init__(self, bot_token: str, channel_id: str, timeout: float = 600):
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.timeout = timeout
        self._bot_user_id: str | None = None
        self._last_ts: str = ""

    def _api(self, method: str, data: dict | None = None) -> dict:
        import urllib.request

        url = f"https://slack.com/api/{method}"
        headers = {"Authorization": f"Bearer {self.bot_token}"}
        if data is not None:
            payload = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
            req = urllib.request.Request(url, payload, headers)
        else:
            req = urllib.request.Request(url, headers=headers)
        return json.loads(urllib.request.urlopen(req, timeout=30).read())

    def _get_bot_user_id(self) -> str:
        if not self._bot_user_id:
            resp = self._api("auth.test")
            self._bot_user_id = resp.get("user_id", "")
        return self._bot_user_id

    def send(self, message: str) -> None:
        try:
            resp = self._api("chat.postMessage", {
                "channel": self.channel_id,
                "text": message,
            })
            self._last_ts = resp.get("ts", "")
        except Exception as e:
            logger.error("Slack send failed: %s", e)

    def receive(self, timeout: float | None = None) -> str:
        bot_id = self._get_bot_user_id()
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            try:
                resp = self._api("conversations.history", {
                    "channel": self.channel_id,
                    "oldest": self._last_ts,
                    "limit": 10,
                })
                for msg in resp.get("messages", []):
                    # Skip bot's own messages and messages at exactly _last_ts
                    if msg.get("user") == bot_id:
                        continue
                    if msg.get("ts", "") <= self._last_ts:
                        continue
                    if msg.get("text"):
                        return msg["text"]
            except Exception as e:
                logger.warning("Slack poll error: %s", e)
            time.sleep(3)
        return "(timeout - no human response)"

    def handle(self, message: str, action_type: str = "information") -> str:
        logger.info("⏸️  WAITING FOR HUMAN REVIEW (Slack) — evolver is paused")
        self.send(f"*[{action_type.upper()}]*\n{message}")
        response = self.receive()
        logger.info("▶️  Human responded via Slack — evolver resuming")
        return response


def create_interface(config: dict) -> HumanInterface:
    """Factory: create a HumanInterface from config dict.

    Config keys (all go into ``config.extra``):
      - ``human_interface``: ``"stdin"`` (default), ``"telegram"``, or ``"slack"``
      - ``telegram_bot_token`` / ``telegram_chat_id``: for Telegram
      - ``slack_bot_token`` / ``slack_channel_id``: for Slack
    """
    itype = config.get("human_interface", "stdin")
    timeout = float(config.get("human_review_timeout", 600))
    if itype == "telegram":
        token = config.get("telegram_bot_token")
        chat_id = config.get("telegram_chat_id")
        if not token or not chat_id:
            raise ValueError(
                "Telegram interface requires 'telegram_bot_token' and "
                "'telegram_chat_id' in config"
            )
        logger.info("Human interface: Telegram (chat_id=%s, timeout=%ss)", chat_id, timeout)
        return TelegramInterface(bot_token=token, chat_id=int(chat_id), timeout=timeout)
    if itype == "slack":
        token = config.get("slack_bot_token")
        channel = config.get("slack_channel_id")
        if not token or not channel:
            raise ValueError(
                "Slack interface requires 'slack_bot_token' and "
                "'slack_channel_id' in config"
            )
        logger.info("Human interface: Slack (channel=%s, timeout=%ss)", channel, timeout)
        return SlackInterface(bot_token=token, channel_id=channel, timeout=timeout)
    logger.info("Human interface: stdin (local terminal)")
    return StdinInterface()
