"""Wave orchestrator — spawning, handoffs, and execution cycle.

This is the engine of the swarm. It sequences agent execution,
manages handoffs between agents, detects hallucination events
during execution, and produces the OrchestrationResult that
the controller wraps into a controller.status() update.

Legacy source: internal wave execution inside SwarmController.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from conveyor.core.types import RunConfig, TaskGoal, SwarmStatus
from conveyor.agents.agent import SimpleAgent, AgentResponse


@dataclass
class WaveResult:
    """Output of a single wave execution."""
    wave_name: str
    wave_index: int
    agent_responses: dict[str, AgentResponse] = field(default_factory=dict)
    handoffs: int = 0
    hallucination_alerts: list[str] = field(default_factory=list)
    failure: bool = False
    failure_reason: str = ""


@dataclass
class OrchestrationResult:
    """Aggregated output from a full orchestration cycle.

    Matches the legacy status keys:
      spawn_count, open_handoff_count, active_topology,
      hallucination_alert_count, latest_hallucination_alert,
      team_ideas_count, wave_name, wave_index
    """
    wave_results: list[WaveResult] = field(default_factory=list)
    spawn_count: int = 0
    open_handoffs: int = 0
    hallucination_alert_count: int = 0
    latest_hallucination_alert: str = ""
    topology: list[str] = field(default_factory=list)
    team_ideas: list[str] = field(default_factory=list)
    recommendation: str = ""


class Orchestrator:
    """Orchestrates wave-based agent execution.

    Thread-safe: no shared mutable state across calls.
    Each execute_wave() call is independent.
    """

    def __init__(
        self,
        agents: dict[str, SimpleAgent],
        memory: Any = None,  # LocalMemory — avoid hard dependency
        guards: Any = None,  # dict[str, Any] — stability, prompt guards
    ) -> None:
        self.agents = agents
        self.memory = memory
        self.guards = guards or {}
        self._wave_index = 0

    def execute_wave(
        self,
        goal: TaskGoal,
        config: RunConfig,
        context: str = "",
        topology: list[str] | None = None,
    ) -> OrchestrationResult:
        """Execute a single wave of agent activity.

        Args:
            goal: The task the swarm is working on.
            config: Runtime configuration.
            context: Prior conversation or execution context.
            topology: List of agent role names to activate in this wave.
                     Defaults to all registered agents.

        Returns:
            OrchestrationResult with per-wave outputs and aggregated status.
        """
        self._wave_index += 1
        wave_name = f"wave-{self._wave_index}"
        active_topology = topology or list(self.agents.keys())

        result = OrchestrationResult(topology=list(active_topology))

        responses: dict[str, AgentResponse] = {}
        alerts: list[str] = []
        handoffs = 0

        # --- Execute agents in wave ---
        for role in active_topology:
            agent = self.agents.get(role)
            if agent is None:
                continue

            prompt = self._build_agent_prompt(goal, role, context)
            agent_response = agent.run(prompt)
            responses[role] = agent_response
            result.spawn_count += 1

            # --- Hallucination check (if guard configured) ---
            guard = self.guards.get("prompt_guard")
            if guard is not None:
                guard_result = guard.evaluate(response_text=agent_response.text)
                if guard_result.confidence < 0.5:
                    alert = f"Low confidence from {role}: {guard_result.confidence:.2f}"
                    alerts.append(alert)
                    result.hallucination_alert_count += 1
                    result.latest_hallucination_alert = alert

            # --- Track memory (if available) ---
            if self.memory is not None and agent_response.text:
                try:
                    self.memory.store_packet(role, goal.language, agent_response.text[:200])
                except Exception:
                    pass  # Memory failures should not crash the wave

        # --- Handoff logic ---
        # In a real implementation, this analyzes agent responses to
        # determine if work should be handed off to a different agent.
        # For now, this is a placeholder matching the legacy structure.
        handoffs = self._determine_handoffs(responses, active_topology)
        result.open_handoffs = handoffs

        # --- Wave result ---
        wave_result = WaveResult(
            wave_name=wave_name,
            wave_index=self._wave_index,
            agent_responses={k: v for k, v in responses.items()},
            handoffs=handoffs,
            hallucination_alerts=alerts,
        )
        result.wave_results.append(wave_result)

        # --- Recommendation ---
        if alerts:
            result.recommendation = "HALTED: hallucination detected"
            wave_result.failure = True
            wave_result.failure_reason = alerts[-1]
        else:
            result.recommendation = "CONTINUE"

        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_agent_prompt(
        self, goal: TaskGoal, role: str, context: str
    ) -> str:
        """Build the prompt for a specific agent in the wave.

        Each agent type needs slightly different prompt structuring.
        This keeps the orchestration logic separate from agent internals.
        """
        base = f"Task: {goal.prompt}\n"
        if goal.target_files:
            base += f"Target files: {', '.join(goal.target_files)}\n"
        if context:
            base += f"\nContext:\n{context}\n"
        base += f"\nYou are the {role} agent. Complete your task."
        return base

    def _determine_handoffs(
        self, responses: dict[str, AgentResponse], topology: list[str]
    ) -> int:
        """Determine how many handoffs are pending between agents.

        A handoff occurs when one agent's output indicates work is
        needed by a different agent type.

        Legacy behaviour: tracked inside the controller's wave loop.
        This is a simplified heuristic — the real logic requires
        analyzing agent responses for handoff signals.
        """
        # Placeholder: no handoff detection yet
        # Real implementation parses agent responses for explicit
        # handoff markers or inferred dependencies.
        return 0

    @property
    def wave_index(self) -> int:
        return self._wave_index
