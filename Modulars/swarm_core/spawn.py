from __future__ import annotations

import time
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


class SpawnManager:
    def __init__(self, registry: AgentRegistry):
        self.registry = registry

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
                    parent_agent="SwarmController",
                    reason=reason,
                    task_scope="Assist current failing cycle with focused remediation hints",
                    files_touched=context.get("candidate_files", []),
                    result_summary=f"{bot_name} spawned for focused support.",
                    duration_seconds=duration,
                    quality_delta=0.05,
                    calls_used=1,
                )
            )

        return records
