from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Dict, List

from .types import RunMetrics


class EfficiencyAnalyzer:
    """Tracks topology performance and recommends the most efficient one."""

    def __init__(self, objective_weights=None):
        self.objective_weights = objective_weights or {
            "reliability": 0.55,
            "speed": 0.30,
            "cost": 0.15,
        }
        self.metrics_by_topology: Dict[str, List[RunMetrics]] = defaultdict(list)

    def update(self, topology_key: str, cycle_metrics: RunMetrics) -> float:
        self.metrics_by_topology[topology_key].append(cycle_metrics)
        return self.score(topology_key)

    def score(self, topology_key: str) -> float:
        rows = self.metrics_by_topology.get(topology_key, [])
        if not rows:
            return 0.0

        pass_rate = sum(row.pass_rate for row in rows) / len(rows)
        duration = median(row.duration_seconds for row in rows)
        cost = sum(row.token_or_call_usage for row in rows) / len(rows)
        retries = sum(row.retries_per_test for row in rows) / len(rows)
        recurrence = sum(row.failure_recurrence for row in rows) / len(rows)

        reliability_score = max(0.0, pass_rate - (0.08 * retries) - (0.04 * recurrence))
        speed_score = 1.0 / (1.0 + duration)
        cost_score = 1.0 / (1.0 + cost)

        return (
            self.objective_weights["reliability"] * reliability_score
            + self.objective_weights["speed"] * speed_score
            + self.objective_weights["cost"] * cost_score
        )

    def recommend_topology(self, fallback: str) -> str:
        if not self.metrics_by_topology:
            return fallback
        return max(self.metrics_by_topology.keys(), key=self.score)

    def scores(self) -> Dict[str, float]:
        return {key: self.score(key) for key in self.metrics_by_topology.keys()}
