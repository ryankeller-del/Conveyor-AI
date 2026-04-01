"""Stability guard — ramp levels, guard modes, and interventions.

Monitors swarm execution health and can override or slow down
the swarm when instability is detected. Matches the legacy
guard_mode, ramp_level, guard_interventions status keys.

Legacy source: stability guard code inside SwarmController.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StabilityConfig:
    """Configuration for the stability guard."""
    max_ramp_level: int = 5
    guard_mode: str = "NORMAL"
    failure_threshold: int = 3
    hallucination_threshold: float = 0.3
    intervention_cooldown_waves: int = 2


@dataclass
class StabilityState:
    """Current stability guard state.

    Matches the legacy status keys:
      guard_mode, guard_interventions, latest_guard_action,
      latest_guard_reason, ramp_level
    """
    ramp_level: int = 0
    guard_mode: str = "NORMAL"
    interventions: int = 0
    latest_action: str = ""
    latest_reason: str = ""
    cooldown_remaining: int = 0


class StabilityGuard:
    """Monitors swarm stability and intervenes when thresholds are crossed.

    Thread-safe: all state mutations are simple integer updates.
    """

    def __init__(self, config: StabilityConfig | None = None) -> None:
        self.config = config or StabilityConfig()
        self.state = StabilityState()

    def evaluate(
        self,
        failure_streak: int = 0,
        hallucination_confidence: float = 1.0,
        intervention_needed: bool = False,
        intervention_reason: str = "",
    ) -> StabilityState:
        """Evaluate swarm stability and update state.

        Called at the end of each wave or when an event triggers
        a stability check.

        Args:
            failure_streak: Number of consecutive wave failures.
            hallucination_confidence: Current confidence (1.0 = confident,
                low = uncertain).
            intervention_needed: Explicit override request from caller.
            intervention_reason: Human-readable reason for intervention.

        Returns:
            Updated StabilityState.
        """
        # Cooldown decrement
        if self.state.cooldown_remaining > 0:
            self.state.cooldown_remaining -= 1

        # --- Guard mode evaluation ---
        low_confidence = hallucination_confidence < self.config.hallucination_threshold

        if intervention_needed or low_confidence:
            reason = intervention_reason or (
                "low confidence" if low_confidence else "explicit request"
            )
            # Only intervene if cooldown has expired
            if self.state.cooldown_remaining <= 0:
                self.state.interventions += 1
                self.state.cooldown_remaining = self.config.intervention_cooldown_waves
                self.state.latest_action = "intervened"
                self.state.latest_reason = reason
                self.state.ramp_level = min(
                    self.state.ramp_level + 1,
                    self.config.max_ramp_level,
                )
        elif failure_streak >= self.config.failure_threshold:
            # Escalate ramp on sustained failures
            self.state.ramp_level = min(
                self.state.ramp_level + 1,
                self.config.max_ramp_level,
            )
            self.state.latest_action = "ramp escalated"
            self.state.latest_reason = f"failure streak: {failure_streak}"
        else:
            # Things are stabilising — slow ramp descent
            if self.state.ramp_level > 0 and failure_streak == 0:
                self.state.ramp_level -= 1
                self.state.latest_action = "ramp reduced"
                self.state.latest_reason = "stable execution"
            else:
                self.state.latest_action = "no change"
                self.state.latest_reason = ""

        # Guard mode based on ramp level
        if self.state.ramp_level >= 4:
            self.state.guard_mode = "STRICT"
        elif self.state.ramp_level >= 2:
            self.state.guard_mode = "ELEVATED"
        else:
            self.state.guard_mode = "NORMAL"

        return self.state

    def reset(self) -> None:
        """Reset guard to default state."""
        self.state = StabilityState()

    def get_status(self) -> dict[str, object]:
        """Return status dict matching legacy keys."""
        return {
            "guard_mode": self.state.guard_mode,
            "guard_interventions": self.state.interventions,
            "latest_guard_action": self.state.latest_action,
            "latest_guard_reason": self.state.latest_reason,
            "ramp_level": self.state.ramp_level,
        }
