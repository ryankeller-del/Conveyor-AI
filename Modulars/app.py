import os
import json
import socket
from typing import Any, Dict

import chainlit as cl
from dotenv import load_dotenv
from openai import OpenAI

from bot_profiles_v3 import build_swarm_profiles
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
    )
    coder_agent = SimpleAgent(
        name=profiles["coder"].name,
        model=profiles["coder"].model,
        fallback_models=profiles["coder"].fallback_models,
        fallback_client_models=["openrouter/free"],
        system_prompt=profiles["coder"].system_prompt,
        client=local_client if local_available else None,
        fallback_client=openrouter_client,
    )
    judge_agent = SimpleAgent(
        name=profiles["judge"].name,
        model=profiles["judge"].model,
        fallback_models=profiles["judge"].fallback_models,
        system_prompt=profiles["judge"].system_prompt,
        client=local_client,
    )
    context_guard_agent = SimpleAgent(
        name=profiles["context_guard"].name,
        model=profiles["context_guard"].model,
        fallback_models=profiles["context_guard"].fallback_models,
        system_prompt=profiles["context_guard"].system_prompt,
        client=local_client if local_available else None,
    )
    pattern_agent = SimpleAgent(
        name=profiles["pattern_finder"].name,
        model=profiles["pattern_finder"].model,
        fallback_models=profiles["pattern_finder"].fallback_models,
        system_prompt=profiles["pattern_finder"].system_prompt,
        client=local_client if local_available else None,
    )
    compression_agent = SimpleAgent(
        name=profiles["compression"].name,
        model=profiles["compression"].model,
        fallback_models=profiles["compression"].fallback_models,
        system_prompt=profiles["compression"].system_prompt,
        client=local_client if local_available else None,
    )
    novelty_agent = SimpleAgent(
        name=profiles["novelty"].name,
        model=profiles["novelty"].model,
        fallback_models=profiles["novelty"].fallback_models,
        system_prompt=profiles["novelty"].system_prompt,
        client=local_client if local_available else None,
    )
    stability_guard_agent = SimpleAgent(
        name=profiles["stability_guard"].name,
        model=profiles["stability_guard"].model,
        fallback_models=profiles["stability_guard"].fallback_models,
        system_prompt=profiles["stability_guard"].system_prompt,
        client=local_client if local_available else None,
    )

    return SwarmController(
        test_agent=test_agent,
        coder_agent=coder_agent,
        judge_agent=judge_agent,
        root_dir=os.path.dirname(os.path.abspath(__file__)),
        context_guard_agent=context_guard_agent,
        pattern_agent=pattern_agent,
        compression_agent=compression_agent,
        novelty_agent=novelty_agent,
        stability_guard_agent=stability_guard_agent,
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
    suggestions_text = "\n".join([f"- {item}" for item in suggestions]) if suggestions else "- none"
    warnings_text = "\n".join([f"- {item}" for item in warnings]) if warnings else "- none"
    await cl.Message(
        content=(
            f"{prefix}\n"
            f"State: {status['state']}\n"
            f"Phase: {status['phase']}\n"
            f"Wave: {status['wave_name']} ({status['wave_index']})\n"
            f"Active Topology: {', '.join(status['active_topology'])}\n"
            f"Spawns: {status['spawn_count']}\n"
            f"Failure Memory Hits: {status.get('failure_memory_hits', 0)}\n"
            f"Hallucination Confidence: {status.get('hallucination_confidence', 1.0):.3f}\n"
            f"Hallucination Alerts: {status.get('hallucination_alert_count', 0)}\n"
            f"Latest Hallucination Alert: {status.get('latest_hallucination_alert') or 'n/a'}\n"
            f"Team Ideas: {status.get('team_ideas_count', 0)}\n"
            f"Latest Brainstorm: {status.get('latest_brainstorm_summary') or 'n/a'}\n"
            f"Compaction Runs: {status.get('compaction_runs', 0)}\n"
            f"Memory Format: {status.get('active_memory_format') or 'n/a'}\n"
            f"Latest Memory Winner: {status.get('latest_memory_winner') or 'n/a'}\n"
            f"Latest Breadcrumb: {status.get('latest_breadcrumb') or 'n/a'}\n"
            f"Directives Active: {status.get('directives_active', True)}\n"
            f"Unfinished Features: {status.get('unfinished_feature_count', 0)}\n"
            f"Current Focus: {status.get('current_focus') or 'n/a'}\n"
            f"Open Handoffs: {status.get('open_handoff_count', 0)}\n"
            f"Latest Handoff Feedback: {status.get('latest_handoff_feedback') or 'n/a'}\n"
            f"Ramp Level: {status.get('ramp_level', 0)}\n"
            f"Guard Mode: {status.get('guard_mode', 'NORMAL')}\n"
            f"Guard Interventions: {status.get('guard_interventions', 0)}\n"
            f"Latest Guard Action: {status.get('latest_guard_action') or 'n/a'}\n"
            f"Latest Guard Reason: {status.get('latest_guard_reason') or 'n/a'}\n"
            f"Handoff Mismatches: {status.get('handoff_mismatch_count', 0)}\n"
            f"Latest Handoff Brief: {status.get('latest_handoff_brief') or 'n/a'}\n"
            f"Rosetta Warnings: {status.get('rosetta_warning_count', 0)}\n"
            f"Latest Rosetta Warning: {status.get('latest_rosetta_warning') or 'n/a'}\n"
            f"Active Skills: {status.get('active_skill_count', 0)}\n"
            f"Skill Retools: {status.get('skill_retool_count', 0)}\n"
            f"Latest Skill Event: {status.get('latest_skill_event') or 'n/a'}\n"
            f"Distillation Enabled: {distill_enabled}\n"
            f"Adaptive Compaction: {adaptive_enabled}\n"
            f"Requested Interval: every {requested_interval} wave(s)\n"
            f"Active Interval: every {active_interval} wave(s)\n"
            f"Memory Rule Limit: {distill_rules}\n"
            f"Memory Breadcrumb Limit: {distill_crumbs}\n"
            f"Recommendation: {status.get('recommendation') or 'n/a'}\n"
            f"Test Command: {active_test_command}\n"
            f"Artifacts: {artifacts}\n"
            f"Memory Primitives: {artifacts}\\memory_primitives.md\n"
            f"Memory Formats: {artifacts}\\memory_formats.json\n"
            f"Warnings:\n{warnings_text}\n"
            f"Suggestions:\n{suggestions_text}"
        ),
        actions=_control_actions(),
    ).send()


@cl.on_chat_start
async def on_chat_start():
    controller = _build_controller()
    cl.user_session.set("swarm_controller", controller)
    cl.user_session.set("active_test_command", RunConfig().test_command)
    cl.user_session.set("run_config_overrides", {})
    await cl.Message(
        content=(
            "Autonomous Swarm v2 online. Send a high-level coding goal to start a run.\n"
            "Commands: /pause, /resume, /stop, /status, /testcmd <command>, "
            "/memory <default|fast|deep|off>, /adaptive <on|off>, /runjson <json>"
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


@cl.action_callback("adaptive_on")
async def on_adaptive_on(_: cl.Action):
    await _set_adaptive_compaction(True)


@cl.action_callback("adaptive_off")
async def on_adaptive_off(_: cl.Action):
    await _set_adaptive_compaction(False)


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

    payload: Dict[str, Any] = {}
    if text.lower().startswith("/runjson "):
        raw_json = text[len("/runjson "):].strip()
        try:
            payload = json.loads(raw_json)
            text = str(payload.get("prompt", "")).strip()
        except Exception as exc:
            await cl.Message(content=f"Invalid /runjson payload: {exc}").send()
            return
    if not text:
        await cl.Message(content="Provide a goal prompt to start a run.").send()
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
            "Adaptive test expansion and dynamic spawning are enabled."
        ),
        actions=_control_actions(),
    ).send()
    await _send_status(controller)
