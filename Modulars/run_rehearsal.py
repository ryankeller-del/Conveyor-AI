from __future__ import annotations

import argparse
import os
import uuid
from dataclasses import asdict
from typing import List

from swarm_core.controller import SwarmController
from swarm_core.rehearsal import OfflineRehearsalManager, stage_manifest_from_snapshot
from swarm_core.types import ControllerPhase, RunConfig, RunSnapshot, RunState, TaskGoal


def build_standalone_snapshot(goal: TaskGoal, stage: str = "BOOTSTRAP") -> RunSnapshot:
    return RunSnapshot(
        run_id=f"rehearsal-{uuid.uuid4().hex[:8]}",
        state=RunState.IDLE,
        phase=ControllerPhase.TEST_WAVE_GEN,
        wave_index=0,
        wave_name=stage,
        total_tests=0,
        passing_tests=0,
        no_gain_waves=0,
        active_topology=["TestBot", "LocalCoder", "JudgeBot"],
        artifacts_path="",
        active_memory_format="NARRATIVE",
        recommendation=None,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the swarm rehearsal offline.")
    parser.add_argument(
        "--profile",
        default="mixed",
        choices=["healthy", "mixed", "stress", "balanced"],
        help="Deterministic rehearsal profile to simulate.",
    )
    parser.add_argument(
        "--goal",
        default="Prepare the swarm with a narrow, deterministic rehearsal.",
        help="Short goal prompt used to seed the simulated rehearsal.",
    )
    parser.add_argument(
        "--target-files",
        default="app_v3.py",
        help="Comma-separated list of target files for the rehearsal goal.",
    )
    parser.add_argument(
        "--language",
        default="general",
        help="Language label used for the rehearsal goal.",
    )
    parser.add_argument(
        "--stage",
        default="BOOTSTRAP",
        choices=[
            "BOOTSTRAP",
            "SEED_LOADING",
            "TEST_WAVE_GEN",
            "IMPLEMENT",
            "HALLUCINATION_GUARD",
            "JUDGE",
            "STABILIZATION",
            "MEMORY_COMPACTION",
            "REPORTING",
        ],
        help="Stage to start the rehearsal from.",
    )
    parser.add_argument(
        "--root",
        default=os.path.dirname(os.path.abspath(__file__)),
        help="Repository root used to store rehearsal artifacts.",
    )
    parser.add_argument(
        "--apply-if-better",
        action="store_true",
        help="Allow the rehearsal to hot-swap the manifest if it beats the live score.",
    )
    return parser.parse_args()


def run_rehearsal(args: argparse.Namespace) -> dict:
    target_files = [item.strip() for item in str(args.target_files).split(",") if item.strip()]
    goal = TaskGoal(prompt=str(args.goal), target_files=target_files or ["app_v3.py"], language=str(args.language))
    snapshot = build_standalone_snapshot(goal, stage=str(args.stage))
    config = RunConfig(
        rehearsal_enabled=True,
        rehearsal_profile=str(args.profile),
        stage_manifest_hot_swap_enabled=True,
    )
    live_manifest = stage_manifest_from_snapshot(
        snapshot,
        config,
        current_stage=snapshot.wave_name,
        next_stage="IMPLEMENT",
        source="live",
        profile="live",
        note="Standalone rehearsal baseline",
    )

    manager = OfflineRehearsalManager(str(args.root))
    outcome = manager.simulate(
        snapshot=snapshot,
        config=config,
        profile=str(args.profile),
        live_manifest=live_manifest,
    )

    return {
        "goal": asdict(goal),
        "rehearsal_id": outcome.rehearsal_id,
        "profile": outcome.profile,
        "accepted": outcome.accepted,
        "live_score": outcome.live_score,
        "rehearsal_score": outcome.rehearsal_score,
        "report_path": outcome.report_path,
        "manifest_path": outcome.manifest_path,
        "trace_path": outcome.trace_path,
        "stage_current": outcome.manifest.current_stage,
        "stage_next": outcome.manifest.next_stage,
        "preload_bundle": outcome.manifest.preload_bundle,
        "required_tools": outcome.manifest.required_tools,
        "report_checklist": outcome.manifest.report_checklist,
        "stage_timeline": outcome.stage_timeline,
        "failure_trace": outcome.failure_trace,
    }


def main() -> int:
    args = parse_args()
    result = run_rehearsal(args)

    print("Offline rehearsal completed.")
    print(f"Rehearsal ID: {result['rehearsal_id']}")
    print(f"Profile: {result['profile']}")
    print(f"Accepted: {result['accepted']}")
    print(f"Live Score: {result['live_score']:.4f}")
    print(f"Rehearsal Score: {result['rehearsal_score']:.4f}")
    print(f"Current Stage: {result['stage_current']}")
    print(f"Next Stage: {result['stage_next']}")
    print(f"Report: {result['report_path']}")
    print(f"Manifest: {result['manifest_path']}")
    print(f"Trace: {result['trace_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
