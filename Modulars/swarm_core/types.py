from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class RunState(str, Enum):
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    COMPLETE = "COMPLETE"


class ControllerPhase(str, Enum):
    PREPARING = "PREPARING"
    TEST_WAVE_GEN = "TEST_WAVE_GEN"
    IMPLEMENT = "IMPLEMENT"
    HALLUCINATION_GUARD = "HALLUCINATION_GUARD"
    JUDGE = "JUDGE"
    WAVE_PROMOTION = "WAVE_PROMOTION"
    TOPOLOGY_REVIEW = "TOPOLOGY_REVIEW"


@dataclass
class TaskGoal:
    prompt: str
    target_files: List[str] = field(default_factory=lambda: ["app_v3.py"])
    language: str = "general"


@dataclass
class TestSpec:
    __test__ = False
    name: str
    wave: str
    content: str
    path: str
    deterministic: bool = True


@dataclass
class StageManifest:
    manifest_id: str
    created_at: str
    source: str
    profile: str
    current_stage: str
    next_stage: str
    stage_index: int
    preload_bundle: List[str] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)
    report_checklist: List[str] = field(default_factory=list)
    guard_overrides: Dict[str, object] = field(default_factory=dict)
    runtime_overrides: Dict[str, object] = field(default_factory=dict)
    score: float = 0.0
    pass_rate: float = 0.0
    retries_per_test: float = 0.0
    open_handoffs: int = 0
    hallucination_confidence: float = 1.0
    stage_completion_confidence: float = 1.0
    note: str = ""


@dataclass
class RehearsalOutcome:
    rehearsal_id: str
    profile: str
    accepted: bool
    live_score: float
    rehearsal_score: float
    manifest: StageManifest
    stage_timeline: List[Dict[str, object]] = field(default_factory=list)
    failure_trace: List[str] = field(default_factory=list)
    report_path: str = ""
    manifest_path: str = ""
    manifest_markdown_path: str = ""
    trace_path: str = ""
    duration_seconds: float = 0.0


@dataclass
class SpawnRecord:
    agent_name: str
    parent_agent: str
    reason: str
    task_scope: str
    files_touched: List[str]
    result_summary: str
    duration_seconds: float
    quality_delta: float
    calls_used: int
    handoff_id: str = ""
    return_required: bool = True
    status: str = "OPEN"


@dataclass
class RunConfig:
    adaptive_testing_enabled: bool = True
    max_waves: int = 3
    max_total_tests: int = 18
    min_new_coverage_gain: float = 0.05
    max_no_gain_waves: int = 2
    dynamic_spawning_enabled: bool = True
    max_concurrent_agents: int = 5
    spawn_policy: str = "balanced"
    spawn_planning_enabled: bool = True
    spawn_min_benefit_score: float = 0.25
    spawn_cooldown_cycles: int = 1
    topology_candidates: List[List[str]] = field(
        default_factory=lambda: [
            ["TestBot", "LocalCoder", "JudgeBot"],
            ["TestBot", "LocalCoder", "JudgeBot", "TestRefinerBot"],
            [
                "TestBot",
                "LocalCoder",
                "JudgeBot",
                "TestRefinerBot",
                "PerfBot",
            ],
        ]
    )
    topology_eval_window_cycles: int = 3
    local_retry_limit: int = 2
    output_filename: str = "app_v3.py"
    artifacts_dir: str = "swarm_runs"
    test_command: str = "pytest {tests_path} -q"
    team_mode_enabled: bool = True
    team_a_files: List[str] = field(default_factory=lambda: ["app.py"])
    team_b_files: List[str] = field(default_factory=lambda: ["app_v3.py"])
    brainstorm_top_n: int = 3
    failure_memory_enabled: bool = True
    failure_memory_limit: int = 3
    hallucination_guard_enabled: bool = True
    hallucination_alert_threshold: float = 0.6
    hallucination_block_threshold: float = 0.35
    doc_grounding_enabled: bool = False
    prompt_guard_enabled: bool = True
    prompt_guard_max_chars: int = 6000
    prompt_guard_complexity_threshold: float = 0.72
    prompt_guard_retry_on_error: bool = True
    memory_distillation_enabled: bool = True
    compaction_interval_waves: int = 5
    adaptive_compaction_enabled: bool = True
    min_compaction_interval_waves: int = 2
    max_compaction_interval_waves: int = 8
    memory_rule_limit: int = 6
    memory_breadcrumb_limit: int = 5
    local_memory_enabled: bool = True
    local_memory_reuse_enabled: bool = True
    local_memory_max_chars: int = 1800
    local_memory_invalidate_on_failure: bool = True
    local_api_throttle_enabled: bool = True
    local_api_max_inflight: int = 1
    local_api_min_interval_seconds: float = 0.35
    local_api_queue_limit: int = 2
    local_api_backoff_seconds: float = 0.2
    chat_history_limit: int = 25
    chat_user_priority_enabled: bool = True
    standard_tests_enabled: bool = True
    standard_test_min_returned_failures: int = 2
    standard_test_advisory_only: bool = True
    specialist_profile_limit: int = 6
    directive_mode_enabled: bool = True
    max_problem_scope_chars: int = 1200
    enforce_no_duplicate_code: bool = True
    testing_agents_exempt_from_directives: bool = True
    spin_off_tests_to_ecosystems: bool = True
    min_passed_tests_for_success: int = 1
    require_zero_open_handoffs_for_success: bool = True
    max_consecutive_failed_waves: int = 8
    ramp_enabled: bool = True
    ramp_step_waves: int = 3
    ramp_max_level: int = 2
    stability_guard_enabled: bool = True
    guard_max_open_handoffs: int = 6
    guard_halt_failed_waves: int = 8
    guard_spawn_pause_waves: int = 2
    guard_no_gain_redirect: int = 4
    guard_retry_pressure_threshold: float = 1.4
    population_control_enabled: bool = True
    specialist_prune_grace_waves: int = 3
    need_to_know_enabled: bool = True
    handoff_brief_max_chars: int = 320
    handoff_feedback_max_chars: int = 260
    mismatch_learning_enabled: bool = True
    mismatch_overlap_threshold: float = 0.08
    rosetta_enabled: bool = True
    rosetta_max_chars: int = 320
    skill_evolution_enabled: bool = True
    skill_min_evidence_count: int = 3
    skill_negative_delta_threshold: float = -0.08
    skill_retool_cooldown_waves: int = 2
    preflight_enabled: bool = True
    preflight_required_before_run: bool = False
    preflight_max_proposals: int = 3
    preflight_approval_required: bool = True
    preflight_validation_required: bool = True
    stage_manifest_hot_swap_enabled: bool = True
    stage_manifest_min_score_delta: float = 0.05
    rehearsal_enabled: bool = True
    rehearsal_profile: str = "balanced"


@dataclass
class RunMetrics:
    cycle_index: int
    duration_seconds: float
    pass_rate: float
    retries_per_test: float
    token_or_call_usage: int
    failure_recurrence: int
    diff_churn: int


@dataclass
class RunSnapshot:
    run_id: str
    state: RunState
    phase: ControllerPhase
    wave_index: int
    wave_name: str
    total_tests: int
    passing_tests: int
    no_gain_waves: int
    active_topology: List[str]
    efficiency_scores: Dict[str, float] = field(default_factory=dict)
    last_error: str = ""
    spawn_count: int = 0
    artifacts_path: str = ""
    recommendation: Optional[str] = None
    team_ideas_count: int = 0
    latest_brainstorm_summary: str = ""
    failure_memory_hits: int = 0
    hallucination_confidence: float = 1.0
    hallucination_alert_count: int = 0
    latest_hallucination_alert: str = ""
    prompt_refactor_count: int = 0
    latest_prompt_guard_note: str = ""
    compaction_runs: int = 0
    active_memory_format: str = "NARRATIVE"
    latest_memory_winner: str = ""
    latest_breadcrumb: str = ""
    compaction_interval_active: int = 5
    local_memory_packet_count: int = 0
    local_memory_reuse_count: int = 0
    local_memory_invalidations: int = 0
    local_api_inflight: int = 0
    local_api_throttle_hits: int = 0
    local_api_user_waiting: int = 0
    local_api_swarm_waiting: int = 0
    local_api_last_lane: str = "swarm"
    latest_local_memory_note: str = ""
    latest_local_memory_agent: str = ""
    latest_local_memory_task_family: str = ""
    returned_failure_streak: int = 0
    standard_test_fallback_count: int = 0
    latest_standard_test_reason: str = ""
    latest_standard_test_pack: str = ""
    specialist_profiles: List[Dict[str, object]] = field(default_factory=list)
    chat_mode: str = "chat"
    chat_turn_count: int = 0
    queued_architect_instruction_count: int = 0
    latest_architect_instruction: str = ""
    ui_suggestions: List[str] = field(default_factory=list)
    ui_warnings: List[str] = field(default_factory=list)
    directives_active: bool = True
    unfinished_feature_count: int = 0
    current_focus: str = ""
    open_handoff_count: int = 0
    latest_handoff_feedback: str = ""
    consecutive_failed_waves: int = 0
    ramp_level: int = 0
    guard_mode: str = "NORMAL"
    guard_interventions: int = 0
    latest_guard_action: str = ""
    latest_guard_reason: str = ""
    handoff_mismatch_count: int = 0
    latest_handoff_brief: str = ""
    rosetta_warning_count: int = 0
    latest_rosetta_warning: str = ""
    active_skill_count: int = 0
    skill_retool_count: int = 0
    latest_skill_event: str = ""
    prep_bundle_id: str = ""
    prep_status: str = "NONE"
    prep_pending_count: int = 0
    prep_approved_count: int = 0
    prep_denied_count: int = 0
    prep_revise_count: int = 0
    prep_last_validation: str = ""
    stage_manifest_id: str = ""
    stage_manifest_source: str = ""
    stage_manifest_profile: str = ""
    stage_manifest_current: str = ""
    stage_manifest_next: str = ""
    stage_manifest_score: float = 0.0
    stage_manifest_note: str = ""
    rehearsal_id: str = ""
    rehearsal_state: str = "IDLE"
    rehearsal_profile: str = ""
    rehearsal_report_path: str = ""
    rehearsal_manifest_path: str = ""
    rehearsal_trace_path: str = ""
