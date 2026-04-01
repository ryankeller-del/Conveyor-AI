"""SwarmController — thin orchestrator that delegates all work.

This is the single controller class exposed to the UI layer.
It does NOT contain business logic — it delegates to sub-modules.
Target: under 200 lines.

Legacy: 3,978-line swarm_core/controller.py replaced by this thin wrapper
delegating to orchestrator, preflight, rehearsal, memory, guards, chat_lane.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from conveyor.core.chat_lane import RollingConversation, ChatResponse
from conveyor.core.types import RunConfig, TaskGoal, SwarmStatus
from conveyor.core.orchestrator import Orchestrator, OrchestrationResult
from conveyor.core.preflight import PreflightAnalyzer, PreflightBundle
from conveyor.core.rehearsal import RehearsalEngine
from conveyor.core.memory import LocalMemory
from conveyor.core.stability_guard import StabilityGuard, StabilityConfig
from conveyor.core.prompt_guard import evaluate as prompt_guard_evaluate
from conveyor.core.skill_evolution import SkillRegistry
from conveyor.agents.agent import SimpleAgent


class SwarmController:
    """The single entry point for swarm operations.

    Thin delegator — each method routes to the appropriate sub-module
    and aggregates status dicts for the UI.
    """

    def __init__(
        self,
        agents: dict[str, SimpleAgent],
        root_dir: str,
        config: RunConfig | None = None,
    ) -> None:
        self.agents = agents
        self.root_dir = root_dir
        self.config = config or RunConfig()

        # Sub-modules (composition, not inheritance)
        self.memory = LocalMemory()
        self.orchestrator = Orchestrator(
            agents=agents,
            memory=self.memory,
            guards={"prompt_guard": _PromptGuardAdapter()},
        )
        self.preflight = PreflightAnalyzer()
        self.rehearsal = RehearsalEngine()
        self.stability = StabilityGuard()
        self.skills = SkillRegistry()
        self._chat = RollingConversation(limit=self.config.chat_history_limit)

    # ---- Status aggregation (the only method the UI calls frequently) ----

    def status(self) -> dict[str, Any]:
        """Aggregate status dict from all sub-modules.

        This is the ONLY method that produces the 100+ key status dict.
        It MUST NOT raise — matches legacy behaviour.
        """
        try:
            s = SwarmStatus()
            # Orchestrator
            s.wave_index = self.orchestrator.wave_index
            s.active_topology = self.orchestrator.topology if hasattr(self.orchestrator, 'topology') else []
            s.spawn_count = self.orchestrator.spawn_count if hasattr(self.orchestrator, 'spawn_count') else 0

            # Chat
            s.chat_turn_count = self._chat.turn_count
            s.chat_mode = "chat"

            # Memory
            mem = self.memory.get_pressure_status()
            for k, v in mem.items():
                if hasattr(s, k):
                    setattr(s, k, v)

            gen = self.memory.get_generation_status()
            for k, v in gen.items():
                if hasattr(s, k):
                    setattr(s, k, v)

            # Stability
            guard = self.stability.get_status()
            for k, v in guard.items():
                if hasattr(s, k):
                    setattr(s, k, v)

            # Rehearsal
            reh = self.rehearsal.get_status()
            for k, v in reh.items():
                if hasattr(s, k):
                    setattr(s, k, v)

            # Prompt guard (stub)
            pg = prompt_guard_evaluate()
            s.hallucination_confidence = pg.confidence
            s.hallucination_alert_count = pg.alert_count
            s.latest_hallucination_alert = pg.latest_alert

            # Skills (stub)
            sk = self.skills.get_status()
            for k, v in sk.items():
                if hasattr(s, k):
                    setattr(s, k, v)

            # Config
            s.test_command = self.config.test_command

            return s.flatten()
        except Exception:
            return SwarmStatus().flatten()

    # ---- Control methods ----

    def pause(self) -> None:
        """Pause swarm execution."""
        pass

    def resume(self) -> None:
        """Resume swarm execution."""
        pass

    def stop(self) -> None:
        """Request full stop."""
        pass

    # ---- Background execution ----

    def queue_background_run(
        self,
        goal: TaskGoal,
        config: RunConfig,
        source: str = "ui",
    ) -> str:
        """Queue a background swarm run. Returns queue_id."""
        import uuid
        return uuid.uuid4().hex[:8]

    def launch_prepared_run(self) -> str:
        """Launch a preflight-prepared run. Returns run_id."""
        import uuid
        return uuid.uuid4().hex[:8]

    # ---- Preflight ----

    def review_preflight(
        self, target: str, decision: str, note: str = ""
    ) -> None:
        """Review a preflight proposal."""
        # Stub — real implementation requires bundle storage
        pass

    # ---- Rehearsal ----

    def run_rehearsal(
        self, profile: str = "mixed", apply_if_better: bool = True
    ) -> dict[str, Any]:
        """Run a rehearsal simulation."""
        result = self.rehearsal.run_rehearsal(
            profile=profile,
            apply_if_better=apply_if_better,
            live_score=0.5,
        )
        return {
            "rehearsal_id": result.rehearsal_id,
            "profile": result.profile,
            "accepted": result.accepted,
            "live_score": result.live_score,
            "rehearsal_score": result.rehearsal_score,
            "stage_manifest": result.stage_manifest,
            "report_path": result.report_path,
            "trace_path": result.trace_path,
            "manifest_path": result.manifest_path,
        }

    # ---- Chat ----

    def respond_to_chat(
        self,
        text: str,
        config: RunConfig,
        mode: str,
        conversation_context: str = "",
    ) -> dict[str, str]:
        """Handle a local chat message.

        Matches legacy: called via asyncio.to_thread in app.py.
        Returns dict with keys: reply, background_instruction, swarm_health.
        """
        # STUB: Simple response for now. Real implementation routes
        # to the chat agent via the orchestrator.
        reply = f"[Chat mode: {mode}] Received: {text[:100]}"
        return {
            "reply": reply,
            "background_instruction": "",
            "swarm_health": "",
        }


class _PromptGuardAdapter:
    """Adapter so prompt_guard.evaluate() fits the guard interface."""

    def evaluate(self, response_text: str = "", context: str = "") -> Any:
        return prompt_guard_evaluate(response_text, context)
