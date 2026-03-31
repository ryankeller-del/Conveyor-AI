import time
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot_profiles_v3 import build_swarm_profiles
from swarm_core.bots import JudgeBot, SimpleAgent
from swarm_core.compaction import DistillationLoop
from swarm_core.controller import SwarmController
from swarm_core.efficiency import EfficiencyAnalyzer
from swarm_core.failure_memory import FailureMemory
from swarm_core.hallucination_guard import HallucinationGuard
from swarm_core.local_runtime import AgentMemoryManager, GenerationMemoryArchive, LocalCallGovernor, MemoryPacket
from swarm_core.rehearsal import OfflineRehearsalManager, stage_manifest_from_snapshot
from swarm_core.preflight import SwarmPreflightManager
from swarm_core.standard_tests import StandardTestLibrary
from swarm_core.rosetta import RosettaStone
from swarm_core.skill_evolution import SkillEvolutionManager
from swarm_core.spawn import AgentDescriptor, AgentRegistry, SpawnManager
from swarm_core.stability_guard import StabilityGuard
from swarm_core.team_collab import BrainstormEngine, TeamComparator
from swarm_core.types import RunConfig, RunMetrics, RunState, TaskGoal, TestSpec


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


class _SelectiveClient:
    def __init__(self):
        self.calls = []

    class _Chat:
        def __init__(self, outer):
            self._outer = outer

        class _Completions:
            def __init__(self, outer):
                self._outer = outer

            def create(self, **kwargs):
                model = kwargs.get("model", "")
                self._outer.calls.append(model)

                class _Msg:
                    content = "" if model == "primary-model" else "fallback-hit"

                class _Choice:
                    message = _Msg()

                class _Resp:
                    choices = [_Choice()]

                return _Resp()

        @property
        def completions(self):
            return self._Completions(self._outer)

    @property
    def chat(self):
        return self._Chat(self)


class _MessageClient:
    class _Chat:
        class _Completions:
            def create(self, **kwargs):
                class _Msg:
                    content = "agent narrative response"

                class _Choice:
                    message = _Msg()

                class _Resp:
                    choices = [_Choice()]

                return _Resp()

        completions = _Completions()

    chat = _Chat()


class _RecordingClient:
    def __init__(self, content="agent narrative response"):
        self.content = content
        self.last_messages = []
        self.last_model = ""

    class _Chat:
        def __init__(self, outer):
            self._outer = outer

        class _Completions:
            def __init__(self, outer):
                self._outer = outer

            def create(self, **kwargs):
                self._outer.last_model = kwargs.get("model", "")
                self._outer.last_messages = kwargs.get("messages", [])
                content = self._outer.content

                class _Msg:
                    pass

                msg = _Msg()
                msg.content = content

                class _Choice:
                    message = msg

                class _Resp:
                    choices = [_Choice()]

                return _Resp()

        @property
        def completions(self):
            return self._Completions(self._outer)

    @property
    def chat(self):
        return self._Chat(self)


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
    assert "active_skill_count" in status
    assert "skill_retool_count" in status
    assert "latest_skill_event" in status
    assert "local_memory_packet_count" in status
    assert "local_memory_reuse_count" in status
    assert "local_memory_invalidations" in status
    assert "local_api_inflight" in status
    assert "local_api_throttle_hits" in status
    assert "latest_local_memory_note" in status
    assert "background_run_queue_depth" in status
    assert "background_run_active_goal" in status
    assert "background_run_last_run_id" in status
    assert "background_run_last_status" in status
    assert "local_model_host" in status
    assert "local_model_routes" in status
    assert "latest_local_model_name" in status
    assert "latest_local_model_lane" in status
    assert "local_memory_pressure" in status
    assert "local_memory_compaction_triggered" in status


def test_swarm_profiles_prioritize_desktop_local_routes():
    profiles = build_swarm_profiles()
    assert profiles["chat"].model == "glm-4.7-flash:q4_K_M"
    assert profiles["coder"].model == "qwen2.5-coder:14b"
    assert "deepseek-r1:32b" in profiles["judge"].fallback_models
    assert all(
        "ocr" not in model.lower() and "pose" not in model.lower()
        for profile in profiles.values()
        for model in [profile.model, *profile.fallback_models]
    )


def test_background_run_queue_serializes_launches(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    launches = []

    def _fake_start(goal, config=None):
        launches.append(goal.prompt)
        controller.snapshot.state = RunState.COMPLETE
        return f"run-{len(launches)}"

    controller.start = _fake_start  # type: ignore[method-assign]

    controller.queue_background_run(
        TaskGoal(prompt="first background goal", target_files=["app_v3.py"], language="general"),
        RunConfig(),
        source="chat",
    )
    controller.queue_background_run(
        TaskGoal(prompt="second background goal", target_files=["app_v3.py"], language="general"),
        RunConfig(),
        source="chat",
    )

    deadline = time.time() + 2.0
    while time.time() < deadline and len(launches) < 2:
        time.sleep(0.05)

    assert launches == ["first background goal", "second background goal"]
    status = controller.status()
    assert status["background_run_queue_depth"] == 0
    assert status["background_run_last_status"].startswith("completed")


def test_filesystem_queue_creates_folder_and_file(tmp_path: Path):
    modulars_root = tmp_path / "Modulars"
    modulars_root.mkdir()
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(modulars_root),
    )

    queue_id = controller.queue_filesystem_creation(
        folder_name="buttnutt",
        files=[{"name": "hello.js", "content": 'const helloWorld = "Hello, world!";\n'}],
        scope="repo_root",
        source="chat",
        note="javascript hello world request",
    )
    assert queue_id

    target_file = tmp_path / "buttnutt" / "hello.js"
    deadline = time.time() + 2.0
    while time.time() < deadline and not target_file.exists():
        time.sleep(0.05)

    assert target_file.exists()
    assert "Hello, world!" in target_file.read_text(encoding="utf-8")
    status = controller.status()
    assert status["filesystem_queue_depth"] == 0
    assert status["filesystem_last_status"].startswith("created")
    assert "hello.js" in status["filesystem_last_result"]
    assert "background_run_queue_depth" in status
    assert "background_run_active_goal" in status
    assert "background_run_last_run_id" in status
    assert "background_run_last_status" in status


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


def test_simple_agent_uses_fallback_models_in_order():
    client = _SelectiveClient()
    agent = SimpleAgent(
        name="coder",
        system_prompt="",
        client=client,
        model="primary-model",
        fallback_models=["fallback-model", "backup-model"],
    )
    content = agent.generate("write code")
    assert content == "fallback-hit"
    assert client.calls[:2] == ["primary-model", "fallback-model"]


def test_agent_memory_manager_reuses_and_refreshes_packets(tmp_path: Path):
    manager = AgentMemoryManager(str(tmp_path / "agent_memory"))
    first = manager.prepare(
        agent_name="LocalCoder",
        task_family="implementation",
        task_prompt="Build a small API.",
        failure_context="",
        support_notes=["Prefer small edits."],
    )
    assert first.content
    second = manager.prepare(
        agent_name="LocalCoder",
        task_family="implementation",
        task_prompt="Build a small API.",
    )
    assert second.reused is True
    manager.inject_solution(
        agent_name="LocalCoder",
        task_family="implementation",
        note="Add null checks before returning.",
        source_agent="JudgeBot",
        reason="failure analysis",
    )
    third = manager.prepare(
        agent_name="LocalCoder",
        task_family="implementation",
        task_prompt="Build a small API.",
    )
    assert third.reused is False
    assert "JudgeBot" in third.content
    assert manager.status()["reuse_count"] >= 1
    assert manager.status()["invalidations"] >= 1


def test_agent_memory_manager_exposes_specialist_profiles(tmp_path: Path):
    manager = AgentMemoryManager(str(tmp_path / "agent_memory"))
    packet = manager.prepare(
        agent_name="LocalCoder",
        task_family="implementation",
        task_prompt="Build a small API.",
        support_notes=["Prefer small edits."],
    )
    manager.record_call(
        agent_name="LocalCoder",
        task_family="implementation",
        packet_id=packet.packet_id,
        success=True,
        outcome="success",
        reused=False,
        note="implementation",
    )
    profiles = manager.specialist_profiles()
    assert profiles
    profile = profiles[0]
    assert profile["agent_name"] == "LocalCoder"
    assert profile["task_family"] == "implementation"
    assert "current_expert_trend" in profile
    assert profile["current_expert_trend"] in {"forming", "stable", "strengthening", "emerging"}


def test_generation_memory_archive_captures_and_restores(tmp_path: Path):
    archive = GenerationMemoryArchive(str(tmp_path / "generation_memory"))
    archive.begin_generation("gen-1", "Keep the swarm focused on testable changes.", "gen-0")
    packet = MemoryPacket(
        packet_id="pkt-1",
        agent_name="LocalCoder",
        task_family="implementation",
        generation_id="gen-1",
        content="Keep edits small and preserve tests.",
        signature="sig-1",
        created_at="2026-03-30T00:00:00Z",
        aspiration_prompt="Keep the swarm focused on testable changes.",
        source_notes=["Prefer small edits."],
        last_outcome="success",
    )
    captured = archive.capture_packet(
        packet=packet,
        reason="culled after replacement",
        aspiration_prompt="Keep the swarm focused on testable changes.",
    )
    assert captured is not None
    assert list((tmp_path / "generation_memory" / "raw").glob("*.json"))

    restored = archive.restore(
        agent_name="LocalCoder",
        task_family="implementation",
        task_prompt="continue the parser work",
        failure_context="previous attempt failed validation",
        max_records=2,
    )
    assert restored.restored is True
    assert "Keep the swarm focused" in restored.content
    assert "GENERATION MEMORY RESTORE" in restored.content

    status = archive.status()
    assert status["record_count"] >= 2
    assert status["restore_count"] >= 1
    assert status["latest_aspiration"]


def test_controller_uses_generation_memory_on_demand(tmp_path: Path):
    client = _RecordingClient()
    agent = SimpleAgent(
        name="coder",
        system_prompt="",
        client=client,
        model="primary-model",
        is_local=True,
    )
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=agent,
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    controller.generation_memory.begin_generation("gen-9", "Preserve the intent to keep edits small.")
    controller.generation_memory.capture_packet(
        MemoryPacket(
            packet_id="pkt-9",
            agent_name="coder",
            task_family="implementation",
            generation_id="gen-9",
            content="Keep edits narrow and validate every change.",
            signature="sig-9",
            created_at="2026-03-30T00:00:00Z",
            aspiration_prompt="Preserve the intent to keep edits small.",
            source_notes=["Prefer narrow edits."],
            last_outcome="success",
        ),
        reason="culled after a generation change",
        aspiration_prompt="Preserve the intent to keep edits small.",
    )

    response = controller._safe_generate(
        agent=agent,
        prompt="Implement the feature carefully.",
        purpose="implementation",
        config=RunConfig(
            max_waves=1,
            max_total_tests=2,
            prompt_guard_enabled=False,
            generation_memory_enabled=True,
            generation_memory_restore_enabled=True,
        ),
        task_family="implementation",
    )

    assert response == "agent narrative response"
    prompt_text = " ".join(
        str(item.get("content", "")) for item in client.last_messages if isinstance(item, dict)
    )
    assert "GENERATION MEMORY RESTORE" in prompt_text
    status = controller.status()
    assert status["generation_memory_records"] >= 2
    assert status["generation_memory_restores"] >= 1
    assert status["generation_memory_latest_aspiration"]


def test_agent_memory_manager_auto_compacts_on_pressure(tmp_path: Path):
    manager = AgentMemoryManager(str(tmp_path / "agent_memory"), max_packet_chars=240)
    first = manager.prepare(
        agent_name="LocalChatBot",
        task_family="chat",
        task_prompt="Explain a simple change for the user in a few short sentences.",
        support_notes=["Prefer concise responses."],
    )
    assert first.reused is False

    second = manager.prepare(
        agent_name="LocalChatBot",
        task_family="chat",
        task_prompt="Explain a simple change for the user in a few short sentences.",
        support_notes=["Prefer concise responses.", "Keep the response short."],
        pressure_threshold=0.2,
    )
    assert second.reused is False
    assert second.compacted is True
    assert second.pressure >= 0.2
    status = manager.status()
    assert status["compaction_triggered"] is True
    assert status["latest_pressure"] >= 0.2
    assert "auto-compacted" in status["latest_compaction_reason"]


def test_standard_test_library_resolves_role_and_failure_patterns():
    library = StandardTestLibrary()
    pack = library.resolve(
        role="test",
        code_type="flask",
        failure_pattern="unknown_api",
        target_file="app_v3.py",
    )
    rendered = pack.render()
    assert pack.role == "test"
    assert pack.code_type == "flask"
    assert "Standard fallback tests" in rendered
    assert "app_v3" in rendered


def test_local_governor_throttles_back_to_back_calls():
    governor = LocalCallGovernor(max_inflight=1, min_interval_seconds=0.01, queue_limit=1, backoff_seconds=0.01)
    lease = governor.acquire("LocalCoder", "implementation")
    governor.release(lease)
    second = governor.acquire("LocalCoder", "implementation")
    try:
        assert second.throttled is True
        assert governor.status()["throttle_hits"] >= 1
    finally:
        governor.release(second)


def test_local_governor_prioritizes_user_lane_over_swarm_queue():
    governor = LocalCallGovernor(max_inflight=1, min_interval_seconds=0.0, queue_limit=4, backoff_seconds=0.01)
    first = governor.acquire("SwarmCoder", "implementation", lane="swarm")
    order = []

    def _acquire(name, lane):
        lease = governor.acquire(name, "implementation", lane=lane)
        order.append(lane)
        try:
            pass
        finally:
            governor.release(lease)

    swarm_thread = threading.Thread(target=_acquire, args=("QueuedSwarm", "swarm"), daemon=True)
    user_thread = threading.Thread(target=_acquire, args=("QueuedUser", "user"), daemon=True)
    swarm_thread.start()
    time.sleep(0.05)
    user_thread.start()
    time.sleep(0.05)
    governor.release(first)

    deadline = time.time() + 2
    while time.time() < deadline and len(order) < 1:
        time.sleep(0.01)
    assert order
    assert order[0] == "user"
    swarm_thread.join(timeout=2)
    user_thread.join(timeout=2)


def test_hallucination_guard_uses_ast_for_python_literals(tmp_path: Path):
    target = tmp_path / "sample.py"
    target.write_text(
        "\n".join(
            [
                'def helper():',
                '    return True',
                '',
                'def run():',
                '    text = "fake_call() should not count"',
                '    # ignored_fake_call()',
                '    return helper()',
                '',
                'run()',
            ]
        ),
        encoding="utf-8",
    )
    guard = HallucinationGuard(str(tmp_path))
    result = guard.evaluate(
        target_file=str(target),
        code=target.read_text(encoding="utf-8"),
        prompt="Build the helper and keep the example simple.",
    )
    assert "fake_call" not in result.unknown_symbols
    assert result.confidence > 0.8


def test_distillation_loop_prefilters_boilerplate_context():
    loop = DistillationLoop()
    lines = [
        "2026-03-30 19:57:03,213 [INFO] Heartbeat tick",
        "2026-03-30 19:56:29,110 [INFO] D: drive - Total: 953.85 GB, Used: 89.52 GB, Free: 864.33 GB",
        "Updated file C:\\OpenClaw\\ConveyorAI\\Modulars\\app.py for the next wave.",
        "Memory distillation selected BLUEPRINT format.",
        "Memory distillation selected BLUEPRINT format.",
    ]
    filtered = loop._prefilter_context_lines(lines)
    assert any("Memory distillation selected BLUEPRINT format." in line for line in filtered)
    assert all("Heartbeat" not in line for line in filtered)
    assert all("drive - Total" not in line for line in filtered)
    assert all("C:\\" not in line for line in filtered)


def test_failure_memory_semantic_guidance_prefers_related_fix(tmp_path: Path):
    memory = FailureMemory(str(tmp_path / "failure_memory"))
    memory.log_failure(
        prompt="Make numeric addition safe across parsed fields.",
        code="value = left + right\n",
        error_message="TypeError: unsupported operand type(s) for +: 'int' and 'str'",
        wave_name="BASELINE",
        target_file="app.py",
        fix_summary="Convert numeric strings before addition and add guard rails.",
    )
    memory.log_failure(
        prompt="Render the settings table cleanly.",
        code="table.render()\n",
        error_message="KeyError: missing table key",
        wave_name="BASELINE",
        target_file="ui.py",
        fix_summary="Use a safe lookup and preserve defaults when the key is absent.",
    )

    guidance = memory.format_guidance(
        prompt="Need to harden string-to-number addition.",
        limit=1,
        failure_context="addition between parsed string values and numeric totals",
    )

    assert "Convert numeric strings before addition" in guidance
    assert "KeyError: missing table key" not in guidance


def test_parallel_validations_overlap_work(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    target = tmp_path / "app.py"
    target.write_text("def run():\n    return True\n", encoding="utf-8")
    specs = [TestSpec("baseline", "BASELINE", "def test_run():\n    assert True\n", str(target))]
    goal = TaskGoal(prompt="build api", target_files=["app.py"], language="general")

    def _slow_guard(*args, **kwargs):
        time.sleep(0.2)
        return type("G", (), {"confidence": 1.0, "alerts": [], "unknown_symbols": [], "unknown_apis": [], "missing_doc_grounding": False})()

    def _slow_judge(*args, **kwargs):
        time.sleep(0.2)
        return True, "ok"

    controller._evaluate_hallucination_guard = _slow_guard  # type: ignore[method-assign]
    controller._evaluate_judge = _slow_judge  # type: ignore[method-assign]
    cfg = RunConfig(hallucination_guard_enabled=True)

    start = time.time()
    guard_result, passed, output = controller._run_parallel_validations(
        goal=goal,
        generated_code="def run():\n    return True\n",
        test_specs=specs,
        config=cfg,
        cycle_index=0,
    )
    elapsed = time.time() - start

    assert elapsed < 0.35
    assert guard_result is not None
    assert passed is True
    assert output == "ok"


def test_chat_prompt_keeps_status_compact_for_plain_conversation(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    status = controller.status()
    chat_prompt = controller._build_chat_prompt(
        message_text="hello there",
        mode="chat",
        status=status,
        conversation_context="User: hello\nAssistant: hi",
    )
    health_prompt = controller._build_chat_prompt(
        message_text="/health",
        mode="health",
        status=status,
        conversation_context="",
    )
    assert "background_queue=" in chat_prompt
    assert "active_topology=" not in chat_prompt
    assert "latest_architect_instruction=" not in chat_prompt
    assert "active_topology=" in health_prompt
    assert "latest_architect_instruction=" in health_prompt


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


def test_standard_tests_are_offered_after_returned_failure_streak(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    controller.returned_failure_streak = 2
    captured = {}

    def _stub_generate_next_wave(**kwargs):
        captured["reference_material"] = kwargs.get("reference_material", "")
        path = kwargs["tests_path"]
        return [
            TestSpec(
                name="baseline",
                wave=kwargs["wave"],
                content="def test_generated():\n    assert True\n",
                path=path,
            )
        ]

    controller.test_bot.generate_next_wave = _stub_generate_next_wave  # type: ignore[method-assign]
    controller.judge_bot.validate_tests = lambda specs: specs
    cfg = RunConfig(
        standard_tests_enabled=True,
        standard_test_min_returned_failures=2,
    )
    generated = controller._generate_wave_tests(
        TaskGoal(prompt="build api", target_files=["app_v3.py"], language="general"),
        cfg,
        previous_tests=0,
    )
    assert captured["reference_material"]
    assert "Standard fallback tests" in generated[0].content
    assert controller.standard_test_fallback_count == 1


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


def test_skill_evolution_promotes_and_retools():
    manager = SkillEvolutionManager()
    for _ in range(3):
        manager.observe_pattern("handoff_mismatch")

    snap = type("S", (), {"wave_index": 3})()
    cfg = RunConfig(skill_min_evidence_count=3, skill_negative_delta_threshold=-0.05)
    metric = RunMetrics(
        cycle_index=3,
        duration_seconds=2.0,
        pass_rate=0.9,
        retries_per_test=0.4,
        token_or_call_usage=5,
        failure_recurrence=0,
        diff_churn=4,
    )
    event = manager.evaluate(snapshot=snap, metric=metric, config=cfg)
    assert event is not None and event.action == "PROMOTE"

    snap2 = type("S", (), {"wave_index": 6})()
    metric2 = RunMetrics(
        cycle_index=6,
        duration_seconds=2.4,
        pass_rate=0.7,
        retries_per_test=1.2,
        token_or_call_usage=9,
        failure_recurrence=2,
        diff_churn=12,
    )
    event2 = manager.evaluate(snapshot=snap2, metric=metric2, config=cfg)
    assert event2 is not None and event2.action == "RETOOL"


def test_preflight_bundle_builds_advisory_proposals(tmp_path: Path):
    manager = SwarmPreflightManager(
        root_dir=str(tmp_path),
        seed_agent=_agent("seed"),
        directive_agent=_agent("directive"),
        stability_agent=_agent("stability"),
    )
    bundle = manager.build_bundle(
        goal=TaskGoal(prompt="prepare safe launch", target_files=["app_v3.py"], language="general"),
        config=RunConfig(),
    )

    assert len(bundle.proposals) == 3
    assert bundle.status in {"PENDING", "APPROVED", "REVISE", "READY"}
    assert bundle.requested_tools
    assert bundle.requested_updates
    assert "pytest" in bundle.requested_tools
    assert "coverage" in bundle.requested_tools
    assert "dry-run harness" in bundle.requested_tools
    assert "spawn report" in bundle.requested_tools
    assert "efficiency report" in bundle.requested_tools
    assert "log inspection" in bundle.requested_tools
    assert "artifact browser" in bundle.requested_tools
    for proposal in bundle.proposals:
        assert proposal.suggested_action
        assert proposal.expected_benefit
        assert proposal.risk_if_wrong
        assert proposal.validation_plan
        assert proposal.status in {"APPROVED", "REVISE", "DENIED"}

    prep_dir = tmp_path / "swarm_runs" / "preflight" / bundle.bundle_id
    assert (prep_dir / "prep_bundle.json").exists()
    assert (prep_dir / "prep_bundle.md").exists()


def test_preflight_bundle_autoreviews_without_manual_approval(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    controller.judge_bot.run_tests_with_command = (
        lambda tests_path, cwd, command_template: (True, "ok")
    )
    goal = TaskGoal(prompt="prepare the swarm", target_files=["app_v3.py"], language="general")
    cfg = RunConfig(max_waves=1, max_total_tests=4)
    run_id = controller.start(goal, cfg)

    assert run_id
    state = _wait_for_terminal(controller)
    status = controller.status()
    assert state == "COMPLETE"
    assert status["prep_ready_to_launch"] is True
    assert status["prep_status"] == "READY"
    assert status["prep_requested_tools"]
    assert status["prep_requested_updates"]
    assert "pytest" in status["prep_requested_tools"]
    assert "coverage" in status["prep_requested_tools"]
    assert "dry-run harness" in status["prep_required_testing_tools"]
    assert "spawn report" in status["prep_required_reporting_tools"]
    assert "log inspection" in status["prep_required_diagnostics_tools"]


def test_offline_rehearsal_generates_manifest_and_report(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    controller.snapshot.run_id = "live-001"
    controller.snapshot.wave_name = "IMPLEMENT"
    controller.snapshot.total_tests = 5
    controller.snapshot.passing_tests = 4
    controller.snapshot.hallucination_confidence = 0.92
    live_manifest = stage_manifest_from_snapshot(
        controller.snapshot,
        RunConfig(),
        current_stage="IMPLEMENT",
        next_stage="JUDGE",
        source="live",
        profile="live",
    )
    controller.live_stage_manifest = live_manifest

    manager = OfflineRehearsalManager(str(tmp_path))
    outcome = manager.simulate(
        snapshot=controller.snapshot,
        config=RunConfig(stage_manifest_min_score_delta=0.01),
        profile="healthy",
        live_manifest=live_manifest,
    )

    assert outcome.report_path and Path(outcome.report_path).exists()
    assert outcome.manifest_path and Path(outcome.manifest_path).exists()
    assert outcome.trace_path and Path(outcome.trace_path).exists()
    assert outcome.manifest.current_stage
    assert outcome.manifest.next_stage
    assert outcome.stage_timeline
    assert outcome.rehearsal_score >= outcome.live_score


def test_live_controller_accepts_better_stage_manifest_without_restart(tmp_path: Path):
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=_agent("coder"),
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )
    controller.snapshot.run_id = "live-002"
    controller.snapshot.wave_name = "TEST_WAVE_GEN"
    controller.snapshot.total_tests = 4
    controller.snapshot.passing_tests = 2
    controller.snapshot.hallucination_confidence = 0.6
    controller.active_run_config = RunConfig(stage_manifest_min_score_delta=0.01)
    controller.live_stage_manifest = stage_manifest_from_snapshot(
        controller.snapshot,
        controller.active_run_config,
        current_stage="TEST_WAVE_GEN",
        next_stage="IMPLEMENT",
        source="live",
        profile="live",
        score_override=0.25,
    )

    better_manifest = stage_manifest_from_snapshot(
        controller.snapshot,
        controller.active_run_config,
        current_stage="STABILIZATION",
        next_stage="REPORTING",
        source="rehearsal",
        profile="mixed",
        score_override=0.75,
        note="Hot-swap candidate",
    )
    accepted = controller.apply_stage_manifest(better_manifest)
    assert accepted is True
    assert controller.live_stage_manifest.current_stage == "STABILIZATION"
    assert controller.snapshot.stage_manifest_current == "STABILIZATION"
    assert controller.snapshot.stage_manifest_next == "REPORTING"
    assert controller.snapshot.stage_manifest_score == 0.75

    worse_manifest = stage_manifest_from_snapshot(
        controller.snapshot,
        controller.active_run_config,
        current_stage="IMPLEMENT",
        next_stage="JUDGE",
        source="rehearsal",
        profile="stress",
        score_override=0.10,
        note="Worse candidate",
    )
    rejected = controller.apply_stage_manifest(worse_manifest)
    assert rejected is False
    assert controller.live_stage_manifest.current_stage == "STABILIZATION"


def test_agent_messages_are_recorded_in_swarm_feed(tmp_path: Path):
    agent = SimpleAgent(name="narrator", system_prompt="", client=_MessageClient(), model="primary")
    controller = SwarmController(
        test_agent=_agent("test"),
        coder_agent=agent,
        judge_agent=_agent("judge"),
        root_dir=str(tmp_path),
    )

    response = controller._safe_generate(
        agent=agent,
        prompt="Say something short.",
        purpose="chat",
        config=RunConfig(max_waves=1, max_total_tests=2),
        task_family="chat",
    )

    assert "agent narrative response" in response
    narrative = controller.recent_swarm_narrative(limit=10)
    assert any("narrator" in entry.get("headline", "").lower() for entry in narrative)
    assert any("agent narrative response" in entry.get("text", "").lower() for entry in narrative)
