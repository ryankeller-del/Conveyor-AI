"""Skill evolution tracking — STUB.

Legacy implementation is inside the 3,978-line controller.py and is opaque.
This stub tracks nothing — the real implementation will be added after
the parallel run phase when actual skill evolution logic can be verified.

Legacy status keys:
  active_skill_count, skill_retool_count, latest_skill_event
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SkillState:
    active_count: int = 0
    retool_count: int = 0
    latest_event: str = ""


class SkillRegistry:
    """Track skill usage and evolution.

    STUB: No-op implementation. Returns zero counts for all metrics.
    """

    def __init__(self) -> None:
        self.state = SkillState()

    def track_usage(self, skill_name: str, agent_name: str) -> None:
        """Record a skill usage event. STUB: no-op."""
        pass

    def retool(self, skill_name: str) -> None:
        """Record a skill retool. STUB: no-op."""
        pass

    def get_state(self) -> SkillState:
        """Return current skill state. STUB: always zeros."""
        return self.state

    def get_status(self) -> dict[str, object]:
        """Return status dict matching legacy keys."""
        return {
            "active_skill_count": self.state.active_count,
            "skill_retool_count": self.state.retool_count,
            "latest_skill_event": self.state.latest_event,
        }
