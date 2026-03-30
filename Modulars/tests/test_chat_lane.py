import asyncio
from pathlib import Path

from swarm_core.bots import SimpleAgent
from swarm_core.chat_lane import RollingConversation, detect_chat_mode, should_launch_swarm
from swarm_core.controller import SwarmController


class _TrackedMessage:
    def __init__(self, content: str):
        self.content = content
        self.removed = False

    async def remove(self):
        self.removed = True


class _JsonClient:
    def __init__(self, content: str):
        self.content = content

    class _Chat:
        def __init__(self, outer):
            self._outer = outer

        class _Completions:
            def __init__(self, outer):
                self._outer = outer

            def create(self, **kwargs):
                content = self._outer.content

                class _Msg:
                    pass

                _Msg.content = content

                class _Choice:
                    message = _Msg()

                class _Resp:
                    choices = [_Choice()]

                return _Resp()

        @property
        def completions(self):
            return self._Completions(self._outer)

    @property
    def chat(self):
        return self._Chat(self)


def _agent(name: str, client=None, local: bool = False):
    return SimpleAgent(
        name=name,
        system_prompt="",
        client=client,
        model="local-model",
        is_local=local,
    )


def test_rolling_conversation_trims_oldest_visible_messages():
    transcript = RollingConversation(limit=25)
    messages = []
    for index in range(26):
        msg = _TrackedMessage(f"message {index}")
        messages.append(msg)
        transcript.append("user" if index % 2 == 0 else "assistant", msg.content, msg)

    removed = asyncio.run(transcript.trim())
    assert removed == 1
    assert transcript.visible_count() == 25
    assert messages[0].removed is True
    assert messages[-1].removed is False


def test_detect_chat_mode_and_swarm_launch_helpers():
    assert detect_chat_mode("/health") == "health"
    assert detect_chat_mode("Please act as master architect") == "architect"
    assert detect_chat_mode("hello there") == "chat"
    assert should_launch_swarm("/swarm build the api") is True
    assert should_launch_swarm("/runjson {}") is False


def test_controller_chat_request_queues_architect_instruction(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test", client=_JsonClient('{"reply":"test","background_instruction":"","swarm_health":"","mode":"chat"}')),
        coder_agent=_agent("coder", client=_JsonClient('{"reply":"coder","background_instruction":"","swarm_health":"","mode":"chat"}')),
        judge_agent=_agent("judge", client=_JsonClient('{"reply":"judge","background_instruction":"","swarm_health":"","mode":"chat"}')),
        root_dir=str(tmp_path),
        chat_agent=_agent(
            "chat",
            client=_JsonClient(
                '{"reply":"I queued the change.","background_instruction":"Review the swarm stage manifest and keep the chat lane open.","swarm_health":"healthy","mode":"architect"}'
            ),
            local=True,
        ),
    )

    result = controller.respond_to_chat("please review the swarm architecture", mode="architect")
    status = controller.status()

    assert result["mode"] == "architect"
    assert "queued the change" in result["reply"].lower()
    assert "keep the chat lane open" in result["background_instruction"].lower()
    assert status["queued_architect_instruction_count"] == 1
    assert "chat lane open" in status["latest_architect_instruction"].lower()
    assert status["chat_mode"] == "architect"
