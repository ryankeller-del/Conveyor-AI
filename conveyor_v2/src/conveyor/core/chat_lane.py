"""Chat lane — conversation management for the local chat interface.

Handles rolling conversation buffers, chat mode detection,
and local bot responses. Thread-safe for use via asyncio.to_thread.

Legacy source: swarm_core/chat_lane.py (RollingConversation, detect_chat_mode)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RollingConversation:
    """A rolling buffer of conversation messages.

    Legacy behaviour: RollingConversation(limit=N) keeps the most recent N
    turn pairs (user + assistant). When the limit is exceeded, oldest messages
    are dropped. This is used to inject conversation context into agent prompts.

    Thread-safe: append and trim can be interleaved via asyncio.to_thread.
    """

    limit: int = 8
    _messages: list[dict[str, Any]] = field(default_factory=list)

    def append(self, role: str, content: str, raw: Any = None) -> None:
        """Add a message to the buffer.

        Args:
            role: "user" or "assistant"
            content: message text
            raw: optional original message object (e.g. cl.Message)
        """
        self._messages.append({
            "role": role,
            "content": content,
            "raw": raw,
        })

    async def trim(self) -> None:
        """Enforce the message limit.

        Trims from the oldest messages when the buffer exceeds the limit.
        The limit counts turn *pairs*, so max messages = limit * 2.
        """
        max_msgs = self.limit * 2
        if len(self._messages) > max_msgs:
            self._messages = self._messages[-max_msgs:]

    def recent_context(self, limit: int | None = None) -> str:
        """Return a formatted string of recent conversation context.

        Args:
            limit: Override the number of messages to include.
                   Defaults to the buffer's limit * 2.

        Returns:
            Formatted conversation string for injection into agent prompts.
        """
        msgs = self._messages
        if limit is not None:
            msgs = msgs[-limit * 2:]
        parts = []
        for msg in msgs:
            role_label = "User" if msg["role"] == "user" else "Assistant"
            parts.append(f"[{role_label}] {msg['content']}")
        return "\n".join(parts)

    @property
    def turn_count(self) -> int:
        """Total number of conversation turns (message pairs)."""
        return len(self._messages) // 2

    def clear(self) -> None:
        """Reset the buffer."""
        self._messages.clear()


def detect_chat_mode(text: str) -> str:
    """Detect the chat mode from a user text input.

    Legacy behaviour from app.py's on_message handler.
    Returns the mode string used to route the user message.

    Modes:
      - "health" — text starts with /health
      - "architect" — text starts with /architect
      - "chat" — text starts with /chat (or doesn't match other prefixes)
      - "recap" — handled separately via _handle_local_chat

    Any text starting with / that isn't a recognized mode is considered
    a "command console" message and should be rejected from local chat.
    """
    normalized = text.strip().lower()

    if normalized.startswith("/health"):
        return "health"
    if normalized.startswith("/architect"):
        return "architect"
    if normalized.startswith("/chat"):
        return "chat"
    if normalized.startswith("/recap"):
        return "recap"

    # Any other slash prefix → belongs on the command console
    if normalized.startswith("/"):
        return "console"

    # Normal message
    return "chat"


@dataclass
class ChatResponse:
    """Response from local chat handling.

    Returned by respond_to_local() / respond_to_chat().
    """
    reply: str
    background_instruction: str = ""
    swarm_health: str = ""
