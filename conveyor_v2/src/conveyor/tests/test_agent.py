"""Tests for agents/agent.py — SimpleAgent with mocked OpenAI client."""

from unittest.mock import MagicMock
from conveyor.agents.agent import SimpleAgent, AgentResponse


def _make_mock_response(text: str) -> MagicMock:
    msg = MagicMock()
    msg.message.content = text
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestSimpleAgentPrimary:
    def test_successful_primary_call(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _make_mock_response("hello")

        agent = SimpleAgent(
            name="test",
            model="llama3.2",
            system_prompt="be helpful",
            client=client,
        )
        result = agent.run("greet me")

        assert result.text == "hello"
        assert result.model_used == "llama3.2"
        assert result.fallback_used is False
        assert result.error is None
        assert result.latency_ms >= 0

    def test_prompt_includes_context(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _make_mock_response("ok")

        agent = SimpleAgent(
            name="test",
            model="llama3.2",
            system_prompt="be helpful",
            client=client,
        )
        agent.run("main prompt", context="prior context")

        # Verify the system prompt and user message were sent
        call_args = client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert messages[0]["content"] == "be helpful"
        assert "prior context" in messages[1]["content"]
        assert "main prompt" in messages[1]["content"]


class TestSimpleAgentFallback:
    def test_fallback_on_primary_failure(self):
        primary = MagicMock()
        primary.chat.completions.create.side_effect = Exception("timeout")

        fallback = MagicMock()
        fallback.chat.completions.create.return_value = _make_mock_response("recovered")

        agent = SimpleAgent(
            name="test",
            model="coder-model",
            system_prompt="code it",
            client=primary,
            fallback_client=fallback,
        )
        result = agent.run("fix the bug")

        assert result.text == "recovered"
        assert result.fallback_used is True
        assert result.error is None

    def test_all_routes_exhausted(self):
        primary = MagicMock()
        primary.chat.completions.create.side_effect = Exception("fail")

        fb = MagicMock()
        fb.chat.completions.create.side_effect = Exception("also fail")

        agent = SimpleAgent(
            name="test",
            model="m1",
            system_prompt="do it",
            client=primary,
            fallback_client=fb,
        )
        result = agent.run("task")

        assert result.text == ""
        assert result.error is not None
        assert result.fallback_used is True

    def test_no_fallback_configured(self):
        primary = MagicMock()
        primary.chat.completions.create.side_effect = Exception("fail")

        agent = SimpleAgent(
            name="test",
            model="m1",
            system_prompt="do it",
            client=primary,
            # No fallback_client
        )
        result = agent.run("task")

        assert result.text == ""
        assert result.error is not None
        assert result.fallback_used is False
