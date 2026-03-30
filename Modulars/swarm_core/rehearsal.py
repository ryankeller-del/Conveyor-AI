from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from statistics import mean
from typing import Any, Dict, List, Optional

from .types import RehearsalOutcome, RunConfig, RunSnapshot, StageManifest


STAGE_SEQUENCE = [
    "BOOTSTRAP",
    "SEED_LOADING",
    "TEST_WAVE_GEN",
    "IMPLEMENT",
    "HALLUCINATION_GUARD",
    "JUDGE",
    "STABILIZATION",
    "MEMORY_COMPACTION",
    "REPORTING",
]

STAGE_PROTOCOLS: Dict[str, Dict[str, List[str]]] = {
    "BOOTSTRAP": {
        "preload": ["failure_library.jsonl", "failure_rules.md", "reports/"],
        "tools": ["artifact review", "status snapshot", "log inspection"],
        "report": ["record baseline readiness", "confirm seed artifacts exist"],
    },
    "SEED_LOADING": {
        "preload": ["seed pack", "known-good example", "boundary case"],
        "tools": ["pytest", "coverage", "failure replay"],
        "report": ["capture seed quality", "note analyzer input gaps"],
    },
    "TEST_WAVE_GEN": {
        "preload": ["test_command", "coverage gaps", "target files"],
        "tools": ["pytest", "dry-run harness", "artifact review"],
        "report": ["record generated tests", "record approved tests"],
    },
    "IMPLEMENT": {
        "preload": ["current_focus", "failure guidance", "existing code"],
        "tools": ["diff review", "log inspection", "failure memory search"],
        "report": ["capture implementation delta", "note scope changes"],
    },
    "HALLUCINATION_GUARD": {
        "preload": ["import graph", "known symbols", "known APIs"],
        "tools": ["hallucination report", "artifact browser", "log inspection"],
        "report": ["capture unknown symbols", "capture unknown APIs"],
    },
    "JUDGE": {
        "preload": ["test output", "fix list", "failure trace"],
        "tools": ["pytest", "failure replay", "status snapshot"],
        "report": ["capture judge decision", "capture retry reasons"],
    },
    "STABILIZATION": {
        "preload": ["guard thresholds", "handoff ledger", "retry pressure"],
        "tools": ["spawn report", "efficiency report", "failure report"],
        "report": ["record stability interventions", "note population control"],
    },
    "MEMORY_COMPACTION": {
        "preload": ["memory_primitives.md", "memory_formats.json", "failure library"],
        "tools": ["progress log", "failure memory search", "artifact review"],
        "report": ["capture distilled rules", "capture breadcrumbs"],
    },
    "REPORTING": {
        "preload": ["progress.md", "spawn_report.md", "efficiency_report.md"],
        "tools": ["status snapshot", "spawn report", "efficiency report"],
        "report": ["package run summary", "record handoff quality"],
    },
}

PROFILE_LIBRARY: Dict[str, List[Dict[str, Any]]] = {
    "healthy": [
        {"stage": "BOOTSTRAP", "pass_rate": 1.0, "retries_per_test": 0.0, "open_handoffs": 0, "hallucination_confidence": 0.98},
        {"stage": "TEST_WAVE_GEN", "pass_rate": 1.0, "retries_per_test": 0.1, "open_handoffs": 0, "hallucination_confidence": 0.97},
        {"stage": "IMPLEMENT", "pass_rate": 1.0, "retries_per_test": 0.2, "open_handoffs": 0, "hallucination_confidence": 0.96},
        {"stage": "REPORTING", "pass_rate": 1.0, "retries_per_test": 0.0, "open_handoffs": 0, "hallucination_confidence": 0.99},
    ],
    "mixed": [
        {"stage": "BOOTSTRAP", "pass_rate": 1.0, "retries_per_test": 0.0, "open_handoffs": 0, "hallucination_confidence": 0.95},
        {"stage": "TEST_WAVE_GEN", "pass_rate": 0.8, "retries_per_test": 0.8, "open_handoffs": 1, "hallucination_confidence": 0.84},
        {"stage": "IMPLEMENT", "pass_rate": 0.7, "retries_per_test": 1.2, "open_handoffs": 1, "hallucination_confidence": 0.72},
        {"stage": "STABILIZATION", "pass_rate": 0.85, "retries_per_test": 0.4, "open_handoffs": 0, "hallucination_confidence": 0.89},
    ],
    "stress": [
        {"stage": "BOOTSTRAP", "pass_rate": 1.0, "retries_per_test": 0.0, "open_handoffs": 0, "hallucination_confidence": 0.92},
        {"stage": "TEST_WAVE_GEN", "pass_rate": 0.5, "retries_per_test": 1.6, "open_handoffs": 2, "hallucination_confidence": 0.48},
        {"stage": "HALLUCINATION_GUARD", "pass_rate": 0.3, "retries_per_test": 2.0, "open_handoffs": 3, "hallucination_confidence": 0.28},
    ],
}
PROFILE_LIBRARY["balanced"] = PROFILE_LIBRARY["mixed"]


def score_stage_state(
    pass_rate: float,
    retries_per_test: float,
    open_handoffs: int,
    hallucination_confidence: float,
    stage_completion_confidence: float = 1.0,
) -> float:
    reliability = max(0.0, min(1.0, pass_rate))
    stability = max(0.0, min(1.0, hallucination_confidence))
    retry_penalty = min(1.0, retries_per_test / 3.0)
    handoff_penalty = min(1.0, open_handoffs / 6.0)
    completion = max(0.0, min(1.0, stage_completion_confidence))
    return round(
        (
            reliability * 0.42
            + stability * 0.28
            + completion * 0.18
            + (1.0 - retry_penalty) * 0.08
            + (1.0 - handoff_penalty) * 0.04
        ),
        6,
    )


def stage_manifest_from_snapshot(
    snapshot: RunSnapshot,
    config: RunConfig,
    *,
    current_stage: Optional[str] = None,
    next_stage: Optional[str] = None,
    source: str = "live",
    profile: str = "live",
    note: str = "",
    score_override: Optional[float] = None,
    stage_completion_confidence: float = 1.0,
) -> StageManifest:
    stage_name = (current_stage or snapshot.wave_name or "BOOTSTRAP").upper()
    stage_index = _stage_index(stage_name)
    next_name = (next_stage or _next_stage(stage_name)).upper()
    preload = _protocol_items(stage_name, "preload")
    tools = _protocol_items(stage_name, "tools")
    checklist = _protocol_items(stage_name, "report")
    score = score_override if score_override is not None else score_stage_state(
        pass_rate=float(snapshot.passing_tests) / max(1, snapshot.total_tests or 1),
        retries_per_test=float(snapshot.no_gain_waves) / max(1, snapshot.wave_index + 1),
        open_handoffs=int(snapshot.open_handoff_count),
        hallucination_confidence=float(snapshot.hallucination_confidence),
        stage_completion_confidence=stage_completion_confidence,
    )
    return StageManifest(
        manifest_id=snapshot.run_id or f"rehearsal-{uuid.uuid4().hex[:8]}",
        created_at=datetime.utcnow().isoformat() + "Z",
        source=source,
        profile=profile,
        current_stage=stage_name,
        next_stage=next_name,
        stage_index=stage_index,
        preload_bundle=preload,
        required_tools=tools,
        report_checklist=checklist,
        guard_overrides=_stage_guard_overrides(config, stage_name),
        runtime_overrides=_stage_runtime_overrides(config, stage_name),
        score=score,
        pass_rate=float(snapshot.passing_tests) / max(1, snapshot.total_tests or 1),
        retries_per_test=float(snapshot.no_gain_waves) / max(1, snapshot.wave_index + 1),
        open_handoffs=int(snapshot.open_handoff_count),
        hallucination_confidence=float(snapshot.hallucination_confidence),
        stage_completion_confidence=stage_completion_confidence,
        note=note,
    )


class OfflineRehearsalManager:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.rehearsal_dir = os.path.join(root_dir, "swarm_runs", "rehearsal")
        os.makedirs(self.rehearsal_dir, exist_ok=True)

    def simulate(
        self,
        snapshot: RunSnapshot,
        config: RunConfig,
        profile: Optional[str] = None,
        live_manifest: Optional[StageManifest] = None,
    ) -> RehearsalOutcome:
        profile_name = (profile or config.rehearsal_profile or "balanced").strip().lower()
        if profile_name not in PROFILE_LIBRARY:
            profile_name = "mixed"

        rehearsal_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        run_dir = os.path.join(self.rehearsal_dir, rehearsal_id)
        os.makedirs(run_dir, exist_ok=True)

        start = time.time()
        current_manifest = live_manifest or stage_manifest_from_snapshot(
            snapshot,
            config,
            source="live",
            profile="live",
            note="Baseline live stage manifest",
        )
        live_score = current_manifest.score
        stage_timeline: List[Dict[str, object]] = []
        failure_trace: List[str] = []

        profile_steps = PROFILE_LIBRARY[profile_name]
        stage_completion_confidence = 1.0
        observed_pass_rates: List[float] = []
        observed_retries: List[float] = []
        observed_handoffs: List[int] = []
        observed_confidence: List[float] = []

        for index, step in enumerate(profile_steps):
            stage = str(step["stage"]).upper()
            pass_rate = float(step["pass_rate"])
            retries_per_test = float(step["retries_per_test"])
            open_handoffs = int(step["open_handoffs"])
            confidence = float(step["hallucination_confidence"])
            observed_pass_rates.append(pass_rate)
            observed_retries.append(retries_per_test)
            observed_handoffs.append(open_handoffs)
            observed_confidence.append(confidence)

            decision, reason = self._decide_step(step, config)
            stage_timeline.append(
                {
                    "index": index,
                    "stage": stage,
                    "decision": decision,
                    "reason": reason,
                    "pass_rate": pass_rate,
                    "retries_per_test": retries_per_test,
                    "open_handoffs": open_handoffs,
                    "hallucination_confidence": confidence,
                }
            )
            if decision != "PASS":
                failure_trace.append(f"{stage}: {reason}")
                stage_completion_confidence = max(0.35, stage_completion_confidence - 0.2)
                if decision == "HALT":
                    break

        average_pass_rate = mean(observed_pass_rates) if observed_pass_rates else 0.0
        average_retries = mean(observed_retries) if observed_retries else 0.0
        max_open_handoffs = max(observed_handoffs) if observed_handoffs else 0
        average_confidence = mean(observed_confidence) if observed_confidence else 0.0
        rehearsal_score = score_stage_state(
            pass_rate=average_pass_rate,
            retries_per_test=average_retries,
            open_handoffs=max_open_handoffs,
            hallucination_confidence=average_confidence,
            stage_completion_confidence=stage_completion_confidence,
        )
        candidate_stage = self._choose_candidate_stage(snapshot, stage_timeline, profile_name)
        manifest = stage_manifest_from_snapshot(
            snapshot,
            config,
            current_stage=candidate_stage,
            next_stage=_next_stage(candidate_stage),
            source="rehearsal",
            profile=profile_name,
            note="Offline rehearsal promotion candidate",
            score_override=rehearsal_score,
            stage_completion_confidence=stage_completion_confidence,
        )
        manifest.manifest_id = f"{rehearsal_id}-{profile_name}"
        manifest.preload_bundle = self._preload_for_manifest(manifest.current_stage)
        manifest.required_tools = self._required_tools_for_manifest(manifest.current_stage, config)
        manifest.report_checklist = self._report_checklist_for_manifest(manifest.current_stage)
        manifest.guard_overrides = _stage_guard_overrides(config, manifest.current_stage)
        manifest.runtime_overrides = _stage_runtime_overrides(config, manifest.current_stage)
        accepted = rehearsal_score > live_score + float(config.stage_manifest_min_score_delta)

        outcome = RehearsalOutcome(
            rehearsal_id=rehearsal_id,
            profile=profile_name,
            accepted=accepted,
            live_score=live_score,
            rehearsal_score=rehearsal_score,
            manifest=manifest,
            stage_timeline=stage_timeline,
            failure_trace=failure_trace,
            duration_seconds=max(0.01, time.time() - start),
        )
        self._write_outcome(run_dir, outcome)
        return outcome

    def _write_outcome(self, run_dir: str, outcome: RehearsalOutcome) -> None:
        report_path = os.path.join(run_dir, "rehearsal_report.md")
        manifest_path = os.path.join(run_dir, "stage_manifest.json")
        manifest_md_path = os.path.join(run_dir, "stage_manifest.md")
        trace_path = os.path.join(run_dir, "failure_trace.md")
        json_path = os.path.join(run_dir, "rehearsal_report.json")

        payload = {
            "rehearsal_id": outcome.rehearsal_id,
            "profile": outcome.profile,
            "accepted": outcome.accepted,
            "live_score": outcome.live_score,
            "rehearsal_score": outcome.rehearsal_score,
            "duration_seconds": outcome.duration_seconds,
            "stage_timeline": outcome.stage_timeline,
            "failure_trace": outcome.failure_trace,
            "manifest": asdict(outcome.manifest),
        }
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(asdict(outcome.manifest), handle, indent=2)

        with open(manifest_md_path, "w", encoding="utf-8") as handle:
            handle.write(self._render_manifest_markdown(outcome))

        with open(trace_path, "w", encoding="utf-8") as handle:
            handle.write(self._render_trace_markdown(outcome))

        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write(self._render_report_markdown(outcome))

        outcome.report_path = report_path
        outcome.manifest_path = manifest_path
        outcome.manifest_markdown_path = manifest_md_path
        outcome.trace_path = trace_path

    def _render_report_markdown(self, outcome: RehearsalOutcome) -> str:
        lines = [
            f"# Rehearsal Report - {outcome.rehearsal_id}",
            "",
            f"Profile: {outcome.profile}",
            f"Accepted: {outcome.accepted}",
            f"Live Score: {outcome.live_score:.4f}",
            f"Rehearsal Score: {outcome.rehearsal_score:.4f}",
            f"Duration Seconds: {outcome.duration_seconds:.2f}",
            "",
            "## Stage Timeline",
        ]
        for row in outcome.stage_timeline:
            lines.append(
                f"- {row.get('stage')}: {row.get('decision')} ({row.get('reason')}) "
                f"pass_rate={row.get('pass_rate')} retries={row.get('retries_per_test')} "
                f"handoffs={row.get('open_handoffs')} confidence={row.get('hallucination_confidence')}"
            )
        lines.extend(
            [
                "",
                "## Failure Trace",
            ]
        )
        if outcome.failure_trace:
            lines.extend([f"- {item}" for item in outcome.failure_trace])
        else:
            lines.append("- none")
        lines.extend(
            [
                "",
                "## Manifest",
                f"- Current Stage: {outcome.manifest.current_stage}",
                f"- Next Stage: {outcome.manifest.next_stage}",
                f"- Score: {outcome.manifest.score:.4f}",
                f"- Preload Bundle: {', '.join(outcome.manifest.preload_bundle) or 'none'}",
                f"- Required Tools: {', '.join(outcome.manifest.required_tools) or 'none'}",
                f"- Report Checklist: {', '.join(outcome.manifest.report_checklist) or 'none'}",
            ]
        )
        return "\n".join(lines) + "\n"

    def _render_manifest_markdown(self, outcome: RehearsalOutcome) -> str:
        manifest = outcome.manifest
        return (
            f"# Stage Manifest\n\n"
            f"Manifest ID: {manifest.manifest_id}\n\n"
            f"Created At: {manifest.created_at}\n\n"
            f"Source: {manifest.source}\n\n"
            f"Profile: {manifest.profile}\n\n"
            f"Current Stage: {manifest.current_stage}\n\n"
            f"Next Stage: {manifest.next_stage}\n\n"
            f"Stage Index: {manifest.stage_index}\n\n"
            f"Score: {manifest.score:.4f}\n\n"
            f"Preload Bundle: {', '.join(manifest.preload_bundle) or 'none'}\n\n"
            f"Required Tools: {', '.join(manifest.required_tools) or 'none'}\n\n"
            f"Report Checklist: {', '.join(manifest.report_checklist) or 'none'}\n\n"
        )

    def _render_trace_markdown(self, outcome: RehearsalOutcome) -> str:
        lines = ["# Rehearsal Failure Trace", ""]
        if not outcome.failure_trace:
            lines.append("- none")
        else:
            lines.extend([f"- {item}" for item in outcome.failure_trace])
        return "\n".join(lines) + "\n"

    def _decide_step(self, step: Dict[str, Any], config: RunConfig) -> tuple[str, str]:
        if float(step["hallucination_confidence"]) < float(config.hallucination_block_threshold):
            return "HALT", "Hallucination confidence fell below block threshold"
        if int(step["open_handoffs"]) >= max(1, config.guard_max_open_handoffs):
            return "REDIRECT", "Open handoff debt exceeded guard threshold"
        if float(step["retries_per_test"]) >= float(config.guard_retry_pressure_threshold):
            return "DEFLECT", "Retry pressure exceeded guard threshold"
        if float(step["pass_rate"]) < 1.0 and step["stage"] in {"IMPLEMENT", "JUDGE"}:
            return "WARN", "Partial pass rate indicates a stabilization gap"
        return "PASS", "Stage remains healthy"

    def _choose_candidate_stage(
        self,
        snapshot: RunSnapshot,
        stage_timeline: List[Dict[str, object]],
        profile: str,
    ) -> str:
        if not stage_timeline:
            return (snapshot.wave_name or "BOOTSTRAP").upper()
        if profile == "healthy":
            return "REPORTING"
        if profile == "stress":
            return "STABILIZATION"
        last_stage = str(stage_timeline[-1].get("stage", snapshot.wave_name or "BOOTSTRAP")).upper()
        if any(row.get("decision") in {"HALT", "REDIRECT"} for row in stage_timeline):
            return "STABILIZATION"
        return last_stage

    def _preload_for_manifest(self, stage_name: str) -> List[str]:
        return list(STAGE_PROTOCOLS.get(stage_name, STAGE_PROTOCOLS["BOOTSTRAP"])["preload"])

    def _required_tools_for_manifest(self, stage_name: str, config: RunConfig) -> List[str]:
        protocol = STAGE_PROTOCOLS.get(stage_name, STAGE_PROTOCOLS["BOOTSTRAP"])
        tools = list(protocol["tools"])
        if config.rehearsal_enabled:
            tools.extend(["rehearsal report", "stage manifest"])
        return _unique_preserve_order(tools)

    def _report_checklist_for_manifest(self, stage_name: str) -> List[str]:
        return list(STAGE_PROTOCOLS.get(stage_name, STAGE_PROTOCOLS["BOOTSTRAP"])["report"])


def _stage_index(stage_name: str) -> int:
    try:
        return STAGE_SEQUENCE.index(stage_name)
    except ValueError:
        return 0


def _next_stage(stage_name: str) -> str:
    idx = _stage_index(stage_name)
    if idx + 1 < len(STAGE_SEQUENCE):
        return STAGE_SEQUENCE[idx + 1]
    return STAGE_SEQUENCE[-1]


def _protocol_items(stage_name: str, key: str) -> List[str]:
    protocol = STAGE_PROTOCOLS.get(stage_name, STAGE_PROTOCOLS["BOOTSTRAP"])
    return list(protocol.get(key, []))


def _stage_guard_overrides(config: RunConfig, stage_name: str) -> Dict[str, Any]:
    if stage_name in {"STABILIZATION", "MEMORY_COMPACTION"}:
        return {
            "guard_max_open_handoffs": min(config.guard_max_open_handoffs, 4),
            "guard_spawn_pause_waves": max(1, config.guard_spawn_pause_waves),
            "guard_no_gain_redirect": min(config.guard_no_gain_redirect, 4),
        }
    if stage_name in {"HALLUCINATION_GUARD", "JUDGE"}:
        return {
            "guard_retry_pressure_threshold": min(config.guard_retry_pressure_threshold, 1.2),
            "guard_max_open_handoffs": min(config.guard_max_open_handoffs, 5),
        }
    return {
        "guard_max_open_handoffs": config.guard_max_open_handoffs,
        "guard_spawn_pause_waves": config.guard_spawn_pause_waves,
        "guard_no_gain_redirect": config.guard_no_gain_redirect,
        "guard_retry_pressure_threshold": config.guard_retry_pressure_threshold,
    }


def _stage_runtime_overrides(config: RunConfig, stage_name: str) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}
    if stage_name in {"MEMORY_COMPACTION", "REPORTING"}:
        overrides["memory_distillation_enabled"] = True
        overrides["compaction_interval_waves"] = max(1, min(config.compaction_interval_waves, 2))
        overrides["memory_rule_limit"] = min(config.memory_rule_limit, 6)
        overrides["memory_breadcrumb_limit"] = min(config.memory_breadcrumb_limit, 5)
    elif stage_name in {"BOOTSTRAP", "SEED_LOADING"}:
        overrides["memory_distillation_enabled"] = True
        overrides["compaction_interval_waves"] = max(2, config.compaction_interval_waves)
    return overrides


def _unique_preserve_order(items):
    seen = set()
    output = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output
