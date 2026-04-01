"""Rehearsal simulation — STUB with minimal viable implementation.

A rehearsal is a dry run of the swarm on a given profile to see
if a different stage manifest would produce a better score.
If the rehearsal score beats the live score, the new manifest
is hot-swapped.

Legacy implementation is opaque (inside the 3,978-line controller).
This provides the interface structure and a minimal implementation.
Full behaviour will be added after the parallel run phase.

Legacy status keys:
  rehearsal_state, rehearsal_profile, rehearsal_report_path,
  rehearsal_manifest_path, rehearsal_trace_path
  stage_manifest_current, stage_manifest_next,
  stage_manifest_score, stage_manifest_profile
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from conveyor.core.types import RehearsalResults


@dataclass
class StageManifest:
    """A candidate stage configuration for the swarm to execute with."""
    name: str
    tools: list[str] = field(default_factory=list)
    profile: str = ""
    score: float = 0.0
    checklist: list[str] = field(default_factory=list)
    preload: list[str] = field(default_factory=list)


class RehearsalEngine:
    """Run rehearsal simulations compare against live performance.

    STUB: Returns consistent but simplified results.
    """

    def __init__(self) -> None:
        self._current_manifest: StageManifest | None = None
        self._last_rehearsal: RehearsalResults | None = None
        self._state: str = "IDLE"

    def set_manifest(self, manifest: StageManifest) -> None:
        """Set the current live stage manifest."""
        self._current_manifest = manifest

    def get_manifest(self) -> StageManifest | None:
        """Get the current live stage manifest."""
        return self._current_manifest

    def run_rehearsal(
        self,
        profile: str = "mixed",
        apply_if_better: bool = True,
        live_score: float = 0.5,
    ) -> RehearsalResults:
        """Run a rehearsal simulation.

        STUB: Generates a rehearsal score slightly above/below
        the live score to exercise the comparison logic.
        In production, this would actually run the swarm agents
        in simulation mode.

        Args:
            profile: Rehearsal profile name.
            apply_if_better: Swap manifest if rehearsal score > live score.
            live_score: Current live performance score (0.0-1.0).

        Returns:
            RehearsalResults with comparison details.
        """
        self._state = "RUNNING"

        rehearsal_id = uuid.uuid4().hex[:8]

        # STUB: Generate a score near the live score for testing
        # In production, this would be a real simulation run
        rehearsal_score = max(0.0, min(1.0, live_score + (0.05 * (hash(rehearsal_id) % 3 - 1))))

        accepted = apply_if_better and rehearsal_score > live_score

        if accepted and self._current_manifest is not None:
            self._current_manifest.score = rehearsal_score

        result = RehearsalResults(
            rehearsal_id=rehearsal_id,
            profile=profile,
            accepted=accepted,
            live_score=live_score,
            rehearsal_score=rehearsal_score,
            stage_manifest={
                "current_stage": self._current_manifest.name if self._current_manifest else "none",
                "next_stage": "",
            } if self._current_manifest else {},
            report_path=f"reports/rehearsal_{rehearsal_id}.json",
            trace_path=f"reports/rehearsal_{rehearsal_id}.trace",
            manifest_path="",
        )

        self._last_rehearsal = result
        self._state = "COMPLETE"

        return result

    def get_status(self) -> dict[str, Any]:
        """Return rehearsal status dict matching legacy keys."""
        manifest = self._current_manifest
        result = self._last_rehearsal

        return {
            "stage_manifest_current": manifest.name if manifest else "n/a",
            "stage_manifest_next": "",
            "stage_manifest_score": manifest.score if manifest else 0.0,
            "stage_manifest_profile": manifest.profile if manifest else "n/a",
            "stage_manifest_preload_bundle": manifest.preload if manifest else [],
            "stage_manifest_required_tools": manifest.tools if manifest else [],
            "stage_manifest_report_checklist": manifest.checklist if manifest else [],
            "rehearsal_state": self._state,
            "rehearsal_profile": result.profile if result else "n/a",
            "rehearsal_report_path": result.report_path if result else "n/a",
            "rehearsal_manifest_path": result.manifest_path if result else "n/a",
            "rehearsal_trace_path": result.trace_path if result else "n/a",
        }
