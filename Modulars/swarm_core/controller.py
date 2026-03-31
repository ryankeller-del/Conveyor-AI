from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from queue import Empty, Queue
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, Optional

from .artifacts import ArtifactStore
from .bots import JudgeBot, SimpleAgent, TestBot
from .compaction import DistillationLoop
from .efficiency import EfficiencyAnalyzer
from .failure_memory import FailureMemory
from .hallucination_guard import HallucinationGuard
from .local_runtime import AgentMemoryManager, GenerationMemoryArchive, LocalCallGovernor
from .prompt_guard import PromptGuard
from .preflight import PrepBundle, SwarmPreflightManager
from .rehearsal import OfflineRehearsalManager, score_stage_state, stage_manifest_from_snapshot
from .local_models import build_desktop_local_routes, desktop_ollama_api_root
from .rosetta import RosettaStone
from .standard_tests import StandardTestLibrary
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
    StageManifest,
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
        chat_agent: Optional[SimpleAgent] = None,
        context_guard_agent: Optional[SimpleAgent] = None,
        pattern_agent: Optional[SimpleAgent] = None,
        compression_agent: Optional[SimpleAgent] = None,
        novelty_agent: Optional[SimpleAgent] = None,
        stability_guard_agent: Optional[SimpleAgent] = None,
        seed_prep_agent: Optional[SimpleAgent] = None,
        directive_prep_agent: Optional[SimpleAgent] = None,
        stability_prep_agent: Optional[SimpleAgent] = None,
    ):
        self.root_dir = root_dir
        self.prompt_guard = PromptGuard(guard_agent=context_guard_agent)
        self.rosetta = RosettaStone()
        self.skill_evolution = SkillEvolutionManager()
        self.test_bot = TestBot(test_agent, prompt_guard=self.prompt_guard)
        self.coder_agent = coder_agent
        self.judge_bot = JudgeBot(judge_agent)
        self.chat_agent = chat_agent or coder_agent

        self.registry = AgentRegistry()
        for name in ["TestRefinerBot", "PerfBot", "SecurityBot", "RefactorBot"]:
            self.registry.register(AgentDescriptor(name=name, role="specialist"))
        self.spawn_manager = SpawnManager(self.registry)
        self.team_comparator = TeamComparator()
        self.brainstorm_engine = BrainstormEngine()

        self.efficiency = EfficiencyAnalyzer()
        self.failure_memory = FailureMemory(os.path.join(self.root_dir, "swarm_learning"))
        self.agent_memory = AgentMemoryManager(
            os.path.join(self.root_dir, "swarm_learning", "agent_memory"),
            max_packet_chars=1800,
        )
        self.generation_memory = GenerationMemoryArchive(
            os.path.join(self.root_dir, "swarm_learning", "generation_memory"),
        )
        self.agent_memory.attach_generation_archive(self.generation_memory)
        self.standard_tests = StandardTestLibrary()
        self.local_governor = LocalCallGovernor()
        self.local_model_host = desktop_ollama_api_root()
        self.local_model_routes = build_desktop_local_routes()
        self.hallucination_guard = HallucinationGuard(self.root_dir)
        self.distillation = DistillationLoop(
            pattern_agent=pattern_agent,
            compression_agent=compression_agent,
            novelty_agent=novelty_agent,
        )
        self.stability_guard = StabilityGuard(stability_guard_agent)
        self.preflight = SwarmPreflightManager(
            root_dir=self.root_dir,
            seed_agent=seed_prep_agent,
            directive_agent=directive_prep_agent,
            stability_agent=stability_prep_agent,
        )
        self.rehearsal = OfflineRehearsalManager(self.root_dir)
        for agent in [
            self.test_bot.agent,
            self.coder_agent,
            self.judge_bot.agent,
            self.chat_agent,
            self.prompt_guard.guard_agent,
            self.distillation.pattern_agent,
            self.distillation.compression_agent,
            self.distillation.novelty_agent,
            self.stability_guard.agent,
            self.preflight.seed_agent,
            self.preflight.directive_agent,
            self.preflight.stability_agent,
        ]:
            if agent is not None:
                try:
                    agent.message_sink = self._record_swarm_message
                except Exception:
                    pass
        self._lock = threading.RLock()
        self._rehearsal_lock = threading.RLock()
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
        self.latest_local_memory_note = ""
        self.latest_local_memory_agent = ""
        self.latest_local_memory_task_family = ""
        self.generation_memory_records = 0
        self.generation_memory_restores = 0
        self.generation_memory_latest_generation_id = ""
        self.generation_memory_latest_aspiration = ""
        self.generation_memory_latest_note = ""
        self.generation_memory_path = ""
        self.latest_local_memory_pressure = 0.0
        self.latest_local_memory_compaction_reason = ""
        self.local_memory_pressure = 0.0
        self.local_memory_compaction_triggered = False
        self.latest_local_model_name = ""
        self.latest_local_model_lane = ""
        self.local_api_inflight = 0
        self.local_api_throttle_hits = 0
        self.returned_failure_streak = 0
        self.standard_test_fallback_count = 0
        self.latest_standard_test_reason = ""
        self.latest_standard_test_pack = ""
        self.latest_specialist_profiles: List[Dict[str, object]] = []
        self.chat_mode = "chat"
        self.chat_turn_count = 0
        self.latest_architect_instruction = ""
        self.queued_architect_briefs: List[str] = []
        self._swarm_feed: List[Dict[str, str]] = []
        self._background_run_queue: "Queue[Dict[str, object]]" = Queue()
        self._background_worker_started = False
        self._background_worker_thread: Optional[threading.Thread] = None
        self.background_run_queue_depth = 0
        self.background_run_active_goal = ""
        self.background_run_last_run_id = ""
        self.background_run_last_status = ""
        self._filesystem_queue: "Queue[Dict[str, object]]" = Queue()
        self._filesystem_worker_started = False
        self._filesystem_worker_thread: Optional[threading.Thread] = None
        self.filesystem_queue_depth = 0
        self.filesystem_active_target = ""
        self.filesystem_last_path = ""
        self.filesystem_last_status = ""
        self.filesystem_last_result = ""
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
        self.preflight_goal: Optional[TaskGoal] = None
        self.preflight_config: Optional[RunConfig] = None
        self.active_run_config: Optional[RunConfig] = None
        self.preflight_bundle: Optional[PrepBundle] = None
        self.artifacts: Optional[ArtifactStore] = None
        self.live_stage_manifest = None
        self.latest_rehearsal = None
        self.rehearsal_state = "IDLE"
        self.rehearsal_profile = ""
        self.rehearsal_report_path = ""
        self.rehearsal_manifest_path = ""
        self.rehearsal_trace_path = ""

    def _wave_name(self, idx: int) -> str:
        return ["BASELINE", "ROBUSTNESS", "MUTATION_REGRESSION"][min(idx, 2)]

    def start(self, goal: TaskGoal, config: Optional[RunConfig] = None) -> str:
        with self._lock:
            if self.snapshot.state == RunState.RUNNING:
                raise RuntimeError("Run already active")

            run_config = config or RunConfig()
            if run_config.preflight_enabled and self._needs_preflight(goal):
                self.preflight_goal = goal
                self.preflight_config = RunConfig(**asdict(run_config))
                self.preflight_bundle = self.preflight.build_bundle(goal, run_config)
                self.last_status = "Preflight bundle prepared"
            self.active_run_config = RunConfig(**asdict(run_config))
            self.run_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
            self.artifacts = ArtifactStore(run_config.artifacts_dir, self.run_id)
            previous_generation = str(self.generation_memory.status().get("latest_generation_id", ""))
            self.generation_memory.begin_generation(
                generation_id=self.run_id,
                aspiration_prompt=goal.prompt,
                source_generation_id=previous_generation,
            )
            queued_briefs = list(self.queued_architect_briefs)
            self.queued_architect_briefs = []
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
            self.live_stage_manifest = stage_manifest_from_snapshot(
                self.snapshot,
                run_config,
                current_stage=self.snapshot.wave_name,
                next_stage=self._wave_name(1),
                source="live",
                profile="live",
                note="Initial live stage manifest",
            )
            self.active_compaction_interval = max(1, run_config.compaction_interval_waves)
            self.recent_metrics = []
            self.unfinished_features = queued_briefs[:]
            self.current_focus = (
                queued_briefs[0] if queued_briefs else goal.prompt[: run_config.max_problem_scope_chars]
            )
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
            self.chat_mode = "chat"
            self.chat_turn_count = 0
            self.latest_architect_instruction = ""
            self.latest_local_memory_note = ""
            self.latest_local_memory_agent = ""
            self.latest_local_memory_task_family = ""
            self.latest_local_memory_pressure = 0.0
            self.latest_local_memory_compaction_reason = ""
            self.local_memory_pressure = 0.0
            self.local_memory_compaction_triggered = False
            self.latest_local_model_name = ""
            self.latest_local_model_lane = ""
            self.local_api_inflight = 0
            self.local_api_throttle_hits = 0
            self.returned_failure_streak = 0
            self.standard_test_fallback_count = 0
            self.latest_standard_test_reason = ""
            self.latest_standard_test_pack = ""
            self.latest_specialist_profiles = []
            self.generation_memory_records = 0
            self.generation_memory_restores = 0
            self.generation_memory_latest_generation_id = ""
            self.generation_memory_latest_aspiration = ""
            self.generation_memory_latest_note = ""
            self.generation_memory_path = ""
            self.latest_rehearsal = None
            self.rehearsal_state = "IDLE"
            self.rehearsal_profile = ""
            self.rehearsal_report_path = ""
            self.rehearsal_manifest_path = ""
            self.rehearsal_trace_path = ""
            self._save_state()
            self._pause_event.set()
            self.artifacts.append_progress("Run started")
        self.artifacts.append_event("run_started", {"goal": asdict(goal), "config": asdict(run_config)})
        self._record_swarm_feed(
            "run",
            "Run started",
            f"Goal: {goal.prompt[:240]} | Target files: {', '.join(goal.target_files)}",
        )

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

    def prepare_run(self, goal: TaskGoal, config: Optional[RunConfig] = None) -> Dict:
        with self._lock:
            if self.snapshot.state == RunState.RUNNING:
                raise RuntimeError("Cannot prepare a new run while another run is active")

            run_config = config or RunConfig()
            self.preflight_goal = goal
            self.preflight_config = RunConfig(**asdict(run_config))
            self.preflight_bundle = self.preflight.build_bundle(goal, run_config)
            self.snapshot.state = RunState.PREPARING
            self.snapshot.phase = ControllerPhase.PREPARING
            self.last_status = "Preflight bundle prepared"
            self._save_state()
            self._refresh_user_guidance(run_config)

        if self.artifacts:
            self.artifacts.append_event(
                "preflight_prepared",
                {
                    "bundle_id": self.preflight_bundle.bundle_id if self.preflight_bundle else "",
                    "goal": asdict(goal),
                    "requested_tools": self.preflight_bundle.requested_tools if self.preflight_bundle else [],
                    "requested_updates": self.preflight_bundle.requested_updates if self.preflight_bundle else [],
                },
            )
            self.artifacts.append_progress(
                f"Preflight prepared: {self.preflight_bundle.bundle_id if self.preflight_bundle else 'n/a'}"
            )
        return self.status()

    def review_preflight(self, target: str, decision: str, note: str = "") -> Dict:
        with self._lock:
            if not self.preflight_bundle:
                raise RuntimeError("No preflight bundle is available")
            bundle = self.preflight.review_proposal(
                bundle_id=self.preflight_bundle.bundle_id,
                target=target,
                decision=decision,
                note=note,
            )
            self.preflight_bundle = bundle
            if bundle.ready_to_launch:
                self.last_status = "Preflight bundle approved and ready to launch"
            else:
                self.last_status = f"Preflight review updated: {bundle.status}"
            self._save_state()
            self._refresh_user_guidance(self.preflight_config or RunConfig())

        if self.artifacts:
            self.artifacts.append_event(
                "preflight_review",
                {
                    "bundle_id": self.preflight_bundle.bundle_id if self.preflight_bundle else "",
                    "target": target,
                    "decision": decision,
                    "note": note[:400],
                },
            )
            self.artifacts.append_progress(
                f"Preflight review {decision.upper()} for {target}"
            )
        return self.status()

    def launch_prepared_run(self) -> str:
        with self._lock:
            if not self.preflight_goal or not self.preflight_config or not self.preflight_bundle:
                raise RuntimeError("No prepared run is available")
            goal = self.preflight_goal
            config = self._merge_preflight_config()

        return self.start(goal, config)

    def queue_filesystem_creation(
        self,
        folder_name: str,
        files: List[Dict[str, str]],
        scope: str = "repo_root",
        source: str = "chat",
        note: str = "",
    ) -> str:
        clean_folder = (folder_name or "").strip()
        if not clean_folder:
            raise ValueError("folder_name is required")
        payload = {
            "folder_name": clean_folder[:120],
            "files": [
                {
                    "name": str(item.get("name", "")).strip() or "hello.js",
                    "content": str(item.get("content", "")),
                }
                for item in (files or [])
            ],
            "scope": (scope or "repo_root").strip().lower(),
            "source": source,
            "note": note[:220],
            "queued_at": time.time(),
            "request_id": uuid.uuid4().hex[:12],
        }
        if not payload["files"]:
            payload["files"] = [{"name": "hello.js", "content": 'const helloWorld = "Hello, world!";\n'}]

        with self._lock:
            self._filesystem_queue.put(payload)
            self._ensure_filesystem_worker_locked()
            self.filesystem_queue_depth = self._filesystem_queue.qsize()
            self.filesystem_active_target = clean_folder[:180]
            self.filesystem_last_status = f"queued from {source}"
            self.snapshot.filesystem_queue_depth = self.filesystem_queue_depth
            self.snapshot.filesystem_active_target = self.filesystem_active_target
            self.snapshot.filesystem_last_path = self.filesystem_last_path
            self.snapshot.filesystem_last_status = self.filesystem_last_status
            self.snapshot.filesystem_last_result = self.filesystem_last_result
            self._save_state()

        if self.artifacts:
            self.artifacts.append_event(
                "filesystem_task_queued",
                {
                    "request_id": payload["request_id"],
                    "source": source,
                    "scope": payload["scope"],
                    "folder_name": payload["folder_name"],
                    "files": [item["name"] for item in payload["files"]],
                    "queue_depth": self._filesystem_queue.qsize(),
                    "note": note,
                },
            )
            self.artifacts.append_progress(
                f"Filesystem task queued from {source}: {clean_folder}"
            )
        self._record_swarm_feed(
            "filesystem",
            "Filesystem task queued",
            f"{source}: create {clean_folder} ({payload['scope']}) with {len(payload['files'])} file(s).",
        )
        return str(payload["request_id"])

    def queue_background_run(
        self,
        goal: TaskGoal,
        config: Optional[RunConfig] = None,
        source: str = "chat",
    ) -> str:
        payload = {
            "goal": TaskGoal(
                prompt=goal.prompt,
                target_files=list(goal.target_files),
                language=goal.language,
            ),
            "config": RunConfig(
                **asdict(config or self.active_run_config or self.preflight_config or RunConfig())
            ),
            "source": source,
            "queued_at": time.time(),
            "request_id": uuid.uuid4().hex[:12],
        }
        with self._lock:
            self._background_run_queue.put(payload)
            self._ensure_background_worker_locked()
            self.background_run_queue_depth = self._background_run_queue.qsize()
            self.background_run_last_status = f"queued from {source}"
            self.snapshot.background_run_queue_depth = self.background_run_queue_depth
            self.snapshot.background_run_active_goal = self.background_run_active_goal
            self.snapshot.background_run_last_run_id = self.background_run_last_run_id
            self.snapshot.background_run_last_status = self.background_run_last_status
            self._save_state()
        if self.artifacts:
            self.artifacts.append_event(
                "background_run_queued",
                {
                    "request_id": payload["request_id"],
                    "source": source,
                    "goal": asdict(payload["goal"]),
                    "queue_depth": self._background_run_queue.qsize(),
                },
            )
            self.artifacts.append_progress(
                f"Background run queued from {source}: {goal.prompt[:140]}"
            )
        self._record_swarm_feed(
            "background",
            "Background run queued",
            f"{source}: {goal.prompt[:200]}",
        )
        return str(payload["request_id"])

    def _ensure_background_worker_locked(self) -> None:
        if self._background_worker_started:
            return
        self._background_worker_started = True
        self._background_worker_thread = threading.Thread(
            target=self._background_run_worker,
            name="swarm-background-launcher",
            daemon=True,
        )
        self._background_worker_thread.start()

    def _ensure_filesystem_worker_locked(self) -> None:
        if self._filesystem_worker_started:
            return
        self._filesystem_worker_started = True
        self._filesystem_worker_thread = threading.Thread(
            target=self._filesystem_worker,
            name="swarm-filesystem-launcher",
            daemon=True,
        )
        self._filesystem_worker_thread.start()

    def _background_run_worker(self) -> None:
        while True:
            try:
                item = self._background_run_queue.get(timeout=0.2)
            except Empty:
                with self._lock:
                    self.background_run_queue_depth = self._background_run_queue.qsize()
                    self.snapshot.background_run_queue_depth = self.background_run_queue_depth
                    self._save_state()
                continue

            goal = item["goal"]
            config = item["config"]
            source = str(item.get("source", "chat"))
            request_id = str(item.get("request_id", ""))

            while True:
                with self._lock:
                    active_state = self.snapshot.state
                if active_state not in {
                    RunState.RUNNING,
                    RunState.PREPARING,
                    RunState.PAUSED,
                    RunState.STOPPING,
                }:
                    break
                time.sleep(0.2)

            with self._lock:
                self.background_run_active_goal = getattr(goal, "prompt", "")[:180]
                self.background_run_last_status = f"launching from {source}"
                self.background_run_queue_depth = self._background_run_queue.qsize()
                self.snapshot.background_run_active_goal = self.background_run_active_goal
                self.snapshot.background_run_last_run_id = self.background_run_last_run_id
                self.snapshot.background_run_last_status = self.background_run_last_status
                self.snapshot.background_run_queue_depth = self.background_run_queue_depth
                self._save_state()

            run_id = ""
            try:
                run_id = self.start(goal, config)
            except Exception as exc:
                with self._lock:
                    self.background_run_active_goal = ""
                    self.background_run_last_status = f"failed to launch: {exc}"
                    self.background_run_queue_depth = self._background_run_queue.qsize()
                    self.snapshot.background_run_active_goal = self.background_run_active_goal
                    self.snapshot.background_run_last_run_id = self.background_run_last_run_id
                    self.snapshot.background_run_last_status = self.background_run_last_status
                    self.snapshot.background_run_queue_depth = self.background_run_queue_depth
                    self._save_state()
                if self.artifacts:
                    self.artifacts.append_event(
                        "background_run_failed",
                        {
                            "request_id": request_id,
                            "source": source,
                            "goal": asdict(goal),
                            "error": str(exc),
                        },
                    )
                continue

            with self._lock:
                self.background_run_last_run_id = run_id
                self.background_run_last_status = f"started {run_id}"
                self.background_run_queue_depth = self._background_run_queue.qsize()
                self.snapshot.background_run_active_goal = self.background_run_active_goal
                self.snapshot.background_run_last_run_id = self.background_run_last_run_id
                self.snapshot.background_run_last_status = self.background_run_last_status
                self.snapshot.background_run_queue_depth = self.background_run_queue_depth
                self._save_state()
            if self.artifacts:
                self.artifacts.append_event(
                    "background_run_started",
                    {
                        "request_id": request_id,
                        "source": source,
                        "goal": asdict(goal),
                        "run_id": run_id,
                    },
                )

            while True:
                with self._lock:
                    active_state = self.snapshot.state
                    current_state = self.snapshot.state.value
                if active_state not in {
                    RunState.RUNNING,
                    RunState.PREPARING,
                    RunState.PAUSED,
                    RunState.STOPPING,
                }:
                    break
                time.sleep(0.25)

            with self._lock:
                self.background_run_active_goal = ""
                self.background_run_last_status = f"completed with {current_state}"
                self.background_run_queue_depth = self._background_run_queue.qsize()
                self.snapshot.background_run_active_goal = self.background_run_active_goal
                self.snapshot.background_run_last_run_id = self.background_run_last_run_id
                self.snapshot.background_run_last_status = self.background_run_last_status
                self.snapshot.background_run_queue_depth = self.background_run_queue_depth
                self._save_state()
            if self.artifacts:
                self.artifacts.append_event(
                    "background_run_completed",
                    {
                        "request_id": request_id,
                        "source": source,
                        "goal": asdict(goal),
                        "run_id": run_id,
                        "state": current_state,
                    },
                )

    def _filesystem_worker(self) -> None:
        while True:
            try:
                item = self._filesystem_queue.get(timeout=0.2)
            except Empty:
                with self._lock:
                    self.filesystem_queue_depth = self._filesystem_queue.qsize()
                    self.snapshot.filesystem_queue_depth = self.filesystem_queue_depth
                    self._save_state()
                continue

            folder_name = str(item.get("folder_name", "")).strip()
            files = list(item.get("files", []))
            scope = str(item.get("scope", "repo_root")).strip().lower()
            source = str(item.get("source", "chat"))
            request_id = str(item.get("request_id", ""))
            base_dir = self._filesystem_scope_base_dir(scope)
            folder_path = os.path.join(base_dir, folder_name)
            created_paths: List[str] = []
            result = "created"

            with self._lock:
                self.filesystem_active_target = folder_name[:180]
                self.filesystem_last_status = f"running from {source}"
                self.snapshot.filesystem_active_target = self.filesystem_active_target
                self.snapshot.filesystem_last_status = self.filesystem_last_status
                self.snapshot.filesystem_queue_depth = self._filesystem_queue.qsize()
                self._save_state()

            try:
                os.makedirs(folder_path, exist_ok=True)
                for file_item in files:
                    file_name = str(file_item.get("name", "")).strip() or "hello.js"
                    content = str(file_item.get("content", ""))
                    target_path = os.path.join(folder_path, file_name)
                    self._write_file(target_path, content)
                    created_paths.append(target_path)
                if not created_paths:
                    created_paths.append(folder_path)
                result = "created folder and files"
            except Exception as exc:
                result = f"failed: {exc}"
                if self.artifacts:
                    self.artifacts.append_event(
                        "filesystem_task_failed",
                        {
                            "request_id": request_id,
                            "source": source,
                            "folder_name": folder_name,
                            "scope": scope,
                            "error": str(exc),
                        },
                    )
            finally:
                with self._lock:
                    self.filesystem_queue_depth = self._filesystem_queue.qsize()
                    self.filesystem_active_target = ""
                    self.filesystem_last_path = created_paths[-1] if created_paths else folder_path
                    self.filesystem_last_status = result
                    self.filesystem_last_result = ", ".join(created_paths) if created_paths else result
                    self.snapshot.filesystem_queue_depth = self.filesystem_queue_depth
                    self.snapshot.filesystem_active_target = self.filesystem_active_target
                    self.snapshot.filesystem_last_path = self.filesystem_last_path
                    self.snapshot.filesystem_last_status = self.filesystem_last_status
                    self.snapshot.filesystem_last_result = self.filesystem_last_result
                    self._save_state()
                if self.artifacts:
                    self.artifacts.append_event(
                        "filesystem_task_completed",
                        {
                            "request_id": request_id,
                            "source": source,
                            "folder_name": folder_name,
                            "scope": scope,
                            "paths": created_paths,
                            "result": result,
                        },
                    )

    def _filesystem_scope_base_dir(self, scope: str) -> str:
        normalized = (scope or "repo_root").strip().lower()
        if normalized in {"repo_root", "above_modulars", "parent", "workspace_root"}:
            return os.path.abspath(os.path.join(self.root_dir, os.pardir))
        return self.root_dir

    def status(self) -> Dict:
        with self._lock:
            payload = asdict(self.snapshot)
            payload["state"] = self.snapshot.state.value
            payload["phase"] = self.snapshot.phase.value
            payload["last_status"] = self.last_status
            payload["prep_ready_to_launch"] = self._preflight_is_ready()
            payload["prep_goal"] = self.preflight_goal.prompt if self.preflight_goal else ""
            payload["prep_proposals"] = self._preflight_status_rows()
            payload["prep_requested_tools"] = (
                list(self.preflight_bundle.requested_tools) if self.preflight_bundle else []
            )
            payload["prep_requested_updates"] = (
                list(self.preflight_bundle.requested_updates) if self.preflight_bundle else []
            )
            payload["prep_required_testing_tools"] = (
                list(getattr(self.preflight, "_required_testing_tools", []))
                if self.preflight_bundle
                else []
            )
            payload["prep_required_reporting_tools"] = (
                list(getattr(self.preflight, "_required_reporting_tools", []))
                if self.preflight_bundle
                else []
            )
            payload["prep_required_diagnostics_tools"] = (
                list(getattr(self.preflight, "_required_diagnostics_tools", []))
                if self.preflight_bundle
                else []
            )
            payload["stage_manifest_id"] = (
                getattr(self.live_stage_manifest, "manifest_id", "")
                if self.live_stage_manifest
                else ""
            )
            payload["stage_manifest_source"] = (
                getattr(self.live_stage_manifest, "source", "")
                if self.live_stage_manifest
                else ""
            )
            payload["stage_manifest_profile"] = (
                getattr(self.live_stage_manifest, "profile", "")
                if self.live_stage_manifest
                else ""
            )
            payload["stage_manifest_current"] = (
                getattr(self.live_stage_manifest, "current_stage", "")
                if self.live_stage_manifest
                else ""
            )
            payload["stage_manifest_next"] = (
                getattr(self.live_stage_manifest, "next_stage", "")
                if self.live_stage_manifest
                else ""
            )
            payload["stage_manifest_score"] = (
                getattr(self.live_stage_manifest, "score", 0.0)
                if self.live_stage_manifest
                else 0.0
            )
            payload["stage_manifest_note"] = (
                getattr(self.live_stage_manifest, "note", "")
                if self.live_stage_manifest
                else ""
            )
            payload["stage_manifest_preload_bundle"] = (
                list(getattr(self.live_stage_manifest, "preload_bundle", []))
                if self.live_stage_manifest
                else []
            )
            payload["stage_manifest_required_tools"] = (
                list(getattr(self.live_stage_manifest, "required_tools", []))
                if self.live_stage_manifest
                else []
            )
            payload["stage_manifest_report_checklist"] = (
                list(getattr(self.live_stage_manifest, "report_checklist", []))
                if self.live_stage_manifest
                else []
            )
            payload["rehearsal_id"] = self.snapshot.rehearsal_id
            payload["rehearsal_state"] = self.rehearsal_state
            payload["rehearsal_profile"] = self.rehearsal_profile
            payload["rehearsal_report_path"] = self.rehearsal_report_path
            payload["rehearsal_manifest_path"] = self.rehearsal_manifest_path
            payload["rehearsal_trace_path"] = self.rehearsal_trace_path
            memory_status = self.agent_memory.status()
            payload["local_memory_packet_count"] = memory_status.get("packet_count", 0)
            payload["local_memory_reuse_count"] = memory_status.get("reuse_count", 0)
            payload["local_memory_invalidations"] = memory_status.get("invalidations", 0)
            payload["latest_local_memory_note"] = memory_status.get("latest_note", "")
            payload["latest_local_memory_agent"] = memory_status.get("latest_agent", "")
            payload["latest_local_memory_task_family"] = memory_status.get(
                "latest_task_family", ""
            )
            payload["latest_local_memory_pressure"] = memory_status.get("latest_pressure", 0.0)
            payload["latest_local_memory_compaction_reason"] = memory_status.get(
                "latest_compaction_reason", ""
            )
            payload["local_memory_pressure"] = memory_status.get("latest_pressure", 0.0)
            payload["local_memory_compaction_triggered"] = memory_status.get(
                "compaction_triggered", False
            )
            generation_status = self.generation_memory.status()
            payload["generation_memory_records"] = generation_status.get("record_count", 0)
            payload["generation_memory_restores"] = generation_status.get("restore_count", 0)
            payload["generation_memory_latest_generation_id"] = generation_status.get(
                "latest_generation_id", ""
            )
            payload["generation_memory_latest_aspiration"] = generation_status.get(
                "latest_aspiration", ""
            )
            payload["generation_memory_latest_note"] = generation_status.get("latest_note", "")
            payload["generation_memory_path"] = generation_status.get("base_dir", "")
            profile_limit = (self.active_run_config or self.preflight_config or RunConfig()).specialist_profile_limit
            payload["specialist_profiles"] = self.agent_memory.specialist_profiles(limit=profile_limit)
            payload["returned_failure_streak"] = self.returned_failure_streak
            payload["standard_test_fallback_count"] = self.standard_test_fallback_count
            payload["latest_standard_test_reason"] = self.latest_standard_test_reason
            payload["latest_standard_test_pack"] = self.latest_standard_test_pack
            governor_status = self.local_governor.status()
            payload["local_api_inflight"] = governor_status.get("inflight", 0)
            payload["local_api_throttle_hits"] = governor_status.get("throttle_hits", 0)
            payload["local_api_user_waiting"] = governor_status.get("user_waiting", 0)
            payload["local_api_swarm_waiting"] = governor_status.get("swarm_waiting", 0)
            payload["local_api_last_lane"] = governor_status.get("last_lane", "swarm")
            payload["local_model_host"] = self.local_model_host
            payload["local_model_routes"] = self.local_model_routes
            payload["latest_local_model_name"] = self.latest_local_model_name
            payload["latest_local_model_lane"] = self.latest_local_model_lane
            payload["chat_mode"] = self.chat_mode
            payload["chat_turn_count"] = self.chat_turn_count
            payload["queued_architect_instruction_count"] = len(self.queued_architect_briefs)
            payload["latest_architect_instruction"] = self.latest_architect_instruction
            payload["background_run_queue_depth"] = self._background_run_queue.qsize()
            payload["background_run_active_goal"] = self.background_run_active_goal
            payload["background_run_last_run_id"] = self.background_run_last_run_id
            payload["background_run_last_status"] = self.background_run_last_status
            payload["filesystem_queue_depth"] = self._filesystem_queue.qsize()
            payload["filesystem_active_target"] = self.filesystem_active_target
            payload["filesystem_last_path"] = self.filesystem_last_path
            payload["filesystem_last_status"] = self.filesystem_last_status
            payload["filesystem_last_result"] = self.filesystem_last_result
            return payload

    def recent_swarm_narrative(self, limit: int = 80) -> List[Dict[str, str]]:
        entries: List[Dict[str, str]] = []
        with self._lock:
            feed_entries = list(self._swarm_feed[-limit:])
        for item in feed_entries:
            entries.append(
                {
                    "timestamp": item.get("timestamp", ""),
                    "kind": item.get("kind", "event"),
                    "headline": item.get("headline", "event"),
                    "text": item.get("text", ""),
                }
            )
        if not self.artifacts:
            return entries[-limit:]

        if os.path.exists(self.artifacts.events_path):
            try:
                with open(self.artifacts.events_path, "r", encoding="utf-8") as handle:
                    rows = [line.strip() for line in handle if line.strip()]
                for line in rows[-limit:]:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    event_type = str(row.get("event_type", "event"))
                    payload = row.get("payload") or {}
                    timestamp = str(row.get("timestamp", ""))
                    text = self._format_swarm_event(event_type, payload)
                    if text:
                        entries.append(
                            {
                                "timestamp": timestamp,
                                "kind": "event",
                                "headline": event_type,
                                "text": text,
                            }
                        )
            except Exception:
                pass

        if os.path.exists(self.artifacts.progress_path):
            try:
                with open(self.artifacts.progress_path, "r", encoding="utf-8") as handle:
                    rows = [line.rstrip("\n") for line in handle if line.strip()]
                for line in rows[-limit:]:
                    timestamp = ""
                    text = line
                    match = re.match(r"^-\s+\[(.*?)\]\s+(.*)$", line)
                    if match:
                        timestamp = match.group(1)
                        text = match.group(2)
                    entries.append(
                        {
                            "timestamp": timestamp,
                            "kind": "progress",
                            "headline": "progress",
                            "text": text,
                        }
                    )
            except Exception:
                pass

        entries = [entry for entry in entries if entry.get("text")]
        entries.sort(key=lambda item: item.get("timestamp", ""))
        return entries[-limit:]

    def _record_swarm_feed(self, kind: str, headline: str, text: str) -> None:
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "kind": kind,
            "headline": headline[:120],
            "text": text[:1000],
        }
        with self._lock:
            self._swarm_feed.append(entry)
            self._swarm_feed = self._swarm_feed[-200:]

    def _record_swarm_message(
        self,
        agent_name: str,
        model: str,
        lane: str,
        prompt: str,
        content: str,
    ) -> None:
        prompt_preview = (prompt or "").strip().replace("\n", " ")
        prompt_preview = re.sub(r"\s+", " ", prompt_preview)[:180]
        content_text = (content or "").strip()
        if not content_text:
            return
        headline = f"{agent_name} via {model} ({lane})"
        text = f"{prompt_preview}\n\n{content_text[:1200]}"
        with self._lock:
            self.latest_local_model_name = model[:160]
            self.latest_local_model_lane = lane[:80]
            self._save_state()
        self._record_swarm_feed("agent_message", headline, text)
        if self.artifacts:
            self.artifacts.append_event(
                "agent_message",
                {
                    "agent_name": agent_name,
                    "model": model,
                    "lane": lane,
                    "prompt_excerpt": prompt_preview,
                    "content_excerpt": content_text[:800],
                },
            )

    def _format_swarm_event(self, event_type: str, payload: Dict[str, object]) -> str:
        if event_type == "run_started":
            goal = payload.get("goal") or {}
            prompt = ""
            if isinstance(goal, dict):
                prompt = str(goal.get("prompt", ""))
            return f"Run started: {prompt or 'new goal received'}"
        if event_type == "run_complete":
            status = payload.get("status", "")
            return f"Run complete: {status}"
        if event_type == "run_failed_quality_gate":
            status = payload.get("status", "")
            return f"Run failed quality gate: {status}"
        if event_type == "preflight_prepared":
            bundle = payload.get("bundle_id", "")
            return f"Preflight bundle prepared: {bundle}"
        if event_type == "preflight_review":
            decision = payload.get("decision", "")
            target = payload.get("target", "")
            return f"Preflight review {str(decision).upper()} for {target}"
        if event_type == "background_run_queued":
            source = payload.get("source", "")
            goal = payload.get("goal") or {}
            prompt = ""
            if isinstance(goal, dict):
                prompt = str(goal.get("prompt", ""))
            return f"Background run queued from {source}: {prompt}"
        if event_type == "background_run_started":
            run_id = payload.get("run_id", "")
            return f"Background run started: {run_id}"
        if event_type == "background_run_completed":
            state = payload.get("state", "")
            run_id = payload.get("run_id", "")
            return f"Background run completed ({state}): {run_id}"
        if event_type == "background_run_failed":
            error = payload.get("error", "")
            return f"Background run failed: {error}"
        if event_type == "filesystem_task_queued":
            folder = payload.get("folder_name", "")
            return f"Filesystem task queued for {folder}"
        if event_type == "filesystem_task_completed":
            folder = payload.get("folder_name", "")
            result = payload.get("result", "")
            return f"Filesystem task completed for {folder}: {result}"
        if event_type == "filesystem_task_failed":
            folder = payload.get("folder_name", "")
            error = payload.get("error", "")
            return f"Filesystem task failed for {folder}: {error}"
        if event_type == "judge_result":
            passed = payload.get("passed", False)
            output = str(payload.get("output_excerpt", "")).strip()
            outcome = "passed" if passed else "failed"
            return f"Judge {outcome}: {output}"
        if event_type == "tests_generated":
            wave = payload.get("wave", "")
            generated = payload.get("generated", 0)
            approved = payload.get("approved", 0)
            return f"Wave {wave}: generated {generated} tests, approved {approved}"
        if event_type == "implementation_written":
            target_file = payload.get("target_file", "")
            return f"Implementation written to {target_file}"
        if event_type == "hallucination_check":
            confidence = payload.get("confidence", 0.0)
            alerts = payload.get("alerts", 0)
            return f"Hallucination check: confidence {confidence}, alerts {alerts}"
        if event_type == "prompt_guard_event":
            purpose = payload.get("purpose", "")
            note = payload.get("note", "")
            return f"Prompt guard {purpose}: {note}"
        if event_type == "agent_spawned":
            agent_name = payload.get("agent_name", "")
            reason = payload.get("reason", "")
            return f"Spawned {agent_name}: {reason}"
        if event_type == "stage_manifest_applied":
            manifest_id = payload.get("manifest_id", "")
            score = payload.get("score", 0.0)
            return f"Applied stage manifest {manifest_id} at score {score}"
        if event_type == "stage_manifest_rejected":
            manifest_id = payload.get("manifest_id", "")
            return f"Rejected stage manifest {manifest_id}"
        if event_type == "rehearsal_completed":
            rehearsal_id = payload.get("rehearsal_id", "")
            accepted = payload.get("accepted", False)
            return f"Rehearsal {rehearsal_id} completed, accepted={accepted}"
        if event_type == "architect_instruction_queued":
            instruction = payload.get("instruction", "")
            return f"Architect brief queued: {instruction}"
        if event_type == "failure_memory_guidance_used":
            guidance = payload.get("guidance_excerpt", "")
            return f"Failure memory guidance used: {guidance}"
        return f"{event_type}: {payload}"

    def queue_architect_instruction(self, instruction: str, source: str = "chat") -> None:
        clean = (instruction or "").strip()
        if not clean:
            return
        clean = clean[:400]
        with self._lock:
            self.queued_architect_briefs.append(clean)
            self.queued_architect_briefs = self.queued_architect_briefs[-6:]
            self.latest_architect_instruction = clean
            self.chat_mode = "architect"
            if self.snapshot.state == RunState.RUNNING:
                if all(item.lower() != clean.lower() for item in self.unfinished_features):
                    self.unfinished_features.insert(0, clean)
                self.current_focus = clean[: self.active_run_config.max_problem_scope_chars] if self.active_run_config else clean[:1200]
            self._save_state()
        if self.artifacts:
            self.artifacts.append_event(
                "architect_instruction_queued",
                {"instruction": clean, "source": source},
            )
            self.artifacts.append_progress(
                f"Queued architect instruction from {source}: {clean[:180]}"
            )
        self._record_swarm_feed(
            "architect",
            "Architect brief queued",
            f"{source}: {clean}",
        )

    def respond_to_chat(
        self,
        message_text: str,
        config: Optional[RunConfig] = None,
        mode: Optional[str] = None,
        conversation_context: str = "",
    ) -> Dict[str, str]:
        cfg = config or self.active_run_config or self.preflight_config or RunConfig()
        chat_mode = self._normalize_chat_mode(mode or "chat", message_text)
        status = self.status()
        prompt = self._build_chat_prompt(
            message_text=message_text,
            mode=chat_mode,
            status=status,
            conversation_context=conversation_context,
        )
        raw = self._safe_generate(
            agent=self.chat_agent,
            prompt=prompt,
            purpose=f"chat-{chat_mode}",
            config=cfg,
            task_family=f"chat:{chat_mode}",
        )
        parsed = self._parse_chat_response(
            raw=raw,
            mode=chat_mode,
            message_text=message_text,
            status=status,
            conversation_context=conversation_context,
        )
        reply = parsed.get("reply") or self._fallback_chat_reply(
            mode=chat_mode,
            message_text=message_text,
            status=status,
            conversation_context=conversation_context,
        )
        background_instruction = parsed.get("background_instruction", "").strip()
        swarm_health = parsed.get("swarm_health", "").strip()
        if chat_mode == "architect" and not background_instruction:
            background_instruction = (
                f"Main architect: address the user's request '{message_text[:160]}' with the "
                "smallest safe change and keep the conversation lane responsive."
            )
        if background_instruction:
            self.queue_architect_instruction(background_instruction, source="chat")
        with self._lock:
            self.chat_mode = chat_mode
            self.chat_turn_count += 1
            self._save_state()
        return {
            "mode": chat_mode,
            "reply": reply,
            "background_instruction": background_instruction,
            "swarm_health": swarm_health,
        }

    def _normalize_chat_mode(self, mode: str, message_text: str) -> str:
        clean = (mode or "chat").strip().lower()
        if clean in {"health", "architect", "chat", "recap"}:
            return clean
        text = (message_text or "").strip().lower()
        if text.startswith("/health"):
            return "health"
        if text.startswith("/architect"):
            return "architect"
        if text.startswith("/recap"):
            return "recap"
        if any(
            phrase in text
            for phrase in (
                "show me the chats",
                "show the chats",
                "what happened so far",
                "what has happened so far",
                "conversation history",
                "chat history",
                "summarize the chat",
                "catch me up",
                "recap the conversation",
            )
        ):
            return "recap"
        return "chat"

    def _build_chat_prompt(
        self,
        message_text: str,
        mode: str,
        status: Dict[str, object],
        conversation_context: str = "",
    ) -> str:
        lines = [
            "You are LocalChatBot, the user's priority local assistant for this swarm.",
            "Return valid JSON only with keys: reply, background_instruction, swarm_health, mode.",
            f"Requested mode: {mode}.",
            "Rules:",
            "- chat: answer conversationally and briefly.",
            "- health: summarize swarm health, blockers, and next useful action.",
            "- architect: give a concise reply and a compact background instruction for the main swarm architect.",
            "- recap: summarize the recent conversation visible below, using only the provided transcript. Do not say you cannot access history; you can see the Recent conversation block.",
            "Swarm status:",
            f"state={status.get('state', 'IDLE')}; phase={status.get('phase', 'PREPARING')}; ",
            f"wave={status.get('wave_name', 'BASELINE')} ({status.get('wave_index', 0)}); ",
            f"active_topology={', '.join(status.get('active_topology', []))}; ",
            f"open_handoffs={status.get('open_handoff_count', 0)}; ",
            f"guard_mode={status.get('guard_mode', 'NORMAL')}; ",
            f"hallucination_confidence={status.get('hallucination_confidence', 1.0):.3f}; ",
            f"local_api_inflight={status.get('local_api_inflight', 0)}; ",
            f"queued_architect_instructions={status.get('queued_architect_instruction_count', 0)}; ",
            f"latest_architect_instruction={status.get('latest_architect_instruction', '')}",
            f"User message: {message_text.strip()}",
        ]
        if conversation_context.strip():
            lines.extend(
                [
                    "Recent conversation:",
                    conversation_context.strip()[:2800],
                ]
            )
        return "\n".join(lines)

    def _parse_chat_response(
        self,
        raw: str,
        mode: str,
        message_text: str,
        status: Dict[str, object],
        conversation_context: str = "",
    ) -> Dict[str, str]:
        payload: Dict[str, str] = {}
        text = (raw or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.replace("json\n", "", 1).strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                payload = {
                    str(key): str(value)
                    for key, value in parsed.items()
                    if value is not None
                }
        except Exception:
            payload = {}

        reply = payload.get("reply", "").strip()
        swarm_health = payload.get("swarm_health", "").strip()
        background_instruction = payload.get("background_instruction", "").strip()
        mode_value = payload.get("mode", mode).strip() or mode
        if not reply:
            reply = self._fallback_chat_reply(
                mode=mode_value,
                message_text=message_text,
                status=status,
                conversation_context=conversation_context,
            )
        return {
            "reply": reply,
            "background_instruction": background_instruction,
            "swarm_health": swarm_health,
            "mode": mode_value,
        }

    def _fallback_chat_reply(
        self,
        mode: str,
        message_text: str,
        status: Dict[str, object],
        conversation_context: str = "",
    ) -> str:
        if mode == "health":
            return (
                f"Swarm is {status.get('state', 'IDLE')} in {status.get('phase', 'PREPARING')}. "
                f"Wave {status.get('wave_name', 'BASELINE')} has {status.get('passing_tests', 0)} passing tests."
            )
        if mode == "architect":
            return (
                "I queued a concise architect brief and kept the local chat lane open. "
                "Tell me the next change or ask for a swarm health check."
            )
        if mode == "recap":
            lines = [line.strip() for line in conversation_context.splitlines() if line.strip()]
            if lines:
                recap = "\n".join(f"- {line}" for line in lines[-12:])
                return "Here's the recent conversation I can see in this session:\n" + recap
            return "I don't see any prior chat messages in this session yet."
        return (
            "I'm here and the local lane is open. "
            "Ask for swarm health, request architect changes, or keep chatting."
        )

    def _preflight_is_ready(self) -> bool:
        return bool(self.preflight_bundle and self.preflight_bundle.ready_to_launch)

    def _needs_preflight(self, goal: TaskGoal) -> bool:
        if not self.preflight_bundle or not self.preflight_goal:
            return True
        return (
            self.preflight_goal.prompt != goal.prompt
            or list(self.preflight_goal.target_files) != list(goal.target_files)
            or self.preflight_goal.language != goal.language
        )

    def _preflight_status_rows(self) -> List[Dict[str, object]]:
        if not self.preflight_bundle:
            return []
        rows: List[Dict[str, object]] = []
        for proposal in self.preflight_bundle.proposals:
            rows.append(
                {
                    "proposal_id": proposal.proposal_id,
                    "agent_name": proposal.agent_name,
                    "goal_key": proposal.goal_key,
                    "title": proposal.title,
                    "suggested_action": proposal.suggested_action,
                    "expected_benefit": proposal.expected_benefit,
                    "risk_if_wrong": proposal.risk_if_wrong,
                    "validation_plan": proposal.validation_plan,
                    "config_overrides": proposal.config_overrides,
                    "requested_tools": proposal.requested_tools,
                    "requested_updates": proposal.requested_updates,
                    "status": proposal.status,
                    "review_note": proposal.review_note,
                    "validation_note": proposal.validation_note,
                }
            )
        return rows

    def _merge_preflight_config(self) -> RunConfig:
        if not self.preflight_config:
            return RunConfig()
        payload = asdict(self.preflight_config)
        if self.preflight_bundle and self.preflight_bundle.launch_overrides:
            payload.update(self.preflight_bundle.launch_overrides)
        return RunConfig(**payload)

    def _effective_config(self, config: RunConfig) -> RunConfig:
        if not config.stage_manifest_hot_swap_enabled:
            return config
        payload = asdict(config)
        if self.live_stage_manifest and (
            self.live_stage_manifest.runtime_overrides
            or self.live_stage_manifest.guard_overrides
        ):
            payload.update(self.live_stage_manifest.runtime_overrides)
            payload.update(self.live_stage_manifest.guard_overrides)
        return RunConfig(**payload)

    def run_rehearsal(
        self,
        profile: Optional[str] = None,
        config: Optional[RunConfig] = None,
        apply_if_better: bool = True,
    ) -> Dict[str, object]:
        with self._lock:
            live_snapshot = RunSnapshot(**asdict(self.snapshot))
            live_manifest = self.live_stage_manifest
            run_config = config or self.active_run_config or self.preflight_config or RunConfig()
            chosen_profile = (profile or run_config.rehearsal_profile or "balanced").strip().lower()
            self.rehearsal_state = "RUNNING"
            self.rehearsal_profile = chosen_profile
            self.snapshot.rehearsal_state = self.rehearsal_state
            self.snapshot.rehearsal_profile = self.rehearsal_profile
            self._save_state()

        outcome = self.rehearsal.simulate(
            snapshot=live_snapshot,
            config=run_config,
            profile=chosen_profile,
            live_manifest=live_manifest,
        )

        accepted = False
        if apply_if_better and outcome.accepted:
            accepted = self.apply_stage_manifest(outcome.manifest, outcome)

        with self._lock:
            self.latest_rehearsal = outcome
            self.rehearsal_state = "APPLIED" if accepted else "COMPLETE"
            self.rehearsal_profile = chosen_profile
            self.rehearsal_report_path = outcome.report_path
            self.rehearsal_manifest_path = outcome.manifest_path
            self.rehearsal_trace_path = outcome.trace_path
            self.snapshot.rehearsal_id = outcome.rehearsal_id
            self.snapshot.rehearsal_state = self.rehearsal_state
            self.snapshot.rehearsal_profile = self.rehearsal_profile
            self.snapshot.rehearsal_report_path = self.rehearsal_report_path
            self.snapshot.rehearsal_manifest_path = self.rehearsal_manifest_path
            self.snapshot.rehearsal_trace_path = self.rehearsal_trace_path
            self._save_state()

        if self.artifacts:
            self.artifacts.append_event(
                "rehearsal_completed",
                {
                    "rehearsal_id": outcome.rehearsal_id,
                    "profile": chosen_profile,
                    "accepted": accepted,
                    "live_score": outcome.live_score,
                    "rehearsal_score": outcome.rehearsal_score,
                    "report_path": outcome.report_path,
                    "manifest_path": outcome.manifest_path,
                },
            )
            self.artifacts.append_progress(
                f"Rehearsal {outcome.rehearsal_id} completed with accepted={accepted}."
            )

        return {
            "rehearsal_id": outcome.rehearsal_id,
            "profile": chosen_profile,
            "accepted": accepted,
            "live_score": outcome.live_score,
            "rehearsal_score": outcome.rehearsal_score,
            "report_path": outcome.report_path,
            "manifest_path": outcome.manifest_path,
            "trace_path": outcome.trace_path,
            "stage_manifest": asdict(outcome.manifest),
            "stage_timeline": outcome.stage_timeline,
            "failure_trace": outcome.failure_trace,
        }

    def apply_stage_manifest(self, manifest, rehearsal: Optional[object] = None) -> bool:
        if manifest is None:
            return False
        if isinstance(manifest, dict):
            manifest = StageManifest(**manifest)
        if not getattr(manifest, "manifest_id", ""):
            return False
        if not self.live_stage_manifest:
            current_score = 0.0
        else:
            current_score = float(getattr(self.live_stage_manifest, "score", 0.0))
        if not self.active_run_config and not self.preflight_config and not self.snapshot.run_id:
            return False

        compare_score = float(getattr(manifest, "score", 0.0))
        min_delta = 0.0
        config = self.active_run_config or self.preflight_config or RunConfig()
        if not config.stage_manifest_hot_swap_enabled:
            return False
        if isinstance(rehearsal, dict) and rehearsal.get("accepted") is False:
            return False
        min_delta = float(getattr(config, "stage_manifest_min_score_delta", 0.05))
        if compare_score <= current_score + min_delta:
            if self.artifacts:
                self.artifacts.append_event(
                    "stage_manifest_rejected",
                    {
                        "manifest_id": manifest.manifest_id,
                        "current_score": current_score,
                        "candidate_score": compare_score,
                        "minimum_delta": min_delta,
                    },
                )
            return False

        self.live_stage_manifest = manifest
        with self._lock:
            self.snapshot.stage_manifest_id = manifest.manifest_id
            self.snapshot.stage_manifest_source = manifest.source
            self.snapshot.stage_manifest_profile = manifest.profile
            self.snapshot.stage_manifest_current = manifest.current_stage
            self.snapshot.stage_manifest_next = manifest.next_stage
            self.snapshot.stage_manifest_score = manifest.score
            self.snapshot.stage_manifest_note = manifest.note[:300]
            self._save_state()

        if self.artifacts:
            self.artifacts.append_event(
                "stage_manifest_applied",
                {
                    "manifest_id": manifest.manifest_id,
                    "current_stage": manifest.current_stage,
                    "next_stage": manifest.next_stage,
                    "score": manifest.score,
                    "source": manifest.source,
                    "profile": manifest.profile,
                    "preload_bundle": manifest.preload_bundle,
                    "required_tools": manifest.required_tools,
                    "report_checklist": manifest.report_checklist,
                },
            )
            self.artifacts.append_progress(
                f"Applied stage manifest {manifest.manifest_id} at score {manifest.score:.4f}."
            )
        return True

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
        memory_status = self.agent_memory.status()
        self.snapshot.local_memory_packet_count = int(memory_status.get("packet_count", 0))
        self.snapshot.local_memory_reuse_count = int(memory_status.get("reuse_count", 0))
        self.snapshot.local_memory_invalidations = int(memory_status.get("invalidations", 0))
        self.snapshot.local_memory_pressure = float(memory_status.get("latest_pressure", 0.0))
        self.snapshot.local_memory_compaction_triggered = bool(
            memory_status.get("compaction_triggered", False)
        )
        self.snapshot.latest_local_memory_pressure = float(memory_status.get("latest_pressure", 0.0))
        self.snapshot.latest_local_memory_compaction_reason = str(
            memory_status.get("latest_compaction_reason", "")
        )[:280]
        self.snapshot.local_api_inflight = int(self.local_governor.status().get("inflight", 0))
        self.snapshot.local_api_throttle_hits = int(
            self.local_governor.status().get("throttle_hits", 0)
        )
        governor_status = self.local_governor.status()
        self.snapshot.local_api_user_waiting = int(governor_status.get("user_waiting", 0))
        self.snapshot.local_api_swarm_waiting = int(governor_status.get("swarm_waiting", 0))
        self.snapshot.local_api_last_lane = str(governor_status.get("last_lane", "swarm"))
        self.snapshot.local_model_host = self.local_model_host
        self.snapshot.local_model_routes = self.local_model_routes
        self.snapshot.latest_local_model_name = self.latest_local_model_name
        self.snapshot.latest_local_model_lane = self.latest_local_model_lane
        self.snapshot.latest_local_memory_note = str(memory_status.get("latest_note", ""))[:280]
        self.snapshot.latest_local_memory_agent = str(memory_status.get("latest_agent", ""))[:140]
        self.snapshot.latest_local_memory_task_family = str(
            memory_status.get("latest_task_family", "")
        )[:140]
        generation_status = self.generation_memory.status()
        self.snapshot.generation_memory_records = int(generation_status.get("record_count", 0))
        self.snapshot.generation_memory_restores = int(generation_status.get("restore_count", 0))
        self.snapshot.generation_memory_latest_generation_id = str(
            generation_status.get("latest_generation_id", "")
        )[:140]
        self.snapshot.generation_memory_latest_aspiration = str(
            generation_status.get("latest_aspiration", "")
        )[:280]
        self.snapshot.generation_memory_latest_note = str(generation_status.get("latest_note", ""))[:280]
        self.snapshot.generation_memory_path = str(generation_status.get("base_dir", ""))[:260]
        self.snapshot.standard_test_fallback_count = self.standard_test_fallback_count
        self.snapshot.latest_standard_test_reason = self.latest_standard_test_reason[:280]
        self.snapshot.latest_standard_test_pack = self.latest_standard_test_pack[:280]
        self.snapshot.returned_failure_streak = self.returned_failure_streak
        self.snapshot.specialist_profiles = self.agent_memory.specialist_profiles(
            limit=(self.active_run_config or self.preflight_config or RunConfig()).specialist_profile_limit
        )
        self.snapshot.chat_mode = self.chat_mode
        self.snapshot.chat_turn_count = self.chat_turn_count
        self.snapshot.queued_architect_instruction_count = len(self.queued_architect_briefs)
        self.snapshot.latest_architect_instruction = self.latest_architect_instruction[:280]
        if self.live_stage_manifest:
            self.snapshot.stage_manifest_id = self.live_stage_manifest.manifest_id
            self.snapshot.stage_manifest_source = self.live_stage_manifest.source
            self.snapshot.stage_manifest_profile = self.live_stage_manifest.profile
            self.snapshot.stage_manifest_current = self.live_stage_manifest.current_stage
            self.snapshot.stage_manifest_next = self.live_stage_manifest.next_stage
            self.snapshot.stage_manifest_score = self.live_stage_manifest.score
            self.snapshot.stage_manifest_note = self.live_stage_manifest.note[:300]
        if self.preflight_bundle:
            self.snapshot.prep_bundle_id = self.preflight_bundle.bundle_id
            self.snapshot.prep_status = self.preflight_bundle.status
            self.snapshot.prep_pending_count = sum(
                1 for item in self.preflight_bundle.proposals if item.status == "PENDING"
            )
            self.snapshot.prep_approved_count = sum(
                1 for item in self.preflight_bundle.proposals if item.status == "APPROVED"
            )
            self.snapshot.prep_denied_count = sum(
                1 for item in self.preflight_bundle.proposals if item.status == "DENIED"
            )
            self.snapshot.prep_revise_count = sum(
                1 for item in self.preflight_bundle.proposals if item.status == "REVISE"
            )
            self.snapshot.prep_last_validation = self.preflight_bundle.validation_note[:320]
        else:
            self.snapshot.prep_bundle_id = ""
            self.snapshot.prep_status = "NONE"
            self.snapshot.prep_pending_count = 0
            self.snapshot.prep_approved_count = 0
            self.snapshot.prep_denied_count = 0
            self.snapshot.prep_revise_count = 0
            self.snapshot.prep_last_validation = ""
        self.snapshot.rehearsal_id = getattr(self.latest_rehearsal, "rehearsal_id", self.snapshot.rehearsal_id)
        self.snapshot.rehearsal_state = self.rehearsal_state
        self.snapshot.rehearsal_profile = self.rehearsal_profile
        self.snapshot.rehearsal_report_path = self.rehearsal_report_path
        self.snapshot.rehearsal_manifest_path = self.rehearsal_manifest_path
        self.snapshot.rehearsal_trace_path = self.rehearsal_trace_path
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
                effective_config = self._effective_config(config)

                with self._lock:
                    self.snapshot.phase = ControllerPhase.TEST_WAVE_GEN
                    self.snapshot.wave_index = wave_idx
                    self.snapshot.wave_name = self._wave_name(wave_idx)
                    self._save_state()

                self.live_stage_manifest = stage_manifest_from_snapshot(
                    self.snapshot,
                    effective_config,
                    current_stage=self.snapshot.wave_name,
                    next_stage=self._wave_name(wave_idx + 1),
                    source="live",
                    profile="live",
                    note="Live stage refresh",
                )
                self._save_state()

                test_specs = self._generate_wave_tests(goal, effective_config, previous_tests)
                if not test_specs:
                    self._complete("No new meaningful tests generated.", config=effective_config)
                    return

                for spec in test_specs:
                    self._write_file(spec.path, spec.content)
                    self.snapshot.total_tests += self._count_tests(spec.content)
                self._apply_population_control(effective_config)

                cycle_start = time.time()
                pass_count = 0
                attempts = 0
                failure_recurrence = 0

                retry_limit = effective_config.local_retry_limit + self.ramp_level
                for attempt in range(retry_limit + 1):
                    if not self._gate_run_state():
                        return

                    attempts += 1
                    with self._lock:
                        self.snapshot.phase = ControllerPhase.IMPLEMENT
                        self._save_state()

                    generated_code = self._implement(goal, test_specs, attempt, effective_config)

                    guard_passed = True
                    if effective_config.hallucination_guard_enabled:
                        with self._lock:
                            self.snapshot.phase = ControllerPhase.HALLUCINATION_GUARD
                            self._save_state()
                        guard_passed = self._run_hallucination_guard(
                            goal=goal,
                            generated_code=generated_code,
                            config=effective_config,
                            cycle_index=wave_idx,
                        )
                        if not guard_passed:
                            failure_recurrence += 1
                            continue

                    with self._lock:
                        self.snapshot.phase = ControllerPhase.JUDGE
                        self._save_state()

                    passed, judge_output = self._judge(goal, test_specs, effective_config)
                    if passed:
                        pass_count = len(test_specs)
                        self._resolve_current_unfinished_feature()
                        self._resolve_open_handoffs("Tests passed for current wave.")
                        break

                    failure_recurrence += 1
                    self._register_unfinished_feature(judge_output)
                    if effective_config.dynamic_spawning_enabled:
                        self._spawn_for_failure(goal, judge_output, effective_config)

                    fix_list = self._generate_fix_list(judge_output, effective_config)
                    self.artifacts.append_progress(
                        f"Retry {attempt + 1}: judge suggested fixes captured"
                    )
                    if effective_config.failure_memory_enabled:
                        self._record_failure(goal, judge_output, fix_list)
                    self._register_unfinished_feature(fix_list)
                    self._pass_back_failed_handoffs(
                        failure_output=judge_output,
                        fix_list=fix_list,
                        config=effective_config,
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

                if consecutive_failed_waves >= max(1, effective_config.max_consecutive_failed_waves):
                    self._complete("Consecutive failed waves limit reached.", config=effective_config)
                    return

                if self.snapshot.total_tests >= effective_config.max_total_tests:
                    self._complete("Reached max_total_tests.", config=effective_config)
                    return

                if self.snapshot.no_gain_waves >= effective_config.max_no_gain_waves:
                    self._complete("Coverage gain plateau reached.", config=effective_config)
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
                self._update_ramp_level(effective_config, metric)
                decision = self.stability_guard.evaluate(
                    snapshot=self.snapshot,
                    metric=metric,
                    recent_metrics=self.recent_metrics,
                    config=effective_config,
                )
                if self._apply_guard_decision(decision, effective_config):
                    return
                self._evaluate_skill_evolution(metric=metric, config=effective_config)
                self._adapt_compaction_policy(effective_config, metric)
                self._refresh_user_guidance(effective_config)
                efficiency_details.append(
                    {
                        "cycle": wave_idx,
                        "topology": topology_key,
                        "pass_rate": round(pass_rate, 3),
                        "duration_seconds": round(wave_duration, 3),
                        "score": round(score, 4),
                    }
                )
                self._rotate_topology(effective_config, wave_idx)
                self._apply_population_control(effective_config)
                if effective_config.team_mode_enabled:
                    self._run_team_brainstorm(effective_config, wave_idx)
                if effective_config.memory_distillation_enabled:
                    self._run_memory_distillation(effective_config, wave_idx)

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
        standard_pack = None
        standard_reference = ""
        if config.standard_tests_enabled and self.returned_failure_streak >= max(
            1, config.standard_test_min_returned_failures
        ):
            failure_pattern = self._failure_pattern_hint()
            standard_pack = self.standard_tests.resolve(
                role="test",
                code_type=self._infer_code_type(goal.target_files[0]),
                failure_pattern=failure_pattern,
                target_file=goal.target_files[0],
            )
            standard_reference = standard_pack.render()
            self.latest_standard_test_reason = (
                f"Returned failure streak {self.returned_failure_streak} triggered fallback pack "
                f"for {standard_pack.role}/{standard_pack.code_type}/{standard_pack.failure_pattern}."
            )
            self.latest_standard_test_pack = standard_reference[:280]

        generated = self.test_bot.generate_next_wave(
            previous_results=[{"last_status": self.last_status}],
            coverage_gaps=coverage_gaps,
            wave=wave,
            target_file=goal.target_files[0],
            tests_path=tests_path,
            reference_material=standard_reference,
        )
        approved = self.judge_bot.validate_tests(generated)
        if standard_pack and standard_reference:
            generated[0].content = f"{generated[0].content}\n\n{standard_reference}"
            approved = self.judge_bot.validate_tests(generated)
            self.standard_test_fallback_count += 1
            if self.artifacts:
                self.artifacts.append_event(
                    "standard_test_pack_used",
                    {
                        "wave": wave,
                        "role": standard_pack.role,
                        "code_type": standard_pack.code_type,
                        "failure_pattern": standard_pack.failure_pattern,
                    },
                )
                self.artifacts.append_progress(
                    f"Standard test fallback pack used for {standard_pack.role}/{standard_pack.code_type}."
                )

        if self.artifacts:
            self.artifacts.append_event(
                "tests_generated",
                {
                    "wave": wave,
                    "generated": len(generated),
                    "approved": len(approved),
                    "previous_tests": previous_tests,
                    "standard_tests_used": bool(standard_pack),
                    "returned_failure_streak": self.returned_failure_streak,
                },
            )
            self.artifacts.append_progress(
                f"Wave {wave}: generated {len(generated)} tests, approved {len(approved)}"
            )

        return approved

    def _infer_code_type(self, target_file: str) -> str:
        name = (target_file or "").lower()
        if name.endswith(".py"):
            if os.path.basename(name) in {"app.py", "app_v3.py"}:
                return "flask"
            return "python"
        if name.endswith(".js"):
            return "javascript"
        if name.endswith(".ts"):
            return "typescript"
        if name.endswith(".cs"):
            return "csharp"
        return "generic"

    def _failure_pattern_hint(self) -> str:
        text = " ".join(
            [
                self.last_status,
                self.last_failure_context,
                " ".join(self.handoff_feedback_log[-2:]),
            ]
        ).lower()
        if "unknown api" in text or "unknown symbol" in text:
            return "unknown_api"
        if "assert" in text or "assertion" in text:
            return "regression"
        if "flaky" in text:
            return "flaky"
        if "boundary" in text:
            return "boundary"
        return "any"

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
        returned_count = 0
        for handoff_id, payload in list(self.open_handoffs.items()):
            if payload.get("status") != "OPEN":
                continue
            returned_count += 1
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
        if returned_count:
            self.returned_failure_streak += 1
        else:
            self.returned_failure_streak = 0
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
        self.returned_failure_streak = 0
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
        self.agent_memory.invalidate(
            agent_name=self.coder_agent.name,
            task_family="implementation",
            reason=judge_output[:240],
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
        if response and config.local_memory_enabled:
            self.agent_memory.inject_solution(
                agent_name=self.coder_agent.name,
                task_family="implementation",
                note=response,
                source_agent=self.judge_bot.agent.name,
                reason="judge fix list",
            )
        return response or failure_output[:1200]

    def _safe_generate(
        self,
        agent: SimpleAgent,
        prompt: str,
        purpose: str,
        config: RunConfig,
        task_family: Optional[str] = None,
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

        local_agent = self._is_local_agent(agent)
        family = (task_family or purpose or "general").strip().lower()
        memory_recall = None
        if local_agent and config.local_memory_enabled:
            support_notes: List[str] = []
            if self.latest_skill_event:
                support_notes.append(self.latest_skill_event)
            if self.handoff_feedback_log:
                support_notes.extend(self.handoff_feedback_log[-2:])
            if self.last_failure_context:
                support_notes.append(self.last_failure_context)
            if purpose in {"implementation", "fix-pass"} and config.failure_memory_enabled:
                guidance = self.failure_memory.format_guidance(
                    working_prompt,
                    limit=config.failure_memory_limit,
                )
                if guidance:
                    support_notes.append(guidance)
            restore_requested = purpose in {"recap", "restore", "memory"}
            task_family_restoreable = (
                purpose in {"implementation", "fix-pass", "testing", "judge", "review"}
                and (
                    bool(self.last_failure_context)
                    or self.returned_failure_streak > 0
                    or not self.agent_memory.has_active_packet(agent.name, family)
                )
            )
            should_restore_lineage = bool(
                config.generation_memory_enabled
                and config.generation_memory_restore_enabled
                and (restore_requested or task_family_restoreable)
            )
            if should_restore_lineage:
                restored = self.generation_memory.restore(
                    agent_name=agent.name,
                    task_family=family,
                    task_prompt=working_prompt,
                    failure_context=self.last_failure_context if purpose != "testing" else "",
                    max_records=config.generation_memory_restore_limit,
                    max_chars=config.local_memory_max_chars,
                )
                if restored.restored and restored.content:
                    support_notes.append(restored.content)
                    generation_status = self.generation_memory.status()
                    with self._lock:
                        self.generation_memory_records = int(generation_status.get("record_count", 0))
                        self.generation_memory_restores = int(generation_status.get("restore_count", 0))
                        self.generation_memory_latest_generation_id = str(
                            generation_status.get("latest_generation_id", "")
                        )
                        self.generation_memory_latest_aspiration = str(
                            generation_status.get("latest_aspiration", "")
                        )
                        self.generation_memory_latest_note = str(generation_status.get("latest_note", ""))
                        self.generation_memory_path = str(generation_status.get("base_dir", ""))
            memory_recall = self.agent_memory.prepare(
                agent_name=agent.name,
                task_family=family,
                task_prompt=working_prompt,
                failure_context=self.last_failure_context if purpose != "testing" else "",
                support_notes=support_notes,
                force_refresh=not config.local_memory_reuse_enabled,
                max_chars=config.local_memory_max_chars,
                pressure_threshold=config.local_memory_pressure_threshold,
            )
            if memory_recall.content:
                working_prompt = (
                    f"{working_prompt}\n\nLOCAL MEMORY PACKET ({agent.name}/{family}):\n"
                    f"{memory_recall.content}"
                )
                with self._lock:
                    self.latest_local_memory_note = memory_recall.note[:280]
                    self.latest_local_memory_agent = agent.name[:140]
                    self.latest_local_memory_task_family = family[:140]
                    self.latest_local_memory_pressure = float(memory_recall.pressure or 0.0)
                    self.latest_local_memory_compaction_reason = memory_recall.compaction_reason[:280]
                    self.local_memory_pressure = float(memory_recall.pressure or 0.0)
                    self.local_memory_compaction_triggered = bool(memory_recall.compacted)
                    self._save_state()
                if self.artifacts:
                    self.artifacts.append_event(
                        "local_memory_compacted"
                        if memory_recall.compacted
                        else "local_memory_primed"
                        if not memory_recall.reused
                        else "local_memory_reused",
                        {
                            "agent_name": agent.name,
                            "task_family": family,
                            "packet_id": memory_recall.packet_id,
                            "reused": memory_recall.reused,
                            "refreshed": memory_recall.refreshed,
                            "note": memory_recall.note,
                            "pressure": memory_recall.pressure,
                            "compacted": memory_recall.compacted,
                            "compaction_reason": memory_recall.compaction_reason,
                        },
                    )

        lease = None
        if local_agent and config.local_api_throttle_enabled:
            self.local_governor.configure(
                max_inflight=config.local_api_max_inflight,
                min_interval_seconds=config.local_api_min_interval_seconds,
                queue_limit=config.local_api_queue_limit,
                backoff_seconds=config.local_api_backoff_seconds,
            )
            lane = "user" if purpose.startswith("chat") else "swarm"
            lease = self.local_governor.acquire(agent.name, family, lane=lane)
            with self._lock:
                governor_status = self.local_governor.status()
                self.local_api_inflight = int(governor_status.get("inflight", 0))
                self.local_api_throttle_hits = int(governor_status.get("throttle_hits", 0))
                self.snapshot.local_api_user_waiting = int(governor_status.get("user_waiting", 0))
                self.snapshot.local_api_swarm_waiting = int(governor_status.get("swarm_waiting", 0))
                self.snapshot.local_api_last_lane = str(governor_status.get("last_lane", lane))
                self._save_state()
            if lease.throttled and self.artifacts:
                self.artifacts.append_event(
                    "local_api_throttled",
                    {
                        "agent_name": agent.name,
                        "task_family": family,
                        "lane": lane,
                        "waited_seconds": lease.waited_seconds,
                        "queue_limit": config.local_api_queue_limit,
                    },
                )

        response = ""
        try:
            response = agent.generate(working_prompt)
        finally:
            if lease:
                self.local_governor.release(lease)
                with self._lock:
                    governor_status = self.local_governor.status()
                    self.local_api_inflight = int(governor_status.get("inflight", 0))
                    self.local_api_throttle_hits = int(governor_status.get("throttle_hits", 0))
                    self.snapshot.local_api_user_waiting = int(governor_status.get("user_waiting", 0))
                    self.snapshot.local_api_swarm_waiting = int(governor_status.get("swarm_waiting", 0))
                    self.snapshot.local_api_last_lane = str(governor_status.get("last_lane", "swarm"))
                    self._save_state()

        final_response = response
        if response and not response.startswith("ERROR:"):
            final_response = response
        elif config.prompt_guard_retry_on_error:
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
            final_response = agent.generate(retry_prompt) or ""

        if memory_recall:
            success = bool(final_response and not final_response.startswith("ERROR:"))
            self.agent_memory.record_call(
                agent_name=agent.name,
                task_family=family,
                packet_id=memory_recall.packet_id,
                success=success,
                outcome="success" if success else "failure",
                reused=memory_recall.reused,
                note=purpose,
            )
            if not success and config.local_memory_invalidate_on_failure:
                self.agent_memory.invalidate(
                    agent_name=agent.name,
                    task_family=family,
                    reason=f"{purpose} call failed or returned empty response",
                )
        return final_response or ""

    def _is_local_agent(self, agent: SimpleAgent) -> bool:
        return bool(getattr(agent, "is_local", False))

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
            if config.local_memory_enabled:
                self.agent_memory.inject_solution(
                    agent_name=self.coder_agent.name,
                    task_family="implementation",
                    note="Replace unknown symbols/APIs with project-defined alternatives.",
                    source_agent="HallucinationGuard",
                    reason="blocked low-confidence cycle",
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

        if self.snapshot.state == RunState.PREPARING and self.preflight_bundle:
            if self.preflight_bundle.ready_to_launch:
                suggestions.append(
                    "Preflight bundle is approved. Use /launch to start the swarm."
                )
            else:
                pending = [
                    item.agent_name
                    for item in self.preflight_bundle.proposals
                    if item.status == "PENDING"
                ]
                revises = [
                    item.agent_name
                    for item in self.preflight_bundle.proposals
                    if item.status == "REVISE"
                ]
                denied = [
                    item.agent_name
                    for item in self.preflight_bundle.proposals
                    if item.status == "DENIED"
                ]
                if pending:
                    warnings.append(
                        "Preflight review pending for: " + ", ".join(pending[:5])
                    )
                if revises:
                    warnings.append(
                        "Preflight proposals need revision: " + ", ".join(revises[:5])
                    )
                if denied:
                    warnings.append(
                        "Preflight proposals were denied: " + ", ".join(denied[:5])
                    )
                suggestions.append(
                    "Approve or revise the prep proposals, then use /launch when ready."
                )
                suggestions.append(
                    "Use /approve <agent>, /deny <agent>, or /revise <agent> with a short note."
                )

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
        self._record_swarm_feed(
            "run",
            "Run finished",
            status,
        )


