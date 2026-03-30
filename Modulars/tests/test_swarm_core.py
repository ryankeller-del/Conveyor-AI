import time
from pathlib import Path

from swarm_core.bots import JudgeBot, SimpleAgent
from swarm_core.controller import SwarmController
from swarm_core.efficiency import EfficiencyAnalyzer
from swarm_core.spawn import AgentDescriptor, AgentRegistry, SpawnManager
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
