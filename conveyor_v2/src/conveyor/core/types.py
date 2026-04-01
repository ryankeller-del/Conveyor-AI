"""Core type definitions for Conveyor v2.

Pure data — no external dependencies, no business logic.
Maps directly to legacy swarm_core/types.py and app.py data structures.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SwarmState(Enum):
    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPED = auto()


class Phase(Enum):
    PREFLIGHT = auto()
    EXECUTION = auto()
    REVIEW = auto()


class GuardMode(Enum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    STRICT = "STRICT"


class MemoryProfile(Enum):
    DEFAULT = "default"
    FAST = "fast"
    DEEP = "deep"
    OFF = "off"


class RehearsalState(Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"


class PreflightStatus(Enum):
    NONE = "NONE"
    PENDING = "PENDING"
    READY = "READY"
    LAUNCHED = "LAUNCHED"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class RunConfig:
    """Run-time configuration with sensible defaults matching legacy.

    Legacy source: SwarmController status() keys + cl.user_session overrides.
    """
    test_command: str = "python -m pytest {tests_path} -q"
    chat_history_limit: int = 8
    memory_distillation_enabled: bool = True
    compaction_interval_waves: int = 3
    memory_rule_limit: int = 6
    memory_breadcrumb_limit: int = 5
    adaptive_compaction_enabled: bool = True

    def apply_overrides(self, overrides: dict[str, Any]) -> "RunConfig":
        """Return a new RunConfig with selected overrides applied.

        Only fields that exist on RunConfig are accepted. Unknown keys
        are silently ignored — matching legacy behaviour where
        cl.user_session overrides were merged by dataclass field name.
        """
        valid = {
            k: v for k, v in overrides.items()
            if k in self.__dataclass_fields__
        }
        return dataclasses.replace(self, **valid) if valid else self


@dataclass
class TaskGoal:
    """A task to be executed by the swarm.

    Legacy source: _parse_goal() in app.py.
    """
    prompt: str
    target_files: list[str] = field(default_factory=list)
    language: str = "general"


# ---------------------------------------------------------------------------
# Agent identity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentRole(Enum):
    """Canonical role identifiers for the 13 specialist agents.

    Legacy source: _build_controller() in app.py — 13 SimpleAgent instances.
    """
    TEST = "test"
    CODER = "coder"
    CHAT = "chat"
    JUDGE = "judge"
    CONTEXT_GUARD = "context_guard"
    PATTERN_FINDER = "pattern_finder"
    COMPRESSION = "compression"
    NOVELTY = "novelty"
    STABILITY_GUARD = "stability_guard"
    SEED_PREP = "seed_prep"
    DIRECTIVE_PREP = "directive_prep"
    STABILITY_PREP = "stability_prep"


@dataclass
class BotProfile:
    """Configuration for a single specialist agent.

    Legacy source: bot_profiles_v3.py build_swarm_profiles().
    """
    name: str
    model: str
    fallback_models: list[str] = field(default_factory=list)
    system_prompt: str = ""
    fallback_client_models: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Status aggregation
# ---------------------------------------------------------------------------

@dataclass
class SwarmStatus:
    """Aggregated status dict matching the 100+ keys from legacy status().

    Each group (chat, memory, guards, etc.) is a sub-dict for clarity.
    The flatten() method produces the exact legacy dict format for
    compatibility during the parallel run phase.
    """

    # Chat state
    chat_mode: str = "chat"
    chat_turn_count: int = 0
    queued_architect_instruction_count: int = 0
    latest_architect_instruction: str = ""
    background_run_queue_depth: int = 0
    background_run_active_goal: str = ""
    background_run_last_run_id: str = ""
    background_run_last_status: str = ""
    filesystem_queue_depth: int = 0
    filesystem_active_target: str = ""
    filesystem_last_path: str = ""
    filesystem_last_status: str = ""
    filesystem_last_result: str = ""

    # Swarm health
    state: str = "idle"
    phase: str = "preflight"
    wave_name: str = ""
    wave_index: int = 0
    active_topology: list[str] = field(default_factory=list)
    spawn_count: int = 0
    open_handoff_count: int = 0
    failure_memory_hits: int = 0
    hallucination_confidence: float = 1.0
    hallucination_alert_count: int = 0
    latest_hallucination_alert: str = ""
    team_ideas_count: int = 0
    latest_brainstorm_summary: str = ""
    recommendation: str = ""
    handoff_mismatch_count: int = 0
    latest_handoff_brief: str = ""
    rosetta_warning_count: int = 0
    latest_rosetta_warning: str = ""
    returned_failure_streak: int = 0
    directives_active: bool = True
    unfinished_feature_count: int = 0
    current_focus: str = ""

    # Memory
    local_memory_packet_count: int = 0
    local_memory_reuse_count: int = 0
    local_memory_invalidations: int = 0
    local_memory_pressure: float = 0.0
    local_memory_compaction_triggered: bool = False
    latest_local_memory_pressure: float = 0.0
    latest_local_memory_compaction_reason: str = ""
    latest_local_memory_note: str = ""
    latest_local_memory_agent: str = ""
    latest_local_memory_task_family: str = ""

    # Generation memory
    generation_memory_records: int = 0
    generation_memory_restores: int = 0
    generation_memory_latest_generation_id: str = ""
    generation_memory_latest_aspiration: str = ""
    generation_memory_latest_note: str = ""
    generation_memory_path: str = ""

    # Model routing
    local_model_host: str = ""
    local_model_routes: dict[str, dict[str, Any]] = field(default_factory=dict)
    latest_local_model_name: str = ""
    latest_local_model_lane: str = ""
    local_api_inflight: int = 0
    local_api_throttle_hits: int = 0
    local_api_user_waiting: int = 0
    local_api_swarm_waiting: int = 0
    local_api_last_lane: str = ""

    # Stage / Rehearsal
    stage_manifest_current: str = ""
    stage_manifest_next: str = ""
    stage_manifest_score: float = 0.0
    stage_manifest_profile: str = ""
    stage_manifest_preload_bundle: list[str] = field(default_factory=list)
    stage_manifest_required_tools: list[str] = field(default_factory=list)
    stage_manifest_report_checklist: list[str] = field(default_factory=list)
    rehearsal_state: str = "IDLE"
    rehearsal_profile: str = ""
    rehearsal_report_path: str = ""
    rehearsal_manifest_path: str = ""
    rehearsal_trace_path: str = ""

    # Preflight
    prep_bundle_id: str = ""
    prep_goal: str = ""
    prep_status: str = "NONE"
    prep_ready_to_launch: bool = False
    prep_requested_tools: list[str] = field(default_factory=list)
    prep_required_testing_tools: list[str] = field(default_factory=list)
    prep_required_reporting_tools: list[str] = field(default_factory=list)
    prep_required_diagnostics_tools: list[str] = field(default_factory=list)
    prep_requested_updates: list[str] = field(default_factory=list)
    prep_proposals: list[dict[str, Any]] = field(default_factory=list)

    # Guards
    guard_mode: str = "NORMAL"
    guard_interventions: int = 0
    latest_guard_action: str = ""
    latest_guard_reason: str = ""
    ramp_level: int = 0

    # Skills
    active_skill_count: int = 0
    skill_retool_count: int = 0
    latest_skill_event: str = ""

    # Tests
    test_command: str = "python -m pytest {tests_path} -q"
    artifacts_path: str = ""
    standard_test_fallback_count: int = 0
    latest_standard_test_reason: str = ""
    latest_standard_test_pack: str = ""

    # Display
    ui_suggestions: list[str] = field(default_factory=list)
    ui_warnings: list[str] = field(default_factory=list)
    specialist_profiles: list[dict[str, Any]] = field(default_factory=list)

    def flatten(self) -> dict[str, Any]:
        """Return a flat dict matching legacy SwarmController.status().

        Key names are exactly the legacy key names — no prefixing.
        This ensures the parallel run phase can diff outputs directly.
        """
        return {
            k: v for k, v in self.__dict__.items()
        }


# ---------------------------------------------------------------------------
# Rehearsal result
# ---------------------------------------------------------------------------

@dataclass
class RehearsalResults:
    """Result from a rehearsal run.

    Legacy source: controller.run_rehearsal() return dict.
    """
    rehearsal_id: str
    profile: str
    accepted: bool
    live_score: float
    rehearsal_score: float
    stage_manifest: dict[str, Any] = field(default_factory=dict)
    report_path: str = ""
    trace_path: str = ""
    manifest_path: str = ""
