from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, Optional

from .artifacts import ArtifactStore
from .bots import JudgeBot, SimpleAgent, TestBot
from .efficiency import EfficiencyAnalyzer
from .failure_memory import FailureMemory
from .spawn import AgentDescriptor, AgentRegistry, SpawnManager
from .team_collab import BrainstormEngine, TeamComparator
from .types import (
    ControllerPhase,
    RunConfig,
    RunMetrics,
    RunSnapshot,
    RunState,
    TaskGoal,
    TestSpec,
)


class SwarmController:
    def __init__(
        self,
        test_agent: SimpleAgent,
        coder_agent: SimpleAgent,
        judge_agent: SimpleAgent,
        root_dir: str,
    ):
        self.root_dir = root_dir
        self.test_bot = TestBot(test_agent)
        self.coder_agent = coder_agent
        self.judge_bot = JudgeBot(judge_agent)

        self.registry = AgentRegistry()
        for name in ["TestRefinerBot", "PerfBot", "SecurityBot", "RefactorBot"]:
            self.registry.register(AgentDescriptor(name=name, role="specialist"))
        self.spawn_manager = SpawnManager(self.registry)
        self.team_comparator = TeamComparator()
        self.brainstorm_engine = BrainstormEngine()

        self.efficiency = EfficiencyAnalyzer()
        self.failure_memory = FailureMemory(os.path.join(self.root_dir, "swarm_learning"))
        self._lock = threading.RLock()
        self._pause_event = threading.Event()
        self._pause_event.set()

        self.run_id: Optional[str] = None
        self.snapshot = RunSnapshot(
            run_id="",
            state=RunState.IDLE,
            phase=ControllerPhase.TEST_WAVE_GEN,
            wave_index=0,
            wave_name="BASELINE",
            total_tests=0,
            passing_tests=0,
            no_gain_waves=0,
            active_topology=["TestBot", "LocalCoder", "JudgeBot"],
        )
        self.last_status = ""
        self.artifacts: Optional[ArtifactStore] = None

    def _wave_name(self, idx: int) -> str:
        return ["BASELINE", "ROBUSTNESS", "MUTATION_REGRESSION"][min(idx, 2)]

    def start(self, goal: TaskGoal, config: Optional[RunConfig] = None) -> str:
        with self._lock:
            if self.snapshot.state == RunState.RUNNING:
                raise RuntimeError("Run already active")

            run_config = config or RunConfig()
            self.run_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
            self.artifacts = ArtifactStore(run_config.artifacts_dir, self.run_id)
            self.snapshot = RunSnapshot(
                run_id=self.run_id,
                state=RunState.RUNNING,
                phase=ControllerPhase.TEST_WAVE_GEN,
                wave_index=0,
                wave_name=self._wave_name(0),
                total_tests=0,
                passing_tests=0,
                no_gain_waves=0,
                active_topology=list(run_config.topology_candidates[0]),
                artifacts_path=self.artifacts.base_dir,
            )
            self._pause_event.set()
            self.artifacts.append_progress("Run started")
            self.artifacts.append_event("run_started", {"goal": asdict(goal), "config": asdict(run_config)})

        worker = threading.Thread(
            target=self._run_loop,
            args=(goal, run_config),
            name=f"swarm-{self.run_id}",
            daemon=True,
        )
        worker.start()
        return self.run_id

    def pause(self) -> None:
        with self._lock:
            if self.snapshot.state != RunState.RUNNING:
                return
            self.snapshot.state = RunState.PAUSED
            self._pause_event.clear()
            self.last_status = "Paused by user"
            self._save_state()

    def resume(self) -> None:
        with self._lock:
            if self.snapshot.state != RunState.PAUSED:
                return
            self.snapshot.state = RunState.RUNNING
            self._pause_event.set()
            self.last_status = "Resumed by user"
            self._save_state()

    def stop(self) -> None:
        with self._lock:
            if self.snapshot.state in {RunState.STOPPED, RunState.COMPLETE, RunState.FAILED}:
                return
            self.snapshot.state = RunState.STOPPING
            self._pause_event.set()
            self.last_status = "Stopping requested"
            self._save_state()

    def status(self) -> Dict:
        with self._lock:
            payload = asdict(self.snapshot)
            payload["state"] = self.snapshot.state.value
            payload["phase"] = self.snapshot.phase.value
            payload["last_status"] = self.last_status
            return payload

    def _save_state(self) -> None:
        if self.artifacts:
            self.artifacts.save_snapshot(self.snapshot)

    def _run_loop(self, goal: TaskGoal, config: RunConfig) -> None:
        previous_tests = 0
        efficiency_details: List[Dict] = []

        try:
            for wave_idx in range(config.max_waves):
                if not self._gate_run_state():
                    return

                with self._lock:
                    self.snapshot.phase = ControllerPhase.TEST_WAVE_GEN
                    self.snapshot.wave_index = wave_idx
                    self.snapshot.wave_name = self._wave_name(wave_idx)
                    self._save_state()

                test_specs = self._generate_wave_tests(goal, config, previous_tests)
                if not test_specs:
                    self._complete("No new meaningful tests generated.")
                    return

                for spec in test_specs:
                    self._write_file(spec.path, spec.content)
                    self.snapshot.total_tests += self._count_tests(spec.content)

                cycle_start = time.time()
                pass_count = 0
                attempts = 0
                failure_recurrence = 0

                for attempt in range(config.local_retry_limit + 1):
                    if not self._gate_run_state():
                        return

                    attempts += 1
                    with self._lock:
                        self.snapshot.phase = ControllerPhase.IMPLEMENT
                        self._save_state()

                    self._implement(goal, test_specs, attempt, config)

                    with self._lock:
                        self.snapshot.phase = ControllerPhase.JUDGE
                        self._save_state()

                    passed, judge_output = self._judge(goal, test_specs, config)
                    if passed:
                        pass_count = len(test_specs)
                        break

                    failure_recurrence += 1
                    if config.dynamic_spawning_enabled:
                        self._spawn_for_failure(goal, judge_output, config)

                    fix_list = self.judge_bot.get_fix_list(judge_output)
                    self.artifacts.append_progress(
                        f"Retry {attempt + 1}: judge suggested fixes captured"
                    )
                    if config.failure_memory_enabled:
                        self._record_failure(goal, judge_output, fix_list)
                    self._apply_fix_pass(goal, fix_list)

                wave_duration = max(0.01, time.time() - cycle_start)
                pass_rate = pass_count / max(1, len(test_specs))

                with self._lock:
                    self.snapshot.passing_tests += pass_count
                    self.snapshot.phase = ControllerPhase.WAVE_PROMOTION
                    self._save_state()

                coverage_gain = max(0.0, (self.snapshot.total_tests - previous_tests) / max(1, previous_tests + 1))
                if pass_rate < 1.0:
                    self.snapshot.no_gain_waves += 1
                    self.artifacts.append_progress(
                        f"Wave {self.snapshot.wave_name} failed to fully pass."
                    )
                else:
                    if coverage_gain < config.min_new_coverage_gain:
                        self.snapshot.no_gain_waves += 1
                    else:
                        self.snapshot.no_gain_waves = 0

                if self.snapshot.total_tests >= config.max_total_tests:
                    self._complete("Reached max_total_tests.")
                    return

                if self.snapshot.no_gain_waves >= config.max_no_gain_waves:
                    self._complete("Coverage gain plateau reached.")
                    return

                with self._lock:
                    self.snapshot.phase = ControllerPhase.TOPOLOGY_REVIEW
                    self._save_state()

                topology_key = "+".join(self.snapshot.active_topology)
                metric = RunMetrics(
                    cycle_index=wave_idx,
                    duration_seconds=wave_duration,
                    pass_rate=pass_rate,
                    retries_per_test=attempts / max(1, len(test_specs)),
                    token_or_call_usage=attempts + len(test_specs),
                    failure_recurrence=failure_recurrence,
                    diff_churn=self._estimate_diff_churn(goal.target_files[0]),
                )
                score = self.efficiency.update(topology_key, metric)
                efficiency_details.append(
                    {
                        "cycle": wave_idx,
                        "topology": topology_key,
                        "pass_rate": round(pass_rate, 3),
                        "duration_seconds": round(wave_duration, 3),
                        "score": round(score, 4),
                    }
                )
                self._rotate_topology(config, wave_idx)
                if config.team_mode_enabled:
                    self._run_team_brainstorm(config, wave_idx)

                previous_tests = self.snapshot.total_tests

            self._complete("Completed configured waves.")
        except Exception as exc:
            with self._lock:
                self.snapshot.state = RunState.FAILED
                self.snapshot.last_error = str(exc)
                self.last_status = f"Failed: {exc}"
                self._save_state()
            if self.artifacts:
                self.artifacts.append_event("run_failed", {"error": str(exc)})
        finally:
            fallback = "+".join(config.topology_candidates[0])
            recommendation = self.efficiency.recommend_topology(fallback)
            with self._lock:
                self.snapshot.recommendation = recommendation
                self.snapshot.efficiency_scores = self.efficiency.scores()
                self._save_state()
            if self.artifacts:
                self.artifacts.write_efficiency_reports(
                    self.efficiency.scores(), recommendation, efficiency_details
                )

    def _gate_run_state(self) -> bool:
        while True:
            with self._lock:
                state = self.snapshot.state
            if state == RunState.STOPPING:
                with self._lock:
                    self.snapshot.state = RunState.STOPPED
                    self.last_status = "Stopped"
                    self._save_state()
                return False
            if state == RunState.PAUSED:
                self._pause_event.wait(timeout=0.5)
                continue
            if state != RunState.RUNNING:
                return False
            return True

    def _generate_wave_tests(self, goal: TaskGoal, config: RunConfig, previous_tests: int) -> List[TestSpec]:
        coverage_gaps = [
            "error handling",
            "boundary inputs",
            "regression from previous failure",
        ]
        wave = self.snapshot.wave_name
        tests_path = os.path.join(self.root_dir, "tests", f"test_{wave.lower()}.py")

        generated = self.test_bot.generate_next_wave(
            previous_results=[{"last_status": self.last_status}],
            coverage_gaps=coverage_gaps,
            wave=wave,
            target_file=goal.target_files[0],
            tests_path=tests_path,
        )
        approved = self.judge_bot.validate_tests(generated)

        if self.artifacts:
            self.artifacts.append_event(
                "tests_generated",
                {
                    "wave": wave,
                    "generated": len(generated),
                    "approved": len(approved),
                    "previous_tests": previous_tests,
                },
            )
            self.artifacts.append_progress(
                f"Wave {wave}: generated {len(generated)} tests, approved {len(approved)}"
            )

        return approved

    def _implement(
        self,
        goal: TaskGoal,
        specs: List[TestSpec],
        attempt: int,
        config: RunConfig,
    ) -> None:
        target_path = os.path.join(self.root_dir, goal.target_files[0])
        existing = self._read_file(target_path)
        tests_summary = "\n\n".join(spec.content for spec in specs)[:5000]

        prompt = (
            "You are implementing code to satisfy tests. Return code only."
            f"\nTarget file: {goal.target_files[0]}"
            f"\nAttempt: {attempt + 1}"
            f"\nGoal: {goal.prompt}"
            f"\nExisting code:\n{existing[:4000]}"
            f"\nTests:\n{tests_summary}"
        )
        guidance = ""
        if config.failure_memory_enabled:
            guidance = self.failure_memory.format_guidance(
                prompt=goal.prompt,
                limit=config.failure_memory_limit,
            )
            if guidance:
                prompt += f"\n\n{guidance}\n"
                with self._lock:
                    self.snapshot.failure_memory_hits += 1
                    self._save_state()

        generated = self.coder_agent.generate(prompt)
        if not generated.strip():
            generated = existing or "def placeholder():\n    return None\n"

        cleaned = (
            generated.replace("```", "")
            .replace("```", "")
            .strip()
            + "\n"
        )
        self._backup_file(target_path)
        self._write_file(target_path, cleaned)

        if self.artifacts:
            self.artifacts.append_event(
                "implementation_written",
                {"target_file": target_path, "attempt": attempt + 1},
            )
            if guidance:
                self.artifacts.append_event(
                    "failure_memory_guidance_used",
                    {"guidance_excerpt": guidance[:500]},
                )

    def _judge(self, goal: TaskGoal, specs: List[TestSpec], config: RunConfig):
        tests_path = specs[0].path
        passed, output = self.judge_bot.run_tests_with_command(
            tests_path=tests_path,
            cwd=self.root_dir,
            command_template=config.test_command,
        )
        if self.artifacts:
            self.artifacts.append_event(
                "judge_result",
                {"passed": passed, "output_excerpt": output[:600]},
            )
        return passed, output

    def _spawn_for_failure(self, goal: TaskGoal, judge_output: str, config: RunConfig) -> None:
        records = self.spawn_manager.evaluate_and_spawn(
            context={
                "active_agents": self.snapshot.active_topology,
                "repeated_failure_count": 2,
                "judge_confidence": 0.45 if "ERROR" in judge_output.upper() else 0.6,
                "flaky_tests_detected": "flaky" in judge_output.lower(),
                "diff_complexity": len(judge_output),
                "candidate_files": goal.target_files,
            },
            max_concurrent_agents=config.max_concurrent_agents,
        )

        for record in records:
            with self._lock:
                if record.agent_name not in self.snapshot.active_topology:
                    self.snapshot.active_topology.append(record.agent_name)
                self.snapshot.spawn_count += 1
            if self.artifacts:
                self.artifacts.append_event("agent_spawned", asdict(record))
                self.artifacts.append_progress(
                    f"Spawned {record.agent_name}: {record.reason}"
                )
                self.artifacts.append_spawn_record(record)

    def _apply_fix_pass(self, goal: TaskGoal, fix_list: str) -> None:
        target_path = os.path.join(self.root_dir, goal.target_files[0])
        existing = self._read_file(target_path)
        prompt = (
            "Update code according to these fix instructions. Return code only.\n"
            f"Fix list:\n{fix_list}\n\nCurrent code:\n{existing[:5000]}"
        )
        candidate = self.coder_agent.generate(prompt)
        if candidate.strip():
            cleaned = candidate.replace("```", "").strip() + "\n"
            self._backup_file(target_path)
            self._write_file(target_path, cleaned)
            self.failure_memory.append_rule(
                f"For task '{goal.prompt[:80]}', successful mitigation used: {fix_list[:180]}"
            )

    def _record_failure(self, goal: TaskGoal, judge_output: str, fix_list: str) -> None:
        target_path = os.path.join(self.root_dir, goal.target_files[0])
        code = self._read_file(target_path)
        self.failure_memory.log_failure(
            prompt=goal.prompt,
            code=code,
            error_message=judge_output,
            wave_name=self.snapshot.wave_name,
            target_file=goal.target_files[0],
            fix_summary=fix_list,
        )
        guidance = self.failure_memory.format_guidance(goal.prompt, limit=1)
        if self.artifacts:
            self.artifacts.append_failure_memory_entry(
                prompt=goal.prompt,
                error_message=judge_output,
                guidance=guidance,
            )

    def _rotate_topology(self, config: RunConfig, wave_idx: int) -> None:
        window = max(1, config.topology_eval_window_cycles)
        if wave_idx > 0 and wave_idx % window == 0:
            fallback = "+".join(config.topology_candidates[0])
            recommended = self.efficiency.recommend_topology(fallback)
            self.snapshot.recommendation = recommended
            self.snapshot.active_topology = recommended.split("+")
            return

        candidate = config.topology_candidates[min(wave_idx, len(config.topology_candidates) - 1)]
        self.snapshot.active_topology = list(candidate)

    def _run_team_brainstorm(self, config: RunConfig, cycle_index: int) -> None:
        team_a_paths = [
            path if os.path.isabs(path) else os.path.join(self.root_dir, path)
            for path in config.team_a_files
        ]
        team_b_paths = [
            path if os.path.isabs(path) else os.path.join(self.root_dir, path)
            for path in config.team_b_files
        ]

        comparison = self.team_comparator.compare(team_a_paths, team_b_paths)
        ideas = self.brainstorm_engine.brainstorm(
            comparison=comparison,
            top_n=config.brainstorm_top_n,
        )
        serializable_ideas = [asdict(idea) for idea in ideas]

        with self._lock:
            self.snapshot.team_ideas_count += len(serializable_ideas)
            self.snapshot.latest_brainstorm_summary = (
                serializable_ideas[0]["idea"] if serializable_ideas else ""
            )
            self._save_state()

        if self.artifacts:
            self.artifacts.append_event(
                "team_brainstorm",
                {
                    "cycle_index": cycle_index,
                    "team_a_files": config.team_a_files,
                    "team_b_files": config.team_b_files,
                    "comparison": comparison,
                    "ideas": serializable_ideas,
                },
            )
            self.artifacts.append_progress(
                f"Team brainstorm produced {len(serializable_ideas)} transferable ideas."
            )
            self.artifacts.append_team_brainstorm(
                cycle_index=cycle_index,
                team_a_files=config.team_a_files,
                team_b_files=config.team_b_files,
                ideas=serializable_ideas,
            )

    def _estimate_diff_churn(self, rel_path: str) -> int:
        path = os.path.join(self.root_dir, rel_path)
        current = self._read_file(path)
        backup = self._read_file(path + ".bak")
        if not backup:
            return 0
        return abs(len(current.splitlines()) - len(backup.splitlines()))

    def _backup_file(self, path: str) -> None:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read()
            with open(path + ".bak", "w", encoding="utf-8") as handle:
                handle.write(content)

    def _read_file(self, path: str) -> str:
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def _write_file(self, path: str, content: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def _count_tests(self, content: str) -> int:
        return sum(1 for line in content.splitlines() if line.strip().startswith("def test_"))

    def _complete(self, status: str) -> None:
        with self._lock:
            if self.snapshot.state not in {RunState.STOPPED, RunState.FAILED}:
                self.snapshot.state = RunState.COMPLETE
            self.last_status = status
            self._save_state()
        if self.artifacts:
            self.artifacts.append_event("run_complete", {"status": status})
            self.artifacts.append_progress(status)
