from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, Optional

from .artifacts import ArtifactStore
from .bots import JudgeBot, SimpleAgent, TestBot
from .compaction import DistillationLoop
from .efficiency import EfficiencyAnalyzer
from .failure_memory import FailureMemory
from .hallucination_guard import HallucinationGuard
from .prompt_guard import PromptGuard
from .rosetta import RosettaStone
from .skill_evolution import SkillEvolutionManager
from .spawn import AgentDescriptor, AgentRegistry, SpawnManager
from .stability_guard import GuardDecision, StabilityGuard
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
        context_guard_agent: Optional[SimpleAgent] = None,
        pattern_agent: Optional[SimpleAgent] = None,
        compression_agent: Optional[SimpleAgent] = None,
        novelty_agent: Optional[SimpleAgent] = None,
        stability_guard_agent: Optional[SimpleAgent] = None,
    ):
        self.root_dir = root_dir
        self.prompt_guard = PromptGuard(guard_agent=context_guard_agent)
        self.rosetta = RosettaStone()
        self.skill_evolution = SkillEvolutionManager()
        self.test_bot = TestBot(test_agent, prompt_guard=self.prompt_guard)
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
        self.hallucination_guard = HallucinationGuard(self.root_dir)
        self.distillation = DistillationLoop(
            pattern_agent=pattern_agent,
            compression_agent=compression_agent,
            novelty_agent=novelty_agent,
        )
        self.stability_guard = StabilityGuard(stability_guard_agent)
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
        self.last_failure_context = ""
        self.last_spawn_wave_index = -999
        self.active_memory_format = "NARRATIVE"
        self.memory_format_performance = {"BLUEPRINT": 0.0, "NARRATIVE": 0.0, "COMMAND": 0.0}
        self.latest_memory_payloads = {"BLUEPRINT": "", "NARRATIVE": "", "COMMAND": ""}
        self.latest_breadcrumb = ""
        self.active_compaction_interval = 5
        self.recent_metrics: List[RunMetrics] = []
        self.unfinished_features: List[str] = []
        self.current_focus = ""
        self.open_handoffs: Dict[str, Dict] = {}
        self.handoff_feedback_log: List[str] = []
        self.ramp_level = 0
        self.guard_spawn_pause_until_wave = -1
        self.guard_mode = "NORMAL"
        self.agent_last_active_wave: Dict[str, int] = {}
        self.handoff_mismatch_count = 0
        self.latest_handoff_brief = ""
        self.rosetta_warning_count = 0
        self.latest_rosetta_warning = ""
        self.latest_skill_event = ""
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
                active_memory_format=self.active_memory_format,
                compaction_interval_active=max(1, run_config.compaction_interval_waves),
                directives_active=run_config.directive_mode_enabled,
            )
            self.active_compaction_interval = max(1, run_config.compaction_interval_waves)
            self.recent_metrics = []
            self.unfinished_features = []
            self.current_focus = goal.prompt[: run_config.max_problem_scope_chars]
            self.open_handoffs = {}
            self.handoff_feedback_log = []
            self.ramp_level = 0
            self.guard_spawn_pause_until_wave = -1
            self.guard_mode = "NORMAL"
            self.agent_last_active_wave = {}
            self.handoff_mismatch_count = 0
            self.latest_handoff_brief = ""
            self.rosetta_warning_count = 0
            self.latest_rosetta_warning = ""
            self.latest_skill_event = ""
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
        self.snapshot.unfinished_feature_count = len(self.unfinished_features)
        self.snapshot.current_focus = self.current_focus[:300]
        self.snapshot.open_handoff_count = len(self.open_handoffs)
        self.snapshot.latest_handoff_feedback = (
            self.handoff_feedback_log[-1][:300] if self.handoff_feedback_log else ""
        )
        self.snapshot.ramp_level = self.ramp_level
        self.snapshot.guard_mode = self.guard_mode
        self.snapshot.handoff_mismatch_count = self.handoff_mismatch_count
        self.snapshot.latest_handoff_brief = self.latest_handoff_brief[:280]
        self.snapshot.rosetta_warning_count = self.rosetta_warning_count
        self.snapshot.latest_rosetta_warning = self.latest_rosetta_warning[:300]
        self.snapshot.active_skill_count = len(self.skill_evolution.active_skills)
        self.snapshot.skill_retool_count = self.skill_evolution.retool_count
        self.snapshot.latest_skill_event = self.latest_skill_event[:280]
        if self.artifacts:
            self.artifacts.save_snapshot(self.snapshot)

    def _run_loop(self, goal: TaskGoal, config: RunConfig) -> None:
        previous_tests = 0
        efficiency_details: List[Dict] = []
        consecutive_failed_waves = 0

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
                self._apply_population_control(config)

                cycle_start = time.time()
                pass_count = 0
                attempts = 0
                failure_recurrence = 0

                retry_limit = config.local_retry_limit + self.ramp_level
                for attempt in range(retry_limit + 1):
                    if not self._gate_run_state():
                        return

                    attempts += 1
                    with self._lock:
                        self.snapshot.phase = ControllerPhase.IMPLEMENT
                        self._save_state()

                    generated_code = self._implement(goal, test_specs, attempt, config)

                    guard_passed = True
                    if config.hallucination_guard_enabled:
                        with self._lock:
                            self.snapshot.phase = ControllerPhase.HALLUCINATION_GUARD
                            self._save_state()
                        guard_passed = self._run_hallucination_guard(
                            goal=goal,
                            generated_code=generated_code,
                            config=config,
                            cycle_index=wave_idx,
                        )
                        if not guard_passed:
                            failure_recurrence += 1
                            continue

                    with self._lock:
                        self.snapshot.phase = ControllerPhase.JUDGE
                        self._save_state()

                    passed, judge_output = self._judge(goal, test_specs, config)
                    if passed:
                        pass_count = len(test_specs)
                        self._resolve_current_unfinished_feature()
                        self._resolve_open_handoffs("Tests passed for current wave.")
                        break

                    failure_recurrence += 1
                    self._register_unfinished_feature(judge_output)
                    if config.dynamic_spawning_enabled:
                        self._spawn_for_failure(goal, judge_output, config)

                    fix_list = self._generate_fix_list(judge_output, config)
                    self.artifacts.append_progress(
                        f"Retry {attempt + 1}: judge suggested fixes captured"
                    )
                    if config.failure_memory_enabled:
                        self._record_failure(goal, judge_output, fix_list)
                    self._register_unfinished_feature(fix_list)
                    self._pass_back_failed_handoffs(
                        failure_output=judge_output,
                        fix_list=fix_list,
                        config=config,
                    )
                    self._apply_fix_pass(goal, fix_list)

                wave_duration = max(0.01, time.time() - cycle_start)
                pass_rate = pass_count / max(1, len(test_specs))

                with self._lock:
                    self.snapshot.passing_tests += pass_count
                    self.snapshot.phase = ControllerPhase.WAVE_PROMOTION
                    self._save_state()

                coverage_gain = max(0.0, (self.snapshot.total_tests - previous_tests) / max(1, previous_tests + 1))
                if pass_rate < 1.0:
                    consecutive_failed_waves += 1
                    self.snapshot.no_gain_waves += 1
                    self.snapshot.consecutive_failed_waves = consecutive_failed_waves
                    self.artifacts.append_progress(
                        f"Wave {self.snapshot.wave_name} failed to fully pass."
                    )
                else:
                    consecutive_failed_waves = 0
                    self.snapshot.consecutive_failed_waves = 0
                    if coverage_gain < config.min_new_coverage_gain:
                        self.snapshot.no_gain_waves += 1
                    else:
                        self.snapshot.no_gain_waves = 0

                if consecutive_failed_waves >= max(1, config.max_consecutive_failed_waves):
                    self._complete("Consecutive failed waves limit reached.", config=config)
                    return

                if self.snapshot.total_tests >= config.max_total_tests:
                    self._complete("Reached max_total_tests.", config=config)
                    return

                if self.snapshot.no_gain_waves >= config.max_no_gain_waves:
                    self._complete("Coverage gain plateau reached.", config=config)
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
                self.recent_metrics.append(metric)
                self.recent_metrics = self.recent_metrics[-6:]
                score = self.efficiency.update(topology_key, metric)
                self._update_memory_format_performance(metric)
                self._update_ramp_level(config, metric)
                decision = self.stability_guard.evaluate(
                    snapshot=self.snapshot,
                    metric=metric,
                    recent_metrics=self.recent_metrics,
                    config=config,
                )
                if self._apply_guard_decision(decision, config):
                    return
                self._evaluate_skill_evolution(metric=metric, config=config)
                self._adapt_compaction_policy(config, metric)
                self._refresh_user_guidance(config)
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
                self._apply_population_control(config)
                if config.team_mode_enabled:
                    self._run_team_brainstorm(config, wave_idx)
                if config.memory_distillation_enabled:
                    self._run_memory_distillation(config, wave_idx)

                previous_tests = self.snapshot.total_tests

            self._complete("Completed configured waves.", config=config)
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
                if config.memory_distillation_enabled:
                    self.artifacts.write_memory_format_benchmark(
                        active_format=self.active_memory_format,
                        scores=self.memory_format_performance,
                        payloads=self.latest_memory_payloads,
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

    def _build_directive_block(
        self,
        goal: TaskGoal,
        config: RunConfig,
        purpose: str = "implementation",
    ) -> str:
        focus = self.unfinished_features[0] if self.unfinished_features else goal.prompt
        focus = (focus or "").strip()[: max(200, config.max_problem_scope_chars)]
        if config.rosetta_enabled:
            mediated = self.rosetta.mediate(
                text=focus,
                audience="specialist",
                max_chars=config.rosetta_max_chars,
            )
            focus = mediated.text
            if mediated.warnings:
                self.rosetta_warning_count += len(mediated.warnings)
                self.latest_rosetta_warning = mediated.warnings[0]
        self.current_focus = focus
        if purpose == "testing" and config.testing_agents_exempt_from_directives:
            return f"Testing objective: {focus}"
        if not config.directive_mode_enabled:
            return f"Current objective: {focus}"

        directives = [
            "Directive Policy (must follow):",
            "1. Complete unfinished features before adding new features.",
            "2. Keep the implementation scope small and focused on the current objective.",
            "3. Avoid duplicated code; prefer reusing or refactoring existing logic.",
            "4. If a change introduces a failure, fix that failure before attempting new work.",
            f"Current small-scope objective: {focus}",
        ]
        return "\n".join(directives)

    def _register_unfinished_feature(self, text: str) -> None:
        line = (text or "").strip().splitlines()[0][:240]
        if not line:
            return
        exists = any(item.lower() == line.lower() for item in self.unfinished_features)
        if not exists:
            self.unfinished_features.append(line)

    def _resolve_current_unfinished_feature(self) -> None:
        if self.unfinished_features:
            resolved = self.unfinished_features.pop(0)
            if self.artifacts:
                self.artifacts.append_progress(f"Resolved unfinished feature: {resolved}")

    def _implement(
        self,
        goal: TaskGoal,
        specs: List[TestSpec],
        attempt: int,
        config: RunConfig,
    ) -> str:
        target_path = os.path.join(self.root_dir, goal.target_files[0])
        existing = self._read_file(target_path)
        tests_summary = "\n\n".join(spec.content for spec in specs)[:5000]

        directive_block = self._build_directive_block(
            goal=goal,
            config=config,
            purpose="implementation",
        )
        prompt = (
            "You are implementing code to satisfy tests. Return code only."
            f"\nTarget file: {goal.target_files[0]}"
            f"\nAttempt: {attempt + 1}"
            f"\nGoal: {goal.prompt}"
            f"\n\n{directive_block}"
            f"\nExisting code:\n{existing[:4000]}"
            f"\nTests:\n{tests_summary}"
        )
        if self.handoff_feedback_log:
            feedback = "\n".join(f"- {item}" for item in self.handoff_feedback_log[-3:])
            prompt += (
                "\n\nDelegation accountability feedback (previous failed handoffs):\n"
                f"{feedback}"
            )
        if self.latest_memory_payloads.get(self.active_memory_format):
            payload = self.latest_memory_payloads[self.active_memory_format]
            prompt += (
                f"\n\nDistilled memory ({self.active_memory_format}):\n"
                f"{payload[:2200]}"
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

        generated = self._safe_generate(
            agent=self.coder_agent,
            prompt=prompt,
            purpose="implementation",
            config=config,
        )
        if not generated.strip():
            generated = existing or "def placeholder():\n    return None\n"

        cleaned = (
            generated.replace("```", "")
            .replace("```", "")
            .strip()
            + "\n"
        )
        if config.enforce_no_duplicate_code and self._has_duplicate_blocks(cleaned):
            self._register_unfinished_feature(
                "Potential duplicated logic detected. Refactor by extracting shared logic before adding new functionality."
            )
            if self.artifacts:
                self.artifacts.append_progress(
                    "Duplicate-logic risk detected; queued targeted refactor as unfinished feature."
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
        return cleaned

    def _has_duplicate_blocks(self, code: str) -> bool:
        lines = [line.strip() for line in code.splitlines() if line.strip()]
        if len(lines) < 20:
            return False
        windows = {}
        window_size = 5
        for idx in range(0, len(lines) - window_size + 1):
            block = "\n".join(lines[idx: idx + window_size])
            key = block.lower()
            windows[key] = windows.get(key, 0) + 1
            if windows[key] >= 2 and len(block) > 120:
                return True
        return False

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
        if self.snapshot.wave_index <= self.guard_spawn_pause_until_wave:
            if self.artifacts:
                self.artifacts.append_progress(
                    f"StabilityGuard paused spawning through wave {self.guard_spawn_pause_until_wave}."
                )
            return
        if len(self.open_handoffs) >= max(1, config.guard_max_open_handoffs):
            if self.artifacts:
                self.artifacts.append_progress(
                    "Spawning blocked: open handoff debt at guard threshold."
                )
            return
        cooldown_violation = (
            (self.snapshot.wave_index - self.last_spawn_wave_index)
            <= config.spawn_cooldown_cycles
        )
        records = self.spawn_manager.evaluate_and_spawn(
            context={
                "requesting_agent": "SwarmController",
                "active_agents": self.snapshot.active_topology,
                "repeated_failure_count": 2,
                "judge_confidence": 0.45 if "ERROR" in judge_output.upper() else 0.6,
                "flaky_tests_detected": "flaky" in judge_output.lower(),
                "diff_complexity": len(judge_output),
                "candidate_files": goal.target_files,
                "pass_rate": (
                    self.snapshot.passing_tests / max(1, self.snapshot.total_tests)
                    if self.snapshot.total_tests
                    else 0.0
                ),
                "cooldown_violation": cooldown_violation,
                "spawn_min_benefit_score": config.spawn_min_benefit_score,
            },
            max_concurrent_agents=max(1, config.max_concurrent_agents + self.ramp_level),
        )

        for record in records:
            brief = self._build_handoff_brief(
                goal=goal,
                judge_output=judge_output,
                config=config,
            )
            with self._lock:
                if record.agent_name not in self.snapshot.active_topology:
                    self.snapshot.active_topology.append(record.agent_name)
                self.snapshot.spawn_count += 1
                self.last_spawn_wave_index = self.snapshot.wave_index
                self.agent_last_active_wave[record.agent_name] = self.snapshot.wave_index
                if record.handoff_id:
                    self.open_handoffs[record.handoff_id] = {
                        "record": asdict(record),
                        "status": "OPEN",
                        "brief": brief,
                    }
                    self.latest_handoff_brief = brief
            if self.artifacts:
                self.artifacts.append_event("agent_spawned", asdict(record))
                self.artifacts.append_event(
                    "handoff_brief",
                    {
                        "handoff_id": record.handoff_id,
                        "agent_name": record.agent_name,
                        "brief": brief,
                    },
                )
                self.artifacts.append_progress(
                    f"Spawned {record.agent_name}: {record.reason}"
                )
                self.artifacts.append_spawn_record(record)
            if config.spin_off_tests_to_ecosystems and "TestRefiner" in record.agent_name:
                self._generate_spin_off_tests(goal=goal, config=config, source_record=record)

    def _build_handoff_brief(self, goal: TaskGoal, judge_output: str, config: RunConfig) -> str:
        if not config.need_to_know_enabled:
            return judge_output[: config.handoff_brief_max_chars]
        focus = (self.current_focus or goal.prompt or "").strip()[:140]
        failure = (judge_output or "").replace("\n", " ").strip()[:160]
        files = ", ".join(self._ecosystem_targets(goal, config)[:2])
        brief = f"Focus={focus}; Failure={failure}; Files={files}"
        if config.rosetta_enabled:
            mediated = self.rosetta.mediate(
                text=brief,
                audience="specialist",
                max_chars=min(config.handoff_brief_max_chars, config.rosetta_max_chars),
            )
            brief = mediated.text
            if mediated.warnings:
                self.rosetta_warning_count += len(mediated.warnings)
                self.latest_rosetta_warning = mediated.warnings[0]
        return brief[: max(80, config.handoff_brief_max_chars)]

    def _token_set(self, text: str) -> set:
        return set(re.findall(r"[a-zA-Z_]{3,}", (text or "").lower()))

    def _is_handoff_mismatch(
        self,
        handoff_payload: Dict,
        failure_output: str,
        fix_list: str,
        config: RunConfig,
    ) -> bool:
        if not config.mismatch_learning_enabled:
            return False
        record = handoff_payload.get("record", {}) if isinstance(handoff_payload, dict) else {}
        brief = handoff_payload.get("brief", "") if isinstance(handoff_payload, dict) else ""
        left = " ".join(
            [
                str(record.get("task_scope", "")),
                str(record.get("files_touched", "")),
                str(brief),
            ]
        )
        right = f"{failure_output} {fix_list}"
        a = self._token_set(left)
        b = self._token_set(right)
        if not a or not b:
            return False
        overlap = len(a.intersection(b)) / max(1, len(a))
        return overlap < float(config.mismatch_overlap_threshold)

    def _ecosystem_targets(self, goal: TaskGoal, config: RunConfig) -> List[str]:
        targets: List[str] = []
        for path in (config.team_a_files + config.team_b_files):
            if path not in targets:
                targets.append(path)
        if not targets:
            targets = list(goal.target_files)
        return targets

    def _generate_spin_off_tests(
        self,
        goal: TaskGoal,
        config: RunConfig,
        source_record,
    ) -> None:
        targets = self._ecosystem_targets(goal, config)
        for rel_target in targets:
            base = os.path.splitext(os.path.basename(rel_target))[0] or "module"
            tests_path = os.path.join(self.root_dir, "tests", f"test_spinoff_{base}.py")
            generated = self.test_bot.generate_next_wave(
                previous_results=[{"spawn_reason": source_record.reason}],
                coverage_gaps=["boundary inputs", "error handling", "regressions"],
                wave=f"SPINOFF_{self.snapshot.wave_name}",
                target_file=rel_target,
                tests_path=tests_path,
            )
            approved = self.judge_bot.validate_tests(generated)
            for spec in approved:
                self._write_file(spec.path, spec.content)
                with self._lock:
                    self.snapshot.total_tests += self._count_tests(spec.content)
                    self._save_state()
            if self.artifacts:
                self.artifacts.append_event(
                    "spinoff_tests_generated",
                    {
                        "target_file": rel_target,
                        "tests_path": tests_path,
                        "count": len(approved),
                        "source_agent": source_record.agent_name,
                    },
                )
                self.artifacts.append_progress(
                    f"Spin-off tests generated for ecosystem target {rel_target}: {len(approved)} file(s)."
                )

    def _apply_guard_decision(self, decision: GuardDecision, config: RunConfig) -> bool:
        action = (decision.action or "NONE").upper()
        reason = (decision.reason or "").strip()[:300]
        focus = (decision.focus or "").strip()[:280]
        if action == "NONE":
            self.guard_mode = "NORMAL"
            return False

        with self._lock:
            self.snapshot.guard_interventions += 1
            self.snapshot.latest_guard_action = action
            self.snapshot.latest_guard_reason = reason

        if action == "DEFLECT":
            self.guard_mode = "DEFLECT"
            self.ramp_level = max(0, self.ramp_level - 1)
            self.active_compaction_interval = max(
                config.min_compaction_interval_waves,
                min(config.max_compaction_interval_waves, self.active_compaction_interval + 1),
            )
            pause = max(1, decision.spawn_pause_waves or config.guard_spawn_pause_waves)
            self.guard_spawn_pause_until_wave = max(
                self.guard_spawn_pause_until_wave,
                self.snapshot.wave_index + pause,
            )
            if focus:
                self._register_unfinished_feature(focus)
                self.current_focus = focus
            if self.artifacts:
                self.artifacts.append_event(
                    "stability_guard_intervention",
                    {
                        "action": action,
                        "reason": reason,
                        "focus": focus,
                        "spawn_pause_until_wave": self.guard_spawn_pause_until_wave,
                    },
                )
                self.artifacts.append_progress(
                    f"StabilityGuard DEFLECT: {reason}"
                )
            self._save_state()
            self._apply_population_control(config)
            return False

        if action == "REDIRECT":
            self.guard_mode = "REDIRECT"
            if focus:
                self._register_unfinished_feature(focus)
                self.current_focus = focus
            if self.artifacts:
                self.artifacts.append_event(
                    "stability_guard_intervention",
                    {"action": action, "reason": reason, "focus": focus},
                )
                self.artifacts.append_progress(
                    f"StabilityGuard REDIRECT: {reason}"
                )
            self._save_state()
            return False

        if action == "HALT":
            self.guard_mode = "HALT"
            if self.artifacts:
                self.artifacts.append_event(
                    "stability_guard_intervention",
                    {"action": action, "reason": reason},
                )
                self.artifacts.append_progress(
                    f"StabilityGuard HALT: {reason}"
                )
            self._complete(f"StabilityGuard HALT: {reason}", config=config)
            return True

        return False

    def _pass_back_failed_handoffs(self, failure_output: str, fix_list: str, config: Optional[RunConfig] = None) -> None:
        if not self.open_handoffs:
            return
        cfg = config or RunConfig()
        failure_excerpt = (failure_output or "").replace("\n", " ")[:320]
        fix_excerpt = (fix_list or "").replace("\n", " ")[:280]
        for handoff_id, payload in list(self.open_handoffs.items()):
            if payload.get("status") != "OPEN":
                continue
            record = payload.get("record", {})
            parent = record.get("parent_agent", "SwarmController")
            agent = record.get("agent_name", "unknown")
            brief = str(payload.get("brief", ""))[: cfg.handoff_feedback_max_chars]
            feedback = (
                f"handoff={handoff_id}; brief={brief}; "
                f"failure={failure_excerpt[:cfg.handoff_feedback_max_chars]}; "
                f"fix={fix_excerpt[:cfg.handoff_feedback_max_chars]}"
            )
            if cfg.rosetta_enabled:
                mediated = self.rosetta.mediate(
                    text=feedback,
                    audience="language",
                    max_chars=min(cfg.handoff_feedback_max_chars, cfg.rosetta_max_chars),
                )
                feedback = mediated.text
                if mediated.warnings:
                    self.rosetta_warning_count += len(mediated.warnings)
                    self.latest_rosetta_warning = mediated.warnings[0]
            note = (
                f"Handoff {handoff_id} returned to {parent} from {agent}: "
                f"{feedback}"
            )
            payload["status"] = "RETURNED_WITH_FAILURE"
            self.handoff_feedback_log.append(note)
            if self._is_handoff_mismatch(payload, failure_output, fix_list, cfg):
                self.handoff_mismatch_count += 1
                self.skill_evolution.observe_pattern("handoff_mismatch")
                learn = (
                    f"Handoff mismatch detected for {agent}. Reassign narrower task scope "
                    f"with explicit file and failing behavior."
                )
                self._register_unfinished_feature(learn)
                self.failure_memory.append_rule(learn)
                if self.artifacts:
                    self.artifacts.append_event(
                        "handoff_mismatch",
                        {
                            "handoff_id": handoff_id,
                            "agent_name": agent,
                            "brief": brief,
                            "failure_excerpt": failure_excerpt[:220],
                        },
                    )
                    self.artifacts.append_progress(
                        f"Need-to-know mismatch learned for handoff {handoff_id}."
                    )
            if self.artifacts:
                self.artifacts.append_event(
                    "handoff_returned",
                    {
                        "handoff_id": handoff_id,
                        "parent_agent": parent,
                        "agent_name": agent,
                        "status": payload["status"],
                        "feedback": feedback,
                    },
                )
                self.artifacts.append_progress(note)
        self.handoff_feedback_log = self.handoff_feedback_log[-10:]
        with self._lock:
            self._save_state()

    def _resolve_open_handoffs(self, resolution_note: str) -> None:
        if not self.open_handoffs:
            return
        for handoff_id, payload in list(self.open_handoffs.items()):
            payload["status"] = "RESOLVED"
            if self.artifacts:
                self.artifacts.append_event(
                    "handoff_resolved",
                    {
                        "handoff_id": handoff_id,
                        "resolution_note": resolution_note[:300],
                        "agent_name": payload.get("record", {}).get("agent_name", "unknown"),
                    },
                )
        self.open_handoffs = {}
        with self._lock:
            self._save_state()

    def _close_unresolved_handoffs(self, reason: str) -> None:
        if not self.open_handoffs:
            return
        for handoff_id, payload in list(self.open_handoffs.items()):
            status = payload.get("status", "OPEN")
            if status != "RESOLVED":
                payload["status"] = "CLOSED_UNRESOLVED"
                if self.artifacts:
                    self.artifacts.append_event(
                        "handoff_closed_unresolved",
                        {
                            "handoff_id": handoff_id,
                            "reason": reason[:300],
                            "agent_name": payload.get("record", {}).get("agent_name", "unknown"),
                        },
                    )
        self.open_handoffs = {}
        with self._lock:
            self._save_state()

    def _evaluate_skill_evolution(self, metric: RunMetrics, config: RunConfig) -> None:
        if self.snapshot.consecutive_failed_waves >= 2:
            self.skill_evolution.observe_pattern("failure_recurrence")
        if self.snapshot.no_gain_waves >= 2:
            self.skill_evolution.observe_pattern("coverage_plateau")
        if self.snapshot.open_handoff_count >= 2:
            self.skill_evolution.observe_pattern("handoff_debt")
        if self.rosetta_warning_count >= 2:
            self.skill_evolution.observe_pattern("prompt_translation")

        event = self.skill_evolution.evaluate(
            snapshot=self.snapshot,
            metric=metric,
            config=config,
        )
        if not event:
            return
        self.latest_skill_event = f"{event.action} {event.skill_name}: {event.reason}"
        if self.artifacts:
            self.artifacts.append_event(
                "skill_evolution",
                {
                    "action": event.action,
                    "skill_name": event.skill_name,
                    "reason": event.reason,
                    "impact_delta": event.impact_delta,
                    "active_skill_count": len(self.skill_evolution.active_skills),
                },
            )
            self.artifacts.append_skill_event(
                action=event.action,
                skill_name=event.skill_name,
                reason=event.reason,
                impact_delta=event.impact_delta,
            )
            self.artifacts.append_progress(self.latest_skill_event)
        self._save_state()

    def _apply_fix_pass(self, goal: TaskGoal, fix_list: str) -> None:
        target_path = os.path.join(self.root_dir, goal.target_files[0])
        existing = self._read_file(target_path)
        prompt = (
            "Update code according to these fix instructions. Return code only.\n"
            f"Fix list:\n{fix_list}\n\nCurrent code:\n{existing[:5000]}"
        )
        candidate = self._safe_generate(
            agent=self.coder_agent,
            prompt=prompt,
            purpose="fix-pass",
            config=RunConfig(),
        )
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
        self.last_failure_context = (
            f"failure={judge_output[:700]}\nfix={fix_list[:500]}\n"
            f"guidance={guidance[:500]}"
        )
        if self.artifacts:
            self.artifacts.append_failure_memory_entry(
                prompt=goal.prompt,
                error_message=judge_output,
                guidance=guidance,
            )

    def _generate_fix_list(self, failure_output: str, config: RunConfig) -> str:
        prompt = (
            "Summarize failures into a concise actionable fix list. "
            "Include probable root cause and exact edits.\n\n"
            f"Failure log:\n{failure_output[:6000]}"
        )
        response = self._safe_generate(
            agent=self.judge_bot.agent,
            prompt=prompt,
            purpose="fix-list",
            config=config,
        )
        return response or failure_output[:1200]

    def _safe_generate(
        self,
        agent: SimpleAgent,
        prompt: str,
        purpose: str,
        config: RunConfig,
    ) -> str:
        working_prompt = prompt

        if config.prompt_guard_enabled:
            guarded = self.prompt_guard.guard_prompt(
                prompt=working_prompt,
                purpose=purpose,
                max_chars=config.prompt_guard_max_chars,
                complexity_threshold=config.prompt_guard_complexity_threshold,
            )
            working_prompt = guarded.prompt
            if guarded.changed:
                with self._lock:
                    self.snapshot.prompt_refactor_count += 1
                    self.snapshot.latest_prompt_guard_note = guarded.note
                    self._save_state()
                if self.artifacts:
                    self.artifacts.append_prompt_guard_event(
                        purpose=purpose,
                        note=guarded.note,
                        before_chars=len(prompt),
                        after_chars=len(working_prompt),
                    )

        response = agent.generate(working_prompt)
        if response and not response.startswith("ERROR:"):
            return response

        if not config.prompt_guard_retry_on_error:
            return response or ""

        failure_context = self.last_failure_context
        if not failure_context and config.failure_memory_enabled:
            similar = self.failure_memory.retrieve_similar(prompt, limit=2)
            if similar:
                failure_context = "\n".join(
                    [
                        f"- error={item.get('error_message', '')[:220]} fix={item.get('fix_summary', '')[:220]}"
                        for item in similar
                    ]
                )

        retry = self.prompt_guard.refactor_on_failure(
            prompt=working_prompt,
            purpose=purpose,
            failure_message=(response or "empty response"),
            failure_context=failure_context or "none",
            max_chars=config.prompt_guard_max_chars,
        )
        retry_prompt = retry.prompt
        with self._lock:
            self.snapshot.prompt_refactor_count += 1
            self.snapshot.latest_prompt_guard_note = retry.note
            self._save_state()
        if self.artifacts:
            self.artifacts.append_prompt_guard_event(
                purpose=f"{purpose}-retry",
                note=retry.note,
                before_chars=len(working_prompt),
                after_chars=len(retry_prompt),
            )
        return agent.generate(retry_prompt) or ""

    def _run_hallucination_guard(
        self,
        goal: TaskGoal,
        generated_code: str,
        config: RunConfig,
        cycle_index: int,
    ) -> bool:
        target_path = (
            os.path.join(self.root_dir, goal.target_files[0])
            if goal.target_files
            else self.root_dir
        )
        result = self.hallucination_guard.evaluate(
            target_file=target_path,
            code=generated_code,
            prompt=goal.prompt,
            doc_grounding_enabled=config.doc_grounding_enabled,
        )

        with self._lock:
            self.snapshot.hallucination_confidence = result.confidence
            self.snapshot.hallucination_alert_count = len(result.alerts)
            self.snapshot.latest_hallucination_alert = (
                result.alerts[0] if result.alerts else ""
            )
            self._save_state()

        if self.artifacts:
            self.artifacts.append_event(
                "hallucination_guard",
                {
                    "cycle_index": cycle_index,
                    "confidence": result.confidence,
                    "alerts": result.alerts,
                    "unknown_symbols": result.unknown_symbols,
                    "unknown_apis": result.unknown_apis,
                    "missing_doc_grounding": result.missing_doc_grounding,
                },
            )
            self.artifacts.append_hallucination_check(
                cycle_index=cycle_index,
                confidence=result.confidence,
                alerts=result.alerts,
                unknown_symbols=result.unknown_symbols,
                unknown_apis=result.unknown_apis,
            )

        if result.confidence < config.hallucination_alert_threshold and self.artifacts:
            self.artifacts.append_progress(
                f"Hallucination alert: confidence={result.confidence:.3f}"
            )

        if result.confidence < config.hallucination_block_threshold:
            if config.failure_memory_enabled:
                self._record_failure(
                    goal=goal,
                    judge_output=(
                        "HallucinationGuard blocked cycle due to low confidence. "
                        + "; ".join(result.alerts[:3])
                    ),
                    fix_list="Replace unknown symbols/APIs with project-defined alternatives.",
                )
            return False
        return True

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

    def _update_memory_format_performance(self, metric: RunMetrics) -> None:
        reliability = max(0.0, min(1.0, metric.pass_rate))
        speed = 1.0 / max(0.05, metric.duration_seconds)
        stability = 1.0 / (1.0 + metric.failure_recurrence + metric.retries_per_test)
        cycle_score = (reliability * 0.55) + (speed * 0.25) + (stability * 0.20)
        current = self.memory_format_performance.get(self.active_memory_format, 0.0)
        self.memory_format_performance[self.active_memory_format] = round(
            (current * 0.7) + (cycle_score * 0.3), 6
        )

    def _run_memory_distillation(self, config: RunConfig, wave_idx: int) -> None:
        interval = max(1, self.active_compaction_interval)
        if (wave_idx + 1) % interval != 0:
            return
        if not self.artifacts:
            return

        result = self.distillation.run(
            cycle_index=wave_idx,
            failure_library_path=self.failure_memory.library_path,
            progress_path=self.artifacts.progress_path,
            historic_format_scores=self.memory_format_performance,
            rule_limit=config.memory_rule_limit,
            breadcrumb_limit=config.memory_breadcrumb_limit,
        )
        self.active_memory_format = result.selected_format
        self.latest_memory_payloads = result.format_payloads
        self.latest_breadcrumb = result.breadcrumbs[0] if result.breadcrumbs else ""
        self.memory_format_performance.update(result.format_scores)

        with self._lock:
            self.snapshot.compaction_runs += 1
            self.snapshot.active_memory_format = self.active_memory_format
            self.snapshot.latest_memory_winner = result.selected_format
            self.snapshot.latest_breadcrumb = self.latest_breadcrumb
            self.snapshot.compaction_interval_active = self.active_compaction_interval
            self._save_state()

        self.artifacts.append_event(
            "memory_compaction",
            {
                "cycle_index": wave_idx,
                "golden_rules": result.golden_rules,
                "breadcrumbs": result.breadcrumbs,
                "selected_format": result.selected_format,
                "format_scores": result.format_scores,
            },
        )
        self.artifacts.append_memory_primitive(
            cycle_index=wave_idx,
            golden_rules=result.golden_rules,
            breadcrumbs=result.breadcrumbs,
            selected_format=result.selected_format,
        )
        self.artifacts.append_progress(
            f"Memory distillation selected {result.selected_format} format."
        )

    def _adapt_compaction_policy(self, config: RunConfig, metric: RunMetrics) -> None:
        if not config.adaptive_compaction_enabled:
            with self._lock:
                self.snapshot.compaction_interval_active = self.active_compaction_interval
                self._save_state()
            return

        min_interval = max(1, config.min_compaction_interval_waves)
        max_interval = max(min_interval, config.max_compaction_interval_waves)
        current = max(min_interval, min(max_interval, self.active_compaction_interval))

        recent = self.recent_metrics[-3:] if self.recent_metrics else [metric]
        unstable_hits = 0
        stable_hits = 0
        for item in recent:
            item_unstable = (
                item.pass_rate < 1.0
                or item.failure_recurrence >= 2
                or item.retries_per_test > 1.0
                or self.snapshot.hallucination_confidence < config.hallucination_alert_threshold
            )
            item_stable = (
                item.pass_rate >= 1.0
                and item.failure_recurrence == 0
                and item.retries_per_test <= 0.4
                and self.snapshot.hallucination_confidence >= max(
                    config.hallucination_alert_threshold, 0.75
                )
            )
            if item_unstable:
                unstable_hits += 1
            if item_stable:
                stable_hits += 1

        needed = 2 if len(recent) > 1 else 1
        if unstable_hits >= needed:
            current = max(min_interval, current - 1)
        elif stable_hits >= needed:
            current = min(max_interval, current + 1)

        self.active_compaction_interval = current
        with self._lock:
            self.snapshot.compaction_interval_active = current
            self._save_state()

    def _update_ramp_level(self, config: RunConfig, metric: RunMetrics) -> None:
        if not config.ramp_enabled:
            return
        recent = self.recent_metrics[-max(2, config.ramp_step_waves):]
        if not recent:
            recent = [metric]
        unstable_hits = 0
        stable_hits = 0
        for item in recent:
            if item.pass_rate < 1.0 or item.failure_recurrence >= 2 or item.retries_per_test > 1.0:
                unstable_hits += 1
            if item.pass_rate >= 1.0 and item.failure_recurrence == 0 and item.retries_per_test <= 0.5:
                stable_hits += 1

        max_level = max(0, config.ramp_max_level)
        if stable_hits >= max(2, config.ramp_step_waves // 2):
            self.ramp_level = min(max_level, self.ramp_level + 1)
        elif unstable_hits >= max(2, config.ramp_step_waves // 2):
            self.ramp_level = max(0, self.ramp_level - 1)
        with self._lock:
            self._save_state()

    def _apply_population_control(self, config: RunConfig) -> None:
        if not config.population_control_enabled:
            return
        baseline = ["TestBot", "LocalCoder", "JudgeBot"]
        active = list(self.snapshot.active_topology)
        current_wave = self.snapshot.wave_index
        max_agents = max(3, config.max_concurrent_agents)

        changed = False
        if len(active) > max_agents:
            keep = []
            for name in active:
                if name in baseline or len(keep) < max_agents:
                    keep.append(name)
            active = keep[:max_agents]
            changed = True

        grace = max(1, config.specialist_prune_grace_waves)
        if self.guard_mode in {"DEFLECT", "HALT"} or len(self.open_handoffs) >= max(1, config.guard_max_open_handoffs):
            pruned = []
            for name in active:
                if name in baseline:
                    pruned.append(name)
                    continue
                last_wave = self.agent_last_active_wave.get(name, -9999)
                if (current_wave - last_wave) <= grace:
                    pruned.append(name)
            active = pruned
            changed = True

        for name in baseline:
            if name not in active:
                active.insert(0, name)
                changed = True
        # preserve order and uniqueness
        deduped = []
        for name in active:
            if name not in deduped:
                deduped.append(name)
        active = deduped[:max_agents]

        if changed:
            self.snapshot.active_topology = active
            if self.artifacts:
                self.artifacts.append_event(
                    "population_control",
                    {
                        "wave": current_wave,
                        "active_topology": active,
                        "open_handoffs": len(self.open_handoffs),
                        "guard_mode": self.guard_mode,
                    },
                )
                self.artifacts.append_progress(
                    f"Population control adjusted active agents to {len(active)}."
                )
            self._save_state()

    def _refresh_user_guidance(self, config: RunConfig) -> None:
        suggestions: List[str] = []
        warnings: List[str] = []

        if self.snapshot.hallucination_confidence < config.hallucination_alert_threshold:
            warnings.append(
                "Low hallucination confidence detected. Narrow prompt scope and validate symbols before next cycle."
            )
            suggestions.append(
                "Use /memory deep temporarily to increase rule extraction frequency while errors persist."
            )

        if self.snapshot.no_gain_waves >= max(1, config.max_no_gain_waves - 1):
            warnings.append(
                "Coverage gain is plateauing; current loop may be overfitting existing tests."
            )
            suggestions.append(
                "Switch test emphasis to untested branches and boundary failures in the next wave."
            )

        if self.active_compaction_interval <= 2 and self.snapshot.compaction_runs >= 2:
            suggestions.append(
                "Compaction is running frequently due to instability. Raise interval when pass rate stabilizes."
            )

        if self.active_compaction_interval >= 6 and self.snapshot.hallucination_alert_count == 0:
            suggestions.append(
                "System is stable; current longer compaction interval is reducing token/call overhead."
            )

        if self.snapshot.spawn_count >= max(1, config.max_concurrent_agents - 1):
            warnings.append(
                "Agent spawn volume is high. Review spawn policy to avoid coordination overhead."
            )
        if self.open_handoffs:
            warnings.append(
                "Open delegated handoffs exist; resolve returned failures before additional delegation."
            )
        if self.unfinished_features:
            suggestions.append(
                "Prioritize the current unfinished feature before opening additional scope."
            )
            if len(self.unfinished_features) > 3:
                warnings.append(
                    "Unfinished feature queue is growing; reduce task size and complete queued items first."
                )
        if self.guard_mode != "NORMAL":
            warnings.append(
                f"StabilityGuard is active in {self.guard_mode} mode; swarm pressure is being redirected."
            )
            suggestions.append(
                "Focus on the current stabilization objective until guard mode returns to NORMAL."
            )

        if not suggestions:
            suggestions.append(
                "No immediate adjustments needed. Continue with current topology and adaptive compaction policy."
            )

        with self._lock:
            self.snapshot.ui_suggestions = suggestions[:5]
            self.snapshot.ui_warnings = warnings[:5]
            self._save_state()

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

    def _complete(self, status: str, config: Optional[RunConfig] = None) -> None:
        mark_failed = False
        if config is not None:
            meets_pass_threshold = self.snapshot.passing_tests >= max(
                0, config.min_passed_tests_for_success
            )
            handoffs_clear = (
                (not config.require_zero_open_handoffs_for_success)
                or len(self.open_handoffs) == 0
            )
            if not (meets_pass_threshold and handoffs_clear):
                mark_failed = True
                status = (
                    f"{status} Quality gate failed: "
                    f"passed_tests={self.snapshot.passing_tests}, "
                    f"open_handoffs={len(self.open_handoffs)}."
                )

        with self._lock:
            if self.snapshot.state not in {RunState.STOPPED, RunState.FAILED}:
                self.snapshot.state = RunState.FAILED if mark_failed else RunState.COMPLETE
            self.last_status = status
            self._save_state()
        if mark_failed:
            self._close_unresolved_handoffs(status)
        if self.artifacts:
            event_type = "run_failed_quality_gate" if mark_failed else "run_complete"
            self.artifacts.append_event(event_type, {"status": status})
            self.artifacts.append_progress(status)
