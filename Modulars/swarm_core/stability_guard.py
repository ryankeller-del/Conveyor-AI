from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .bots import SimpleAgent
from .types import RunConfig, RunMetrics, RunSnapshot


@dataclass
class GuardDecision:
    action: str
    reason: str
    focus: str = ""
    spawn_pause_waves: int = 0


class StabilityGuard:
    def __init__(self, agent: Optional[SimpleAgent] = None):
        self.agent = agent

    def evaluate(
        self,
        snapshot: RunSnapshot,
        metric: RunMetrics,
        recent_metrics: List[RunMetrics],
        config: RunConfig,
    ) -> GuardDecision:
        if not config.stability_guard_enabled:
            return GuardDecision(action="NONE", reason="stability guard disabled")

        failed_waves = int(snapshot.consecutive_failed_waves)
        open_handoffs = int(snapshot.open_handoff_count)
        no_gain = int(snapshot.no_gain_waves)
        retry_pressure = float(metric.retries_per_test)
        pass_rate = float(metric.pass_rate)

        if failed_waves >= max(1, config.guard_halt_failed_waves):
            return GuardDecision(
                action="HALT",
                reason=(
                    f"consecutive_failed_waves={failed_waves} exceeded "
                    f"guard_halt_failed_waves={config.guard_halt_failed_waves}"
                ),
            )

        if open_handoffs >= max(1, config.guard_max_open_handoffs):
            focus = self._propose_focus(snapshot, recent_metrics)
            return GuardDecision(
                action="DEFLECT",
                reason=(
                    f"open_handoffs={open_handoffs} exceeded "
                    f"guard_max_open_handoffs={config.guard_max_open_handoffs}"
                ),
                focus=focus,
                spawn_pause_waves=max(1, config.guard_spawn_pause_waves),
            )

        if retry_pressure >= config.guard_retry_pressure_threshold and pass_rate < 1.0:
            focus = self._propose_focus(snapshot, recent_metrics)
            return GuardDecision(
                action="DEFLECT",
                reason=(
                    f"retry_pressure={retry_pressure:.2f} above "
                    f"guard_retry_pressure_threshold={config.guard_retry_pressure_threshold:.2f}"
                ),
                focus=focus,
                spawn_pause_waves=max(1, config.guard_spawn_pause_waves),
            )

        if no_gain >= max(1, config.guard_no_gain_redirect):
            focus = self._propose_focus(snapshot, recent_metrics)
            return GuardDecision(
                action="REDIRECT",
                reason=(
                    f"no_gain_waves={no_gain} reached "
                    f"guard_no_gain_redirect={config.guard_no_gain_redirect}"
                ),
                focus=focus,
            )

        return GuardDecision(action="NONE", reason="stability metrics within guardrails")

    def _propose_focus(self, snapshot: RunSnapshot, recent_metrics: List[RunMetrics]) -> str:
        if self.agent is None:
            return (
                "Stabilize the top failing path first. Reduce scope to one failing behavior, "
                "close open handoffs, and regain deterministic green tests."
            )

        last = recent_metrics[-1] if recent_metrics else None
        prompt = (
            "Return one concise stabilization objective (single sentence). "
            "Prioritize reducing retries and closing unresolved handoffs.\n"
            f"Snapshot: wave={snapshot.wave_index}, no_gain={snapshot.no_gain_waves}, "
            f"open_handoffs={snapshot.open_handoff_count}, "
            f"consecutive_failed={snapshot.consecutive_failed_waves}.\n"
            f"Latest metric: pass_rate={getattr(last, 'pass_rate', 0.0)}, "
            f"retries_per_test={getattr(last, 'retries_per_test', 0.0)}, "
            f"failure_recurrence={getattr(last, 'failure_recurrence', 0)}."
        )
        response = (self.agent.generate(prompt) or "").strip()
        if not response:
            return (
                "Stabilize one failing behavior before expanding scope; close handoffs and "
                "recover deterministic test pass rate."
            )
        return response.splitlines()[0][:280]
