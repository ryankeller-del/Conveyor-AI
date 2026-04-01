"""Session state — UI-agnostic session management.

Replaces the legacy cl.user_session.get()/set() pattern
with a clean abstraction that works with any UI framework
(Chainlit, CLI, Telegram, etc.).

Legacy source: cl.user_session throughout app.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionState:
    """Holds all session-scoped state that the UI previously stored
    directly in cl.user_session.

    This isolates session concerns from the chainlit-specific layer,
    enabling the same controller to be used from a CLI, web UI,
    or other gateway.
    """
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    active_test_command: str = "python -m pytest {tests_path} -q"
    run_config_overrides: dict[str, Any] = field(default_factory=dict)
    chat_transcript: Any = None  # RollingConversation — set externally
    paused: bool = False
    stopped: bool = False
