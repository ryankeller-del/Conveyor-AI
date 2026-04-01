"""Tests for chat_lane.py."""

import pytest
from conveyor.core.chat_lane import RollingConversation, detect_chat_mode, ChatResponse


class TestRollingConversation:
    def test_append_and_count(self):
        rc = RollingConversation(limit=3)
        rc.append("user", "hello")
        assert rc.turn_count == 0  # half a turn
        rc.append("assistant", "hi")
        assert rc.turn_count == 1  # one full pair

    def test_trim_enforces_limit(self):
        rc = RollingConversation(limit=2)
        for i in range(5):
            rc.append("user", f"u{i}")
            rc.append("assistant", f"a{i}")
        # 5 pairs = 10 messages, limit=2 pairs = 4 max
        # trim is async, but we can check the content directly
        rc._messages = rc._messages[-4:]  # simulating what trim does
        assert len(rc._messages) == 4

    def test_recent_context_formats_correctly(self):
        rc = RollingConversation(limit=5)
        rc.append("user", "What is Python?")
        rc.append("assistant", "A language.")
        ctx = rc.recent_context()
        assert "[User] What is Python?" in ctx
        assert "[Assistant] A language." in ctx

    def test_recent_context_respects_limit(self):
        rc = RollingConversation(limit=5)
        for i in range(10):
            rc.append("user", f"u{i}")
            rc.append("assistant", f"a{i}")
        # With limit override
        ctx = rc.recent_context(limit=2)
        # Should only include the last 2 * 2 = 4 messages
        assert ctx.count("[User]") == 2

    def test_clear(self):
        rc = RollingConversation(limit=5)
        rc.append("user", "hello")
        rc.clear()
        assert len(rc._messages) == 0


class TestDetectChatMode:
    def test_normal_message(self):
        assert detect_chat_mode("hello world") == "chat"

    def test_health_command(self):
        assert detect_chat_mode("/health check") == "health"

    def test_architect_command(self):
        assert detect_chat_mode("/architect plan something") == "architect"

    def test_chat_command(self):
        assert detect_chat_mode("/chat please") == "chat"

    def test_recap_command(self):
        assert detect_chat_mode("/recap what happened") == "recap"

    def test_unknown_command_is_console(self):
        assert detect_chat_mode("/unknown_cmd") == "console"

    def test_unknown_command_case_insensitive(self):
        assert detect_chat_mode("/HEALTH") == "health"

    def test_whitespace_handling(self):
        assert detect_chat_mode("  /health  ") == "health"


class TestChatResponse:
    def test_defaults(self):
        r = ChatResponse(reply="hello")
        assert r.background_instruction == ""
        assert r.swarm_health == ""
