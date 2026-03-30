from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, List


@dataclass
class ChatRecord:
    role: str
    content: str
    message: Any
    visible: bool = True


class RollingConversation:
    def __init__(self, limit: int = 25):
        self.limit = max(1, int(limit))
        self._records: List[ChatRecord] = []

    def append(self, role: str, content: str, message: Any, visible: bool = True) -> None:
        if not visible:
            return
        self._records.append(
            ChatRecord(
                role=str(role or "assistant"),
                content=str(content or ""),
                message=message,
                visible=True,
            )
        )

    def visible_count(self) -> int:
        return len([item for item in self._records if item.visible])

    def recent_context(self, limit: int = 8) -> str:
        limit = max(1, int(limit))
        tail = [item for item in self._records if item.visible][-limit:]
        return "\n".join(f"{item.role.title()}: {item.content}" for item in tail if item.content)

    def entries(self) -> List[ChatRecord]:
        return list(self._records)

    async def trim(self) -> int:
        removed = 0
        while self.visible_count() > self.limit and self._records:
            record = self._records.pop(0)
            if record.visible:
                removed += 1
                remover = getattr(record.message, "remove", None)
                if callable(remover):
                    result = remover()
                    if inspect.isawaitable(result):
                        await result
        return removed

    def clear(self) -> None:
        self._records.clear()


def detect_chat_mode(text: str) -> str:
    normalized = (text or "").strip().lower()
    if normalized.startswith("/health"):
        return "health"
    if normalized.startswith("/architect"):
        return "architect"
    if normalized.startswith("/chat"):
        return "chat"
    if any(
        phrase in normalized
        for phrase in (
            "swarm health",
            "health of the swarm",
            "status of the swarm",
            "report on swarm health",
        )
    ):
        return "health"
    if any(
        phrase in normalized
        for phrase in (
            "master architect",
            "architect",
            "make changes",
            "change the system",
            "queue instruction",
            "background instruction",
        )
    ):
        return "architect"
    return "chat"


def should_launch_swarm(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized in {"/swarm", "/run"} or normalized.startswith("/swarm ") or normalized.startswith("/run ")
