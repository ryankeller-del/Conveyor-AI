from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class RunState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    COMPLETE = "COMPLETE"


class ControllerPhase(str, Enum):
    TEST_WAVE_GEN = "TEST_WAVE_GEN"
    IMPLEMENT = "IMPLEMENT"
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
    name: str
    wave: str
    content: str
    path: str
    deterministic: bool = True


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
