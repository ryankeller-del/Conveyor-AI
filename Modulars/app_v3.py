import os
import socket
from typing import Any, Dict

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from openai import OpenAI

from bot_profiles_v3 import build_swarm_profiles
from swarm_core.bots import SimpleAgent
from swarm_core.controller import SwarmController
from swarm_core.types import RunConfig, TaskGoal

load_dotenv()

app = Flask(__name__)


def _make_openai_client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key)


def _is_local_llm_available(host: str = "127.0.0.1", port: int = 11434, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _build_controller() -> SwarmController:
    profiles = build_swarm_profiles()

    groq_client = _make_openai_client(
        "https://api.groq.com/openai/v1",
        os.getenv("GROQ_API_KEY", ""),
    )
    openrouter_client = _make_openai_client(
        "https://openrouter.ai/api/v1",
        os.getenv("OPENROUTER_API_KEY", ""),
    )
    local_client = _make_openai_client(
        "http://localhost:11434/v1",
        "ollama",
    )
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


controller = _build_controller()


def _goal_from_payload(payload: Dict[str, Any]) -> TaskGoal:
    return TaskGoal(
        prompt=str(payload.get("prompt", "Build robust production-ready code.")),
        target_files=list(payload.get("target_files", ["app_v3.py"])),
        language=str(payload.get("language", "general")),
    )


def _config_from_payload(payload: Dict[str, Any]) -> RunConfig:
    defaults = RunConfig()
    fields = {field: payload.get(field, getattr(defaults, field)) for field in defaults.__dataclass_fields__.keys()}
    return RunConfig(**fields)


@app.get("/")
def hello():
    return "Codex Autonomous Swarm v2 online"


@app.post("/run")
def run_swarm():
    payload = request.get_json(silent=True) or {}
    goal = _goal_from_payload(payload)
    config = _config_from_payload(payload)
    run_id = controller.start(goal=goal, config=config)
    return jsonify({"run_id": run_id, "status": controller.status()})


@app.get("/run/examples")
def run_examples():
    return jsonify(
        {
            "examples": [
                {
                    "name": "Generic pytest run",
                    "payload": {
                        "prompt": "Add robust validation and tests for the API layer.",
                        "target_files": ["app_v3.py"],
                        "language": "general",
                        "test_command": "pytest {tests_path} -q",
                    },
                },
                {
                    "name": "Dotnet test run",
                    "payload": {
                        "prompt": "Implement feature parity with stronger error handling.",
                        "target_files": ["src/Program.cs"],
                        "language": "general",
                        "test_command": "dotnet test --nologo",
                    },
                },
                {
                    "name": "Node test run",
                    "payload": {
                        "prompt": "Refactor service logic and preserve behavior with tests.",
                        "target_files": ["src/index.ts"],
                        "language": "general",
                        "test_command": "npm test -- --runInBand",
                    },
                },
            ]
        }
    )


@app.post("/pause")
def pause_swarm():
    controller.pause()
    return jsonify(controller.status())


@app.post("/resume")
def resume_swarm():
    controller.resume()
    return jsonify(controller.status())


@app.post("/stop")
def stop_swarm():
    controller.stop()
    return jsonify(controller.status())


@app.get("/status")
def status_swarm():
    return jsonify(controller.status())


if __name__ == "__main__":
    try:
        app.run(host="localhost", port=8001, debug=True)
    except Exception as exc:
        print(f"An error occurred: {exc}")
