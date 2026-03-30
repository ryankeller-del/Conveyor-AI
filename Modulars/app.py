import asyncio
import json
import os
import socket
from typing import Any, Dict

import chainlit as cl
from dotenv import load_dotenv
from openai import OpenAI

from bot_profiles_v3 import build_swarm_profiles
from swarm_core.chat_lane import RollingConversation, detect_chat_mode, should_launch_swarm
from swarm_core.bots import SimpleAgent
from swarm_core.controller import SwarmController
from swarm_core.types import RunConfig, TaskGoal

load_dotenv()


def _client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key)


def _is_local_llm_available(host: str = "127.0.0.1", port: int = 11434, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _build_controller() -> SwarmController:
    profiles = build_swarm_profiles()

    groq_client = _client("https://api.groq.com/openai/v1", os.getenv("GROQ_API_KEY", ""))
    openrouter_client = _client("https://openrouter.ai/api/v1", os.getenv("OPENROUTER_API_KEY", ""))
    local_client = _client("http://localhost:11434/v1", "ollama")
    local_available = _is_local_llm_available()

    test_agent = SimpleAgent(
        name=profiles["test"].name,
        model=profiles["test"].model,
        fallback_models=profiles["test"].fallback_models,
        system_prompt=profiles["test"].system_prompt,
        client=groq_client,
        is_local=False,
    )
    coder_agent = SimpleAgent(
        name=profiles["coder"].name,
        model=profiles["coder"].model,
        fallback_models=profiles["coder"].fallback_models,
        fallback_client_models=["openrouter/free"],
        system_prompt=profiles["coder"].system_prompt,
        client=local_client if local_available else None,
        fallback_client=openrouter_client,
        is_local=local_available,
    )
    chat_agent = SimpleAgent(
        name=profiles["chat"].name,
        model=profiles["chat"].model,
        fallback_models=profiles["chat"].fallback_models,
        system_prompt=profiles["chat"].system_prompt,
        client=local_client if local_available else None,
        fallback_client=openrouter_client,
        is_local=local_available,
    )
    judge_agent = SimpleAgent(
        name=profiles["judge"].name,
        model=profiles["judge"].model,
        fallback_models=profiles["judge"].fallback_models,
        system_prompt=profiles["judge"].system_prompt,
        client=local_client,
        fallback_client=openrouter_client,
        is_local=local_available,
    )
    context_guard_agent = SimpleAgent(
        name=profiles["context_guard"].name,
        model=profiles["context_guard"].model,
        fallback_models=profiles["context_guard"].fallback_models,
        system_prompt=profiles["context_guard"].system_prompt,
        client=local_client if local_available else None,
        fallback_client=openrouter_client,
        is_local=local_available,
    )
    pattern_agent = SimpleAgent(
        name=profiles["pattern_finder"].name,
        model=profiles["pattern_finder"].model,
        fallback_models=profiles["pattern_finder"].fallback_models,
        system_prompt=profiles["pattern_finder"].system_prompt,
        client=local_client if local_available else None,
        fallback_client=openrouter_client,
        is_local=local_available,
    )
    compression_agent = SimpleAgent(
        name=profiles["compression"].name,
        model=profiles["compression"].model,
        fallback_models=profiles["compression"].fallback_models,
        system_prompt=profiles["compression"].system_prompt,
        client=local_client if local_available else None,
        fallback_client=openrouter_client,
        is_local=local_available,
    )
    novelty_agent = SimpleAgent(
        name=profiles["novelty"].name,
        model=profiles["novelty"].model,
        fallback_models=profiles["novelty"].fallback_models,
        system_prompt=profiles["novelty"].system_prompt,
        client=local_client if local_available else None,
        fallback_client=openrouter_client,
        is_local=local_available,
    )
    stability_guard_agent = SimpleAgent(
        name=profiles["stability_guard"].name,
        model=profiles["stability_guard"].model,
        fallback_models=profiles["stability_guard"].fallback_models,
        system_prompt=profiles["stability_guard"].system_prompt,
        client=local_client if local_available else None,
        fallback_client=openrouter_client,
        is_local=local_available,
    )
    seed_prep_agent = SimpleAgent(
        name=profiles["seed_prep"].name,
        model=profiles["seed_prep"].model,
        fallback_models=profiles["seed_prep"].fallback_models,
        system_prompt=profiles["seed_prep"].system_prompt,
        client=local_client if local_available else None,
        fallback_client=openrouter_client,
        is_local=local_available,
    )
    directive_prep_agent = SimpleAgent(
        name=profiles["directive_prep"].name,
        model=profiles["directive_prep"].model,
        fallback_models=profiles["directive_prep"].fallback_models,
        system_prompt=profiles["directive_prep"].system_prompt,
        client=local_client if local_available else None,
        fallback_client=openrouter_client,
        is_local=local_available,
    )
    stability_prep_agent = SimpleAgent(
        name=profiles["stability_prep"].name,
        model=profiles["stability_prep"].model,
        fallback_models=profiles["stability_prep"].fallback_models,
        system_prompt=profiles["stability_prep"].system_prompt,
        client=local_client if local_available else None,
        fallback_client=openrouter_client,
        is_local=local_available,
    )

    return SwarmController(
        test_agent=test_agent,
        coder_agent=coder_agent,
        judge_agent=judge_agent,
        root_dir=os.path.dirname(os.path.abspath(__file__)),
        chat_agent=chat_agent,
        context_guard_agent=context_guard_agent,
        pattern_agent=pattern_agent,
        compression_agent=compression_agent,
        novelty_agent=novelty_agent,
        stability_guard_agent=stability_guard_agent,
        seed_prep_agent=seed_prep_agent,
        directive_prep_agent=directive_prep_agent,
        stability_prep_agent=stability_prep_agent,
    )


def _parse_goal(message_text: str) -> TaskGoal:
    return TaskGoal(prompt=message_text, target_files=["app_v3.py"], language="general")


def _parse_config(payload: Dict[str, Any] | None = None) -> RunConfig:
    payload = payload or {}
    defaults = RunConfig()
    session_overrides = cl.user_session.get("run_config_overrides") or {}
    merged = {**session_overrides, **payload}
    fields = {
        field: merged.get(field, getattr(defaults, field))
        for field in defaults.__dataclass_fields__.keys()
    }
    return RunConfig(**fields)


def _chat_history_limit() -> int:
    overrides = cl.user_session.get("run_config_overrides") or {}
    defaults = RunConfig()
    return int(overrides.get("chat_history_limit", defaults.chat_history_limit))


def _chat_transcript() -> RollingConversation:
    transcript = cl.user_session.get("chat_transcript")
    if not isinstance(transcript, RollingConversation):
        transcript = RollingConversation(limit=_chat_history_limit())
        cl.user_session.set("chat_transcript", transcript)
    else:
        transcript.limit = _chat_history_limit()
    return transcript


def _is_swarm_launch(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized.startswith("/swarm") or normalized.startswith("/runjson")


def _control_actions():
    return [
        cl.Action(name="swarm_pause", label="Pause", payload={}),
        cl.Action(name="swarm_resume", label="Resume", payload={}),
        cl.Action(name="swarm_stop", label="Stop", payload={}),
        cl.Action(name="swarm_status", label="Status", payload={}),
        cl.Action(name="runner_pytest", label="Runner: Pytest", payload={}),
        cl.Action(name="runner_dotnet", label="Runner: Dotnet", payload={}),
        cl.Action(name="runner_npm", label="Runner: NPM Test", payload={}),
        cl.Action(name="memory_default", label="Memory: Default", payload={}),
        cl.Action(name="memory_fast", label="Memory: Fast", payload={}),
        cl.Action(name="memory_deep", label="Memory: Deep", payload={}),
        cl.Action(name="memory_off", label="Memory: Off", payload={}),
        cl.Action(name="adaptive_on", label="Adaptive: On", payload={}),
        cl.Action(name="adaptive_off", label="Adaptive: Off", payload={}),
        cl.Action(name="prep_status", label="Prep: Status", payload={}),
        cl.Action(name="prep_launch", label="Prep: Launch", payload={}),
        cl.Action(name="rehearsal_run", label="Rehearsal: Run", payload={}),
    ]


async def _send_status(controller: SwarmController, prefix: str = "Swarm status"):
    status = controller.status()
    active_test_command = cl.user_session.get("active_test_command") or RunConfig().test_command
    overrides = cl.user_session.get("run_config_overrides") or {}
    distill_enabled = overrides.get("memory_distillation_enabled", RunConfig().memory_distillation_enabled)
    requested_interval = overrides.get("compaction_interval_waves", RunConfig().compaction_interval_waves)
    distill_rules = overrides.get("memory_rule_limit", RunConfig().memory_rule_limit)
    distill_crumbs = overrides.get("memory_breadcrumb_limit", RunConfig().memory_breadcrumb_limit)
    adaptive_enabled = overrides.get("adaptive_compaction_enabled", RunConfig().adaptive_compaction_enabled)
    active_interval = status.get("compaction_interval_active", requested_interval)
    artifacts = status.get("artifacts_path") or "n/a"
    suggestions = status.get("ui_suggestions") or []
    warnings = status.get("ui_warnings") or []
    prep_ready = status.get("prep_ready_to_launch", False)
    prep_goal = status.get("prep_goal") or "n/a"
    prep_bundle_id = status.get("prep_bundle_id") or "n/a"
    prep_status = status.get("prep_status") or "NONE"
    prep_proposals = status.get("prep_proposals") or []
    prep_tools = status.get("prep_requested_tools") or []
    prep_updates = status.get("prep_requested_updates") or []
    prep_testing_tools = status.get("prep_required_testing_tools") or []
    prep_reporting_tools = status.get("prep_required_reporting_tools") or []
    prep_diagnostics_tools = status.get("prep_required_diagnostics_tools") or []
    stage_manifest_current = status.get("stage_manifest_current") or "n/a"
    stage_manifest_next = status.get("stage_manifest_next") or "n/a"
    stage_manifest_score = status.get("stage_manifest_score", 0.0)
    stage_manifest_profile = status.get("stage_manifest_profile") or "n/a"
    stage_manifest_preload_bundle = status.get("stage_manifest_preload_bundle") or []
    stage_manifest_required_tools = status.get("stage_manifest_required_tools") or []
    stage_manifest_report_checklist = status.get("stage_manifest_report_checklist") or []
    rehearsal_state = status.get("rehearsal_state") or "IDLE"
    rehearsal_profile = status.get("rehearsal_profile") or "n/a"
    rehearsal_report_path = status.get("rehearsal_report_path") or "n/a"
    rehearsal_manifest_path = status.get("rehearsal_manifest_path") or "n/a"
    rehearsal_trace_path = status.get("rehearsal_trace_path") or "n/a"
    local_memory_packet_count = status.get("local_memory_packet_count", 0)
    local_memory_reuse_count = status.get("local_memory_reuse_count", 0)
    local_memory_invalidations = status.get("local_memory_invalidations", 0)
    local_api_inflight = status.get("local_api_inflight", 0)
    local_api_throttle_hits = status.get("local_api_throttle_hits", 0)
    local_api_user_waiting = status.get("local_api_user_waiting", 0)
    local_api_swarm_waiting = status.get("local_api_swarm_waiting", 0)
    local_api_last_lane = status.get("local_api_last_lane") or "n/a"
    latest_local_memory_note = status.get("latest_local_memory_note") or "n/a"
    latest_local_memory_agent = status.get("latest_local_memory_agent") or "n/a"
    latest_local_memory_task_family = status.get("latest_local_memory_task_family") or "n/a"
    returned_failure_streak = status.get("returned_failure_streak", 0)
    standard_test_fallback_count = status.get("standard_test_fallback_count", 0)
    latest_standard_test_reason = status.get("latest_standard_test_reason") or "n/a"
    latest_standard_test_pack = status.get("latest_standard_test_pack") or "n/a"
    chat_mode = status.get("chat_mode") or "chat"
    chat_turn_count = status.get("chat_turn_count", 0)
    queued_architect_instruction_count = status.get("queued_architect_instruction_count", 0)
    latest_architect_instruction = status.get("latest_architect_instruction") or "n/a"
    specialist_profiles = status.get("specialist_profiles") or []
    suggestions_text = "\n".join([f"- {item}" for item in suggestions]) if suggestions else "- none"
    warnings_text = "\n".join([f"- {item}" for item in warnings]) if warnings else "- none"
    if prep_proposals:
        prep_lines = []
        for proposal in prep_proposals:
            prep_lines.append(
                f"- {proposal.get('agent_name', 'unknown')} [{proposal.get('status', 'PENDING')}]: "
                f"{proposal.get('title', '')}"
            )
            prep_lines.append(f"  Action: {proposal.get('suggested_action', 'n/a')}")
            prep_lines.append(f"  Tools: {', '.join(proposal.get('requested_tools') or []) or 'none'}")
            prep_lines.append(f"  Updates: {', '.join(proposal.get('requested_updates') or []) or 'none'}")
        prep_text = "\n".join(prep_lines)
    else:
        prep_text = "- none"
    if specialist_profiles:
        profile_lines = []
        for profile in specialist_profiles[:5]:
            profile_lines.append(
                f"- {profile.get('agent_name', 'unknown')} / {profile.get('task_family', 'n/a')}: "
                f"{profile.get('current_expert_trend', 'forming')} "
                f"(reuse={profile.get('reuse_count', 0)}, refresh={profile.get('refresh_count', 0)}, "
                f"invalidations={profile.get('invalidations', 0)}, success={profile.get('success_rate', 0.0):.2f})"
            )
        specialist_text = "\n".join(profile_lines)
    else:
        specialist_text = "- none"
    content = "\n".join(
        [
            f"{prefix}",
            "",
            "[Chat Lane]",
            f"Mode: {chat_mode}",
            f"Turns: {chat_turn_count}",
            f"Latest Architect Instruction: {latest_architect_instruction}",
            "",
            "[Swarm Health]",
            f"State: {status['state']}",
            f"Phase: {status['phase']}",
            f"Wave: {status['wave_name']} ({status['wave_index']})",
            f"Active Topology: {', '.join(status['active_topology'])}",
            f"Spawns: {status['spawn_count']}",
            f"Open Handoffs: {status.get('open_handoff_count', 0)}",
            f"Failure Memory Hits: {status.get('failure_memory_hits', 0)}",
            f"Hallucination Confidence: {status.get('hallucination_confidence', 1.0):.3f}",
            f"Hallucination Alerts: {status.get('hallucination_alert_count', 0)}",
            f"Latest Hallucination Alert: {status.get('latest_hallucination_alert') or 'n/a'}",
            f"Team Ideas: {status.get('team_ideas_count', 0)}",
            f"Latest Brainstorm: {status.get('latest_brainstorm_summary') or 'n/a'}",
            f"Recommendation: {status.get('recommendation') or 'n/a'}",
            "",
            "[Memory + Architect]",
            f"Local Memory Packets: {local_memory_packet_count}",
            f"Local Memory Reuses: {local_memory_reuse_count}",
            f"Local Memory Invalidations: {local_memory_invalidations}",
            f"Local API Inflight: {local_api_inflight}",
            f"Local API Throttle Hits: {local_api_throttle_hits}",
            f"Local API User Waiting: {local_api_user_waiting}",
            f"Local API Swarm Waiting: {local_api_swarm_waiting}",
            f"Local API Last Lane: {local_api_last_lane}",
            f"Latest Local Memory Agent: {latest_local_memory_agent}",
            f"Latest Local Memory Family: {latest_local_memory_task_family}",
            f"Latest Local Memory Note: {latest_local_memory_note}",
            f"Queued Architect Briefs: {queued_architect_instruction_count}",
            f"Returned Failure Streak: {returned_failure_streak}",
            f"Standard Test Fallbacks: {standard_test_fallback_count}",
            f"Latest Standard Test Reason: {latest_standard_test_reason}",
            f"Latest Standard Test Pack: {latest_standard_test_pack}",
            f"Specialist Snapshot:\n{specialist_text}",
            "",
            "[Stage + Rehearsal]",
            f"Stage Manifest Current: {stage_manifest_current}",
            f"Stage Manifest Next: {stage_manifest_next}",
            f"Stage Manifest Score: {stage_manifest_score:.4f}",
            f"Stage Manifest Profile: {stage_manifest_profile}",
            f"Stage Manifest Preload: {', '.join(stage_manifest_preload_bundle) if stage_manifest_preload_bundle else 'none'}",
            f"Stage Manifest Tools: {', '.join(stage_manifest_required_tools) if stage_manifest_required_tools else 'none'}",
            f"Stage Manifest Checklist: {', '.join(stage_manifest_report_checklist) if stage_manifest_report_checklist else 'none'}",
            f"Rehearsal State: {rehearsal_state}",
            f"Rehearsal Profile: {rehearsal_profile}",
            f"Rehearsal Report: {rehearsal_report_path}",
            f"Rehearsal Manifest: {rehearsal_manifest_path}",
            f"Rehearsal Trace: {rehearsal_trace_path}",
            "",
            "[Preflight + Controls]",
            f"Preflight Bundle: {prep_bundle_id}",
            f"Preflight Goal: {prep_goal}",
            f"Preflight Status: {prep_status}",
            f"Preflight Ready: {prep_ready}",
            f"Preflight Requested Tools: {', '.join(prep_tools) if prep_tools else 'none'}",
            f"Preflight Required Testing Tools: {', '.join(prep_testing_tools) if prep_testing_tools else 'none'}",
            f"Preflight Required Reporting Tools: {', '.join(prep_reporting_tools) if prep_reporting_tools else 'none'}",
            f"Preflight Required Diagnostics Tools: {', '.join(prep_diagnostics_tools) if prep_diagnostics_tools else 'none'}",
            f"Preflight Requested Updates: {', '.join(prep_updates) if prep_updates else 'none'}",
            f"Preflight Proposals:\n{prep_text}",
            f"Distillation Enabled: {distill_enabled}",
            f"Adaptive Compaction: {adaptive_enabled}",
            f"Requested Interval: every {requested_interval} wave(s)",
            f"Active Interval: every {active_interval} wave(s)",
            f"Memory Rule Limit: {distill_rules}",
            f"Memory Breadcrumb Limit: {distill_crumbs}",
            f"Directives Active: {status.get('directives_active', True)}",
            f"Unfinished Features: {status.get('unfinished_feature_count', 0)}",
            f"Current Focus: {status.get('current_focus') or 'n/a'}",
            f"Ramp Level: {status.get('ramp_level', 0)}",
            f"Guard Mode: {status.get('guard_mode', 'NORMAL')}",
            f"Guard Interventions: {status.get('guard_interventions', 0)}",
            f"Latest Guard Action: {status.get('latest_guard_action') or 'n/a'}",
            f"Latest Guard Reason: {status.get('latest_guard_reason') or 'n/a'}",
            f"Handoff Mismatches: {status.get('handoff_mismatch_count', 0)}",
            f"Latest Handoff Brief: {status.get('latest_handoff_brief') or 'n/a'}",
            f"Rosetta Warnings: {status.get('rosetta_warning_count', 0)}",
            f"Latest Rosetta Warning: {status.get('latest_rosetta_warning') or 'n/a'}",
            f"Active Skills: {status.get('active_skill_count', 0)}",
            f"Skill Retools: {status.get('skill_retool_count', 0)}",
            f"Latest Skill Event: {status.get('latest_skill_event') or 'n/a'}",
            f"Test Command: {active_test_command}",
            f"Artifacts: {artifacts}",
            f"Memory Primitives: {artifacts}\\memory_primitives.md",
            f"Memory Formats: {artifacts}\\memory_formats.json",
            "",
            "[Guidance]",
            f"Warnings:\n{warnings_text}",
            f"Suggestions:\n{suggestions_text}",
        ]
    )
    await cl.Message(content=content, actions=_control_actions()).send()


@cl.on_chat_start
async def on_chat_start():
    controller = _build_controller()
    cl.user_session.set("swarm_controller", controller)
    cl.user_session.set("active_test_command", RunConfig().test_command)
    cl.user_session.set("run_config_overrides", {})
    cl.user_session.set("chat_transcript", RollingConversation(limit=_chat_history_limit()))
    await cl.Message(
        content=(
            "Autonomous Swarm v2 online.\n"
            "Normal messages chat with the local bot first; use /swarm <goal> or /runjson <json> to start the swarm.\n"
            "Ask for /health to get a swarm health summary, or /architect to queue a master-architect brief.\n"
            "Preflight suggestions, requested tools, and requested updates are prepared internally.\n"
            "Rehearsal simulations can run in parallel and hot-swap better stage manifests.\n"
            "Commands: /pause, /resume, /stop, /status, /testcmd <command>, "
            "/memory <default|fast|deep|off>, /adaptive <on|off>, /prep, /launch, /swarm <goal>, "
            "/runjson <json>, /rehearsal <healthy|mixed|stress>"
        ),
        actions=_control_actions(),
    ).send()


@cl.action_callback("swarm_pause")
async def on_pause(_: cl.Action):
    controller: SwarmController = cl.user_session.get("swarm_controller")
    controller.pause()
    await _send_status(controller, "Paused")


@cl.action_callback("swarm_resume")
async def on_resume(_: cl.Action):
    controller: SwarmController = cl.user_session.get("swarm_controller")
    controller.resume()
    await _send_status(controller, "Resumed")


@cl.action_callback("swarm_stop")
async def on_stop(_: cl.Action):
    controller: SwarmController = cl.user_session.get("swarm_controller")
    controller.stop()
    await _send_status(controller, "Stop requested")


@cl.action_callback("swarm_status")
async def on_status(_: cl.Action):
    controller: SwarmController = cl.user_session.get("swarm_controller")
    await _send_status(controller)


async def _set_runner(command: str, label: str):
    cl.user_session.set("active_test_command", command)
    controller: SwarmController = cl.user_session.get("swarm_controller")
    await _send_status(controller, f"{label} selected")


@cl.action_callback("runner_pytest")
async def on_runner_pytest(_: cl.Action):
    await _set_runner("pytest {tests_path} -q", "Pytest runner")


@cl.action_callback("runner_dotnet")
async def on_runner_dotnet(_: cl.Action):
    await _set_runner("dotnet test --nologo", "Dotnet runner")


@cl.action_callback("runner_npm")
async def on_runner_npm(_: cl.Action):
    await _set_runner("npm test -- --runInBand", "NPM test runner")


async def _set_memory_profile(profile: str):
    overrides = cl.user_session.get("run_config_overrides") or {}
    if profile == "default":
        overrides.pop("memory_distillation_enabled", None)
        overrides.pop("compaction_interval_waves", None)
        overrides.pop("memory_rule_limit", None)
        overrides.pop("memory_breadcrumb_limit", None)
        label = "Memory profile reset to default"
    elif profile == "fast":
        overrides["memory_distillation_enabled"] = True
        overrides["compaction_interval_waves"] = 2
        overrides["memory_rule_limit"] = 4
        overrides["memory_breadcrumb_limit"] = 3
        label = "Memory profile set to fast"
    elif profile == "deep":
        overrides["memory_distillation_enabled"] = True
        overrides["compaction_interval_waves"] = 1
        overrides["memory_rule_limit"] = 10
        overrides["memory_breadcrumb_limit"] = 8
        label = "Memory profile set to deep"
    else:
        overrides["memory_distillation_enabled"] = False
        label = "Memory distillation disabled"
    cl.user_session.set("run_config_overrides", overrides)
    controller: SwarmController = cl.user_session.get("swarm_controller")
    await _send_status(controller, label)


@cl.action_callback("memory_default")
async def on_memory_default(_: cl.Action):
    await _set_memory_profile("default")


@cl.action_callback("memory_fast")
async def on_memory_fast(_: cl.Action):
    await _set_memory_profile("fast")


@cl.action_callback("memory_deep")
async def on_memory_deep(_: cl.Action):
    await _set_memory_profile("deep")


@cl.action_callback("memory_off")
async def on_memory_off(_: cl.Action):
    await _set_memory_profile("off")


async def _set_adaptive_compaction(enabled: bool):
    overrides = cl.user_session.get("run_config_overrides") or {}
    overrides["adaptive_compaction_enabled"] = enabled
    cl.user_session.set("run_config_overrides", overrides)
    controller: SwarmController = cl.user_session.get("swarm_controller")
    await _send_status(
        controller,
        "Adaptive compaction enabled" if enabled else "Adaptive compaction disabled",
    )


async def _review_preflight(decision: str, target: str, note: str = ""):
    controller: SwarmController = cl.user_session.get("swarm_controller")
    try:
        controller.review_preflight(target=target, decision=decision, note=note)
    except Exception as exc:
        await cl.Message(content=f"Preflight review failed: {exc}").send()
        return
    await _send_status(controller, f"Preflight {decision.lower()} for {target}")


async def _launch_prepared_run():
    controller: SwarmController = cl.user_session.get("swarm_controller")
    try:
        run_id = controller.launch_prepared_run()
    except Exception as exc:
        await cl.Message(content=f"Launch blocked: {exc}").send()
        return
    await cl.Message(
        content=(
            f"Preflight approved. Swarm launch started.\n"
            f"Run ID: {run_id}"
        ),
        actions=_control_actions(),
    ).send()
    await _send_status(controller, "Prepared run launched")


async def _run_rehearsal(profile: str = "mixed"):
    controller: SwarmController = cl.user_session.get("swarm_controller")
    try:
        result = controller.run_rehearsal(profile=profile, apply_if_better=True)
    except Exception as exc:
        await cl.Message(content=f"Rehearsal failed: {exc}").send()
        return
    manifest = result.get("stage_manifest") or {}
    await cl.Message(
        content=(
            f"Rehearsal complete: {result.get('rehearsal_id')}\n"
            f"Profile: {result.get('profile')}\n"
            f"Accepted: {result.get('accepted')}\n"
            f"Live Score: {result.get('live_score', 0.0):.4f}\n"
            f"Rehearsal Score: {result.get('rehearsal_score', 0.0):.4f}\n"
            f"Current Stage: {manifest.get('current_stage', 'n/a')}\n"
            f"Next Stage: {manifest.get('next_stage', 'n/a')}"
        ),
        actions=_control_actions(),
    ).send()
    await _send_status(controller, "Rehearsal completed")


async def _handle_local_chat(
    controller: SwarmController,
    user_message: cl.Message,
    text: str,
    mode: str,
):
    transcript = _chat_transcript()
    transcript.append("user", text, user_message)
    conversation_context = transcript.recent_context(limit=8)
    config = _parse_config()
    result = await asyncio.to_thread(
        controller.respond_to_chat,
        text,
        config,
        mode,
        conversation_context,
    )
    reply = result.get("reply") or "I’m here."
    background_instruction = result.get("background_instruction") or ""
    swarm_health = result.get("swarm_health") or ""
    if swarm_health and mode in {"health", "architect"}:
        reply = f"{reply}\n\nSwarm health:\n{swarm_health}"
    if background_instruction:
        reply = (
            f"{reply}\n\nArchitect brief queued: "
            f"{background_instruction[:220]}"
        )
    assistant_message = await cl.Message(
        content=reply,
        actions=_control_actions(),
    ).send()
    transcript.append("assistant", reply, assistant_message)
    await transcript.trim()


@cl.action_callback("adaptive_on")
async def on_adaptive_on(_: cl.Action):
    await _set_adaptive_compaction(True)


@cl.action_callback("adaptive_off")
async def on_adaptive_off(_: cl.Action):
    await _set_adaptive_compaction(False)


@cl.action_callback("prep_status")
async def on_prep_status(_: cl.Action):
    controller: SwarmController = cl.user_session.get("swarm_controller")
    await _send_status(controller, "Preflight status")


@cl.action_callback("prep_launch")
async def on_prep_launch(_: cl.Action):
    await _launch_prepared_run()


@cl.action_callback("rehearsal_run")
async def on_rehearsal_run(_: cl.Action):
    await _run_rehearsal("mixed")


@cl.on_message
async def on_message(message: cl.Message):
    controller: SwarmController = cl.user_session.get("swarm_controller")
    text = message.content.strip()

    if text.lower() == "/pause":
        controller.pause()
        await _send_status(controller, "Paused")
        return
    if text.lower() == "/resume":
        controller.resume()
        await _send_status(controller, "Resumed")
        return
    if text.lower() == "/stop":
        controller.stop()
        await _send_status(controller, "Stop requested")
        return
    if text.lower() == "/status":
        await _send_status(controller)
        return
    if text.lower().startswith("/testcmd "):
        custom = text[len("/testcmd "):].strip()
        if not custom:
            await cl.Message(content="Usage: /testcmd <command>").send()
            return
        cl.user_session.set("active_test_command", custom)
        await _send_status(controller, "Custom test command updated")
        return
    if text.lower().startswith("/memory "):
        profile = text[len("/memory "):].strip().lower()
        if profile not in {"default", "fast", "deep", "off"}:
            await cl.Message(content="Usage: /memory <default|fast|deep|off>").send()
            return
        await _set_memory_profile(profile)
        return
    if text.lower().startswith("/adaptive "):
        option = text[len("/adaptive "):].strip().lower()
        if option not in {"on", "off"}:
            await cl.Message(content="Usage: /adaptive <on|off>").send()
            return
        await _set_adaptive_compaction(option == "on")
        return
    if text.lower().startswith("/approve "):
        remainder = text[len("/approve "):].strip()
        if not remainder:
            await cl.Message(
                content="Usage: /approve <SeedPrepBot|DirectivePrepBot|StabilityPrepBot|all> [note]"
            ).send()
            return
        parts = remainder.split(maxsplit=1)
        target = parts[0]
        note = parts[1] if len(parts) > 1 else ""
        await _review_preflight("APPROVED", target, note)
        return
    if text.lower().startswith("/deny "):
        remainder = text[len("/deny "):].strip()
        if not remainder:
            await cl.Message(
                content="Usage: /deny <SeedPrepBot|DirectivePrepBot|StabilityPrepBot|all> [note]"
            ).send()
            return
        parts = remainder.split(maxsplit=1)
        target = parts[0]
        note = parts[1] if len(parts) > 1 else ""
        await _review_preflight("DENIED", target, note)
        return
    if text.lower().startswith("/revise "):
        remainder = text[len("/revise "):].strip()
        if not remainder:
            await cl.Message(
                content="Usage: /revise <SeedPrepBot|DirectivePrepBot|StabilityPrepBot|all> [note]"
            ).send()
            return
        parts = remainder.split(maxsplit=1)
        target = parts[0]
        note = parts[1] if len(parts) > 1 else ""
        await _review_preflight("REVISE", target, note)
        return
    if text.lower() == "/launch":
        await _launch_prepared_run()
        return
    if text.lower() == "/prep":
        await _send_status(controller, "Preflight bundle")
        return
    if text.lower().startswith("/rehearsal "):
        profile = text[len("/rehearsal "):].strip().lower()
        if profile not in {"healthy", "mixed", "stress"}:
            await cl.Message(content="Usage: /rehearsal <healthy|mixed|stress>").send()
            return
        await _run_rehearsal(profile)
        return

    if text.lower().startswith("/runjson "):
        raw_json = text[len("/runjson "):].strip()
        try:
            payload = json.loads(raw_json)
            text = str(payload.get("prompt", "")).strip()
        except Exception as exc:
            await cl.Message(content=f"Invalid /runjson payload: {exc}").send()
            return
        if not text:
            await cl.Message(content="Provide a goal prompt in /runjson.").send()
            return
        goal = _parse_goal(text)
        config = _parse_config(payload)
        config.test_command = payload.get(
            "test_command",
            cl.user_session.get("active_test_command") or config.test_command,
        )
        run_id = controller.start(goal=goal, config=config)
        await cl.Message(
            content=(
                f"Run started: {run_id}\n"
                "Preflight prep ran internally and the request list is visible in /status."
            ),
            actions=_control_actions(),
        ).send()
        await _send_status(controller)
        return

    if should_launch_swarm(text):
        swarm_goal = text.split(" ", 1)[1].strip() if " " in text.strip() else ""
        if not swarm_goal:
            await cl.Message(content="Usage: /swarm <goal>").send()
            return
        goal = _parse_goal(swarm_goal)
        config = _parse_config()
        run_id = controller.start(goal=goal, config=config)
        await cl.Message(
            content=(
                f"Swarm run started: {run_id}\n"
                "The local chat lane stays open while the background swarm works."
            ),
            actions=_control_actions(),
        ).send()
        await _send_status(controller)
        return

    if not text:
        await cl.Message(
            content=(
                "Type a chat message, /health for a swarm summary, "
                "/architect for a master-architect request, or /swarm <goal> to launch a run."
            )
        ).send()
        return

    chat_mode = detect_chat_mode(text)
    if text.lower().startswith("/health"):
        chat_mode = "health"
    elif text.lower().startswith("/architect"):
        chat_mode = "architect"
    elif text.lower().startswith("/chat"):
        chat_mode = "chat"
    elif text.startswith("/"):
        await cl.Message(
            content=(
                "Unknown command. Use /swarm <goal> to start a swarm run, "
                "/health for a health report, or /architect for a master-architect request."
            )
        ).send()
        return

    await _handle_local_chat(controller, message, text, chat_mode)
