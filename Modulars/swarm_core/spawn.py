from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Dict, List

from .types import SpawnRecord


@dataclass
class AgentDescriptor:
    name: str
    role: str
    enabled: bool = True


class AgentRegistry:
    def __init__(self):
        self._agents: Dict[str, AgentDescriptor] = {}

    def register(self, descriptor: AgentDescriptor) -> None:
        self._agents[descriptor.name] = descriptor

    def list_enabled(self) -> List[str]:
        return [name for name, descriptor in self._agents.items() if descriptor.enabled]


@dataclass
class DelegationDecision:
    approved: bool
    benefit_score: float
    risk_score: float
    rationale: str
    plan_summary: str


class DelegationPlanner:
    def plan(self, context: Dict) -> DelegationDecision:
        repeated_failure = int(context.get("repeated_failure_count", 0))
        judge_confidence = float(context.get("judge_confidence", 1.0))
        diff_complexity = int(context.get("diff_complexity", 0))
        pass_rate = float(context.get("pass_rate", 0.0))

        benefit_score = 0.0
        if repeated_failure >= 2:
            benefit_score += 0.35
        if judge_confidence < 0.55:
            benefit_score += 0.25
        if diff_complexity > 80:
            benefit_score += 0.2
        if pass_rate < 1.0:
            benefit_score += 0.2

        active_agents = len(context.get("active_agents", []))
        max_agents = max(1, int(context.get("max_concurrent_agents", 1)))
        capacity_ratio = active_agents / max_agents
        cooldown_violation = bool(context.get("cooldown_violation", False))
        risk_score = min(1.0, 0.5 * capacity_ratio + (0.5 if cooldown_violation else 0.0))

        threshold = float(context.get("spawn_min_benefit_score", 0.25))
        approved = (benefit_score - risk_score) >= threshold and not cooldown_violation

        rationale = (
            f"benefit={benefit_score:.2f}, risk={risk_score:.2f}, "
            f"threshold={threshold:.2f}, cooldown_violation={cooldown_violation}"
        )
        plan_summary = (
            "Delegation plan: isolate failing scope, assign specialist, "
            "return concise remediation proposal and affected files only."
        )
        return DelegationDecision(
            approved=approved,
            benefit_score=benefit_score,
            risk_score=risk_score,
            rationale=rationale,
            plan_summary=plan_summary,
        )


class SpawnManager:
    def __init__(self, registry: AgentRegistry):
        self.registry = registry
        self.planner = DelegationPlanner()

    def evaluate_and_spawn(self, context: Dict, max_concurrent_agents: int) -> List[SpawnRecord]:
        active_count = len(context.get("active_agents", []))
        if active_count >= max_concurrent_agents:
            return []

        repeated_failure = int(context.get("repeated_failure_count", 0)) >= 2
        low_confidence = float(context.get("judge_confidence", 1.0)) < 0.55
        flaky_tests = bool(context.get("flaky_tests_detected", False))
        diff_complexity = int(context.get("diff_complexity", 0)) > 80

        candidates = []
        if repeated_failure:
            candidates.append(("TestRefinerBot", "Repeated failure on same error class"))
        if low_confidence:
            candidates.append(("SecurityBot", "Low confidence in judge fix list"))
        if flaky_tests:
            candidates.append(("RefactorBot", "Flaky tests detected"))
        if diff_complexity:
            candidates.append(("PerfBot", "Large diff complexity hotspot"))

        context = dict(context)
        context["max_concurrent_agents"] = max_concurrent_agents
        decision = self.planner.plan(context)
        if not decision.approved:
            return []

        records: List[SpawnRecord] = []
        for bot_name, reason in candidates:
            if bot_name not in self.registry.list_enabled():
                continue
            if active_count + len(records) >= max_concurrent_agents:
                break

            start = time.time()
            duration = max(0.05, time.time() - start)
            records.append(
                SpawnRecord(
                    agent_name=bot_name,
                    parent_agent=str(context.get("requesting_agent", "SwarmController")),
                    reason=f"{reason} | {decision.rationale}",
                    task_scope=decision.plan_summary,
                    files_touched=context.get("candidate_files", []),
                    result_summary=f"{bot_name} spawned for focused support.",
                    duration_seconds=duration,
                    quality_delta=0.05,
                    calls_used=1,
                    handoff_id=uuid.uuid4().hex[:12],
                    return_required=True,
                    status="OPEN",
                )
            )

        return records
