from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .types import RunConfig, RunMetrics, RunSnapshot


@dataclass
class SkillEvent:
    action: str
    skill_name: str
    reason: str
    impact_delta: float = 0.0


class SkillEvolutionManager:
    def __init__(self):
        self.candidates: Dict[str, int] = {}
        self.active_skills: Dict[str, Dict] = {}
        self.retool_count = 0
        self.last_event = ""

    def observe_pattern(self, pattern_key: str) -> None:
        if not pattern_key:
            return
        self.candidates[pattern_key] = self.candidates.get(pattern_key, 0) + 1

    def evaluate(
        self,
        snapshot: RunSnapshot,
        metric: RunMetrics,
        config: RunConfig,
    ) -> Optional[SkillEvent]:
        if not config.skill_evolution_enabled:
            return None

        # Promote candidate to skill only after enough evidence.
        for pattern, count in sorted(self.candidates.items(), key=lambda p: p[1], reverse=True):
            if count >= max(1, config.skill_min_evidence_count) and pattern not in self.active_skills:
                skill_name = f"skill_{pattern}"
                self.active_skills[pattern] = {
                    "name": skill_name,
                    "created_wave": snapshot.wave_index,
                    "baseline_pass_rate": metric.pass_rate,
                    "last_retool_wave": -9999,
                }
                self.last_event = f"PROMOTE {skill_name}: evidence={count}"
                return SkillEvent(
                    action="PROMOTE",
                    skill_name=skill_name,
                    reason=f"pattern={pattern} reached evidence={count}",
                    impact_delta=0.0,
                )

        # Retool skills if observed outcome regresses.
        for pattern, meta in self.active_skills.items():
            baseline = float(meta.get("baseline_pass_rate", metric.pass_rate))
            delta = metric.pass_rate - baseline
            wave_gap = snapshot.wave_index - int(meta.get("last_retool_wave", -9999))
            if (
                delta <= float(config.skill_negative_delta_threshold)
                and wave_gap >= max(1, config.skill_retool_cooldown_waves)
            ):
                meta["last_retool_wave"] = snapshot.wave_index
                meta["baseline_pass_rate"] = metric.pass_rate
                self.retool_count += 1
                self.last_event = f"RETOOL {meta['name']}: delta={delta:.3f}"
                return SkillEvent(
                    action="RETOOL",
                    skill_name=str(meta["name"]),
                    reason=f"negative pass-rate delta {delta:.3f}",
                    impact_delta=delta,
                )
        return None
