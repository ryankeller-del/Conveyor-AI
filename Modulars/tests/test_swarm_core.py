import time
from pathlib import Path

from swarm_core.bots import JudgeBot, SimpleAgent
from swarm_core.compaction import DistillationLoop
from swarm_core.controller import SwarmController
from swarm_core.efficiency import EfficiencyAnalyzer
from swarm_core.rosetta import RosettaStone
from swarm_core.spawn import AgentDescriptor, AgentRegistry, SpawnManager
from swarm_core.stability_guard import StabilityGuard
from swarm_core.team_collab import BrainstormEngine, TeamComparator
from swarm_core.types import RunConfig, RunMetrics, TaskGoal, TestSpec


class _FakeClient:
    class _Chat:
        class _Completions:
            def create(self, **kwargs):
                class _Msg:
                    content = ""

                class _Choice:
                    message = _Msg()

                class _Resp:
                    choices = [_Choice()]

                return _Resp()

        completions = _Completions()

    chat = _Chat()


class _EmptyClient:
    class _Chat:
        class _Completions:
            def create(self, **kwargs):
                raise RuntimeError("primary unavailable")

        completions = _Completions()

    chat = _Chat()


class _FallbackClient:
    class _Chat:
        class _Completions:
            def create(self, **kwargs):
                class _Msg:
                    content = "fallback-response"

                class _Choice:
                    message = _Msg()

                class _Resp:
                    choices = [_Choice()]

                return _Resp()

        completions = _Completions()

    chat = _Chat()


def _agent(name):
    return SimpleAgent(name=name, system_prompt="", client=_FakeClient(), model="dummy")


def _wait_for_terminal(controller, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = controller.status()["state"]
        if state in {"COMPLETE", "FAILED", "STOPPED"}:
            return state
        time.sleep(0.1)
    return controller.status()["state"]


def test_judge_rejects_flaky_and_duplicates():
    judge = JudgeBot(_agent("judge"))
    specs = [
        TestSpec("a", "BASELINE", "def test_a():\n    assert True", "x.py"),
        TestSpec("b", "BASELINE", "def test_a():\n    assert True", "x.py"),
        TestSpec("c", "BASELINE", "def test_c():\n    import random\n    assert random.random() > 0", "x.py"),
    ]
    approved = judge.validate_tests(specs)
    assert len(approved) == 1


def test_spawn_manager_respects_cap_and_records():
    registry = AgentRegistry()
    registry.register(AgentDescriptor(name="TestRefinerBot", role="specialist"))
    manager = SpawnManager(registry)
    records = manager.evaluate_and_spawn(
        context={
            "active_agents": ["TestBot", "LocalCoder", "JudgeBot"],
            "repeated_failure_count": 3,
            "judge_confidence": 0.4,
            "diff_complexity": 100,
            "candidate_files": ["app_v3.py"],
        },
        max_concurrent_agents=4,
    )
    assert len(records) == 1
    assert records[0].agent_name == "TestRefinerBot"
    assert "benefit=" in records[0].reason
    assert records[0].handoff_id
    assert records[0].return_required is True


def test_efficiency_analyzer_recommendation():
    analyzer = EfficiencyAnalyzer()
    analyzer.update(
        "A+B+C",
        RunMetrics(
            cycle_index=0,
            duration_seconds=3.0,
            pass_rate=0.7,
            retries_per_test=1.0,
            token_or_call_usage=10,
            failure_recurrence=2,
            diff_churn=20,
        ),
    )
    analyzer.update(
        "A+B+C+D",
        RunMetrics(
            cycle_index=1,
            duration_seconds=1.0,
            pass_rate=1.0,
            retries_per_test=0.1,
            token_or_call_usage=8,
            failure_recurrence=0,
            diff_churn=8,
        ),
    )
    assert analyzer.recommend_topology("A+B+C") == "A+B+C+D"


def test_controller_status_has_wave_topology_and_recommendation(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )

    controller.judge_bot.run_tests_with_command = (
        lambda tests_path, cwd, command_template: (True, "ok")
    )

    goal = TaskGoal(prompt="build simple flask app", target_files=["app_v3.py"], language="general")
    cfg = RunConfig(max_waves=1, max_total_tests=4)
    controller.start(goal, cfg)
    state = _wait_for_terminal(controller)

    status = controller.status()
    assert state in {"COMPLETE", "STOPPED", "FAILED"}
    assert "wave_name" in status
    assert isinstance(status.get("active_topology"), list)
    assert "recommendation" in status
    assert "team_ideas_count" in status
    assert "failure_memory_hits" in status
    assert "hallucination_confidence" in status
    assert "hallucination_alert_count" in status
    assert "compaction_runs" in status
    assert "active_memory_format" in status
    assert "latest_memory_winner" in status
    assert "compaction_interval_active" in status
    assert "ui_suggestions" in status
    assert "ui_warnings" in status
    assert "unfinished_feature_count" in status
    assert "current_focus" in status
    assert "open_handoff_count" in status
    assert "latest_handoff_feedback" in status
    assert "ramp_level" in status
    assert "guard_mode" in status
    assert "guard_interventions" in status
    assert "latest_guard_action" in status
    assert "latest_guard_reason" in status
    assert "handoff_mismatch_count" in status
    assert "latest_handoff_brief" in status
    assert "rosetta_warning_count" in status
    assert "latest_rosetta_warning" in status


def test_team_comparison_and_brainstorm_detects_novel_signatures(tmp_path: Path):
    team_a = tmp_path / "team_a.py"
    team_b = tmp_path / "team_b.py"
    team_a.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    team_b.write_text("def beta():\n    return 2\n", encoding="utf-8")

    comparator = TeamComparator()
    comparison = comparator.compare([str(team_a)], [str(team_b)])
    assert "alpha" in comparison["novel_a"]
    assert "beta" in comparison["novel_b"]

    brainstorm = BrainstormEngine().brainstorm(comparison, top_n=2)
    assert len(brainstorm) >= 2


def test_simple_agent_uses_fallback_client_when_primary_unavailable():
    agent = SimpleAgent(
        name="coder",
        system_prompt="",
        client=_EmptyClient(),
        model="local-model",
        fallback_client=_FallbackClient(),
        fallback_client_models=["openrouter/free"],
    )
    content = agent.generate("write code")
    assert content == "fallback-response"


def test_spawn_manager_blocks_when_cooldown_violation():
    registry = AgentRegistry()
    registry.register(AgentDescriptor(name="TestRefinerBot", role="specialist"))
    manager = SpawnManager(registry)
    records = manager.evaluate_and_spawn(
        context={
            "active_agents": ["TestBot"],
            "repeated_failure_count": 3,
            "judge_confidence": 0.4,
            "diff_complexity": 120,
            "candidate_files": ["app_v3.py"],
            "cooldown_violation": True,
            "spawn_min_benefit_score": 0.2,
        },
        max_concurrent_agents=5,
    )
    assert records == []


def test_distillation_loop_outputs_formats_and_winner(tmp_path: Path):
    learning = tmp_path / "swarm_learning"
    learning.mkdir(parents=True, exist_ok=True)
    failure_library = learning / "failure_library.jsonl"
    failure_library.write_text(
        '{"prompt":"build handler","error_message":"NameError: x","fix_summary":"define x first"}\n',
        encoding="utf-8",
    )
    progress = tmp_path / "progress.md"
    progress.write_text("- [time] Spawned TestRefinerBot: repeated failure\n", encoding="utf-8")

    loop = DistillationLoop()
    result = loop.run(
        cycle_index=4,
        failure_library_path=str(failure_library),
        progress_path=str(progress),
        historic_format_scores={"BLUEPRINT": 0.2, "NARRATIVE": 0.1, "COMMAND": 0.3},
        rule_limit=4,
        breadcrumb_limit=3,
    )
    assert result.selected_format in {"BLUEPRINT", "NARRATIVE", "COMMAND"}
    assert result.golden_rules
    assert result.breadcrumbs
    assert set(result.format_payloads.keys()) == {"BLUEPRINT", "NARRATIVE", "COMMAND"}


def test_adaptive_compaction_interval_moves_with_stability(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    cfg = RunConfig(
        adaptive_compaction_enabled=True,
        compaction_interval_waves=4,
        min_compaction_interval_waves=1,
        max_compaction_interval_waves=8,
    )
    controller.active_compaction_interval = 4
    controller.snapshot.hallucination_confidence = 0.4
    controller._adapt_compaction_policy(
        cfg,
        RunMetrics(
            cycle_index=0,
            duration_seconds=3.0,
            pass_rate=0.5,
            retries_per_test=1.5,
            token_or_call_usage=10,
            failure_recurrence=2,
            diff_churn=20,
        ),
    )
    assert controller.active_compaction_interval == 3

    controller.snapshot.hallucination_confidence = 0.95
    controller._adapt_compaction_policy(
        cfg,
        RunMetrics(
            cycle_index=1,
            duration_seconds=1.0,
            pass_rate=1.0,
            retries_per_test=0.1,
            token_or_call_usage=6,
            failure_recurrence=0,
            diff_churn=5,
        ),
    )
    assert controller.active_compaction_interval == 4


def test_spinoff_tests_generated_for_team_ecosystems(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    goal = TaskGoal(prompt="stabilize both systems", target_files=["app_v3.py"], language="general")
    cfg = RunConfig(
        team_a_files=["app.py"],
        team_b_files=["app_v3.py"],
        spin_off_tests_to_ecosystems=True,
    )

    class _Record:
        agent_name = "TestRefinerBot"
        reason = "repeated failure"

    controller._generate_spin_off_tests(goal=goal, config=cfg, source_record=_Record())
    assert (tmp_path / "tests" / "test_spinoff_app.py").exists()
    assert (tmp_path / "tests" / "test_spinoff_app_v3.py").exists()


def test_failed_handoffs_are_returned_with_feedback(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    controller.open_handoffs = {
        "abc123": {
            "record": {"agent_name": "TestRefinerBot", "parent_agent": "SwarmController"},
            "status": "OPEN",
        }
    }
    controller._pass_back_failed_handoffs("AssertionError in test_x", "Update null checks")
    assert controller.open_handoffs["abc123"]["status"] == "RETURNED_WITH_FAILURE"
    assert controller.handoff_feedback_log
    before = len(controller.handoff_feedback_log)
    controller._pass_back_failed_handoffs("AssertionError in test_x", "Update null checks")
    assert len(controller.handoff_feedback_log) == before


def test_handoff_mismatch_learning_increments_counter(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    controller.open_handoffs = {
        "xyz987": {
            "record": {
                "agent_name": "PerfBot",
                "parent_agent": "SwarmController",
                "task_scope": "optimize rendering pipeline",
                "files_touched": ["render.py"],
            },
            "status": "OPEN",
            "brief": "Focus=render performance; Files=render.py",
        }
    }
    cfg = RunConfig(mismatch_overlap_threshold=0.9, mismatch_learning_enabled=True)
    controller._pass_back_failed_handoffs(
        failure_output="authentication token parsing failed",
        fix_list="update auth parser and token validator",
        config=cfg,
    )
    assert controller.handoff_mismatch_count >= 1


def test_quality_gate_marks_failed_when_no_passes(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    controller.judge_bot.run_tests_with_command = (
        lambda tests_path, cwd, command_template: (False, "AssertionError: always fail")
    )
    cfg = RunConfig(
        max_waves=4,
        max_no_gain_waves=2,
        min_passed_tests_for_success=1,
        require_zero_open_handoffs_for_success=True,
    )
    controller.start(TaskGoal(prompt="force fail", target_files=["app_v3.py"]), cfg)
    state = _wait_for_terminal(controller)
    assert state == "FAILED"


def test_stability_guard_deflects_on_handoff_debt():
    guard = StabilityGuard()
    snap = type("S", (), {
        "consecutive_failed_waves": 1,
        "open_handoff_count": 8,
        "no_gain_waves": 1,
        "wave_index": 4,
    })()
    metric = RunMetrics(
        cycle_index=4,
        duration_seconds=2.0,
        pass_rate=0.6,
        retries_per_test=1.5,
        token_or_call_usage=8,
        failure_recurrence=2,
        diff_churn=10,
    )
    cfg = RunConfig(guard_max_open_handoffs=6, stability_guard_enabled=True)
    decision = guard.evaluate(snapshot=snap, metric=metric, recent_metrics=[metric], config=cfg)
    assert decision.action == "DEFLECT"


def test_population_control_prunes_specialists_under_deflect(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    controller.snapshot.active_topology = [
        "TestBot",
        "LocalCoder",
        "JudgeBot",
        "TestRefinerBot",
        "PerfBot",
        "SecurityBot",
    ]
    controller.guard_mode = "DEFLECT"
    controller.snapshot.wave_index = 10
    controller.open_handoffs = {f"h{i}": {"status": "OPEN"} for i in range(7)}
    cfg = RunConfig(max_concurrent_agents=4, population_control_enabled=True)
    controller._apply_population_control(cfg)
    active = controller.snapshot.active_topology
    assert "TestBot" in active and "LocalCoder" in active and "JudgeBot" in active
    assert len(active) <= 4


def test_rosetta_flags_vague_or_impossible_requests():
    rosetta = RosettaStone()
    result = rosetta.mediate(
        text="optimize everything instantly and guarantee no failures forever",
        audience="specialist",
        max_chars=260,
    )
    assert result.warnings
    assert "Objective:" in result.text
