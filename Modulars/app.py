import os
import json
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


def _build_controller() -> SwarmController:
    profiles = build_swarm_profiles()

    groq_client = _client("https://api.groq.com/openai/v1", os.getenv("GROQ_API_KEY", ""))
    local_client = _client("http://localhost:11434/v1", "ollama")

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
        system_prompt=profiles["coder"].system_prompt,
        client=local_client,
    )
    judge_agent = SimpleAgent(
        name=profiles["judge"].name,
        model=profiles["judge"].model,
        fallback_models=profiles["judge"].fallback_models,
        system_prompt=profiles["judge"].system_prompt,
        client=local_client,
    )

    return SwarmController(
        test_agent=test_agent,
        coder_agent=coder_agent,
        judge_agent=judge_agent,
        root_dir=os.getcwd(),
    )


def _parse_goal(message_text: str) -> TaskGoal:
    return TaskGoal(prompt=message_text, target_files=["app_v3.py"], language="general")


def _parse_config(payload: Dict[str, Any] | None = None) -> RunConfig:
    payload = payload or {}
    defaults = RunConfig()
    fields = {
        field: payload.get(field, getattr(defaults, field))
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
    ]


async def _send_status(controller: SwarmController, prefix: str = "Swarm status"):
    status = controller.status()
    active_test_command = cl.user_session.get("active_test_command") or RunConfig().test_command
    await cl.Message(
        content=(
            f"{prefix}\n"
            f"State: {status['state']}\n"
            f"Phase: {status['phase']}\n"
            f"Wave: {status['wave_name']} ({status['wave_index']})\n"
            f"Active Topology: {', '.join(status['active_topology'])}\n"
            f"Spawns: {status['spawn_count']}\n"
            f"Failure Memory Hits: {status.get('failure_memory_hits', 0)}\n"
            f"Team Ideas: {status.get('team_ideas_count', 0)}\n"
            f"Latest Brainstorm: {status.get('latest_brainstorm_summary') or 'n/a'}\n"
            f"Recommendation: {status.get('recommendation') or 'n/a'}\n"
            f"Test Command: {active_test_command}\n"
            f"Artifacts: {status.get('artifacts_path') or 'n/a'}"
        ),
        actions=_control_actions(),
    ).send()


@cl.on_chat_start
async def on_chat_start():
    controller = _build_controller()
    cl.user_session.set("swarm_controller", controller)
    cl.user_session.set("active_test_command", RunConfig().test_command)
    await cl.Message(
        content=(
            "Autonomous Swarm v2 online. Send a high-level coding goal to start a run.\n"
            "Commands: /pause, /resume, /stop, /status, /testcmd <command>, /runjson <json>"
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
