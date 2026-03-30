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
    local_available = _is_local_llm_available()

    groq_client = _client("https://api.groq.com/openai/v1", os.getenv("GROQ_API_KEY", ""))
    openrouter_client = _client("https://openrouter.ai/api/v1", os.getenv("OPENROUTER_API_KEY", ""))
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
        client=local_client if local_available else None,
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
    target_files = payload.get("target_files") or ["app_v3.py"]
    if not isinstance(target_files, list):
        target_files = ["app_v3.py"]
    return TaskGoal(
        prompt=str(payload.get("prompt", "Build robust production-ready code.")),
        target_files=[str(item) for item in target_files],
        language=str(payload.get("language", "general")),
    )


def _config_from_payload(payload: Dict[str, Any]) -> RunConfig:
    defaults = RunConfig()
    fields = {
        field: payload.get(field, getattr(defaults, field))
        for field in defaults.__dataclass_fields__.keys()
    }
    return RunConfig(**fields)


@app.get("/")
def home():
    return jsonify(
        {
            "name": "Codex Autonomous Swarm v2",
            "status": "online",
            "endpoints": [
                "/run",
                "/status",
                "/pause",
                "/resume",
                "/stop",
                "/run/examples",
                "/rehearsal/run",
                "/rehearsal/status",
                "/stage-manifest/apply",
            ],
        }
    )


@app.get("/health")
def health():
    return jsonify({"ok": True, "controller_state": controller.status().get("state")})


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
                    "name": "Minimal stabilization pass",
                    "payload": {
                        "prompt": "Stabilize one small behavior and write tests first.",
                        "target_files": ["app_v3.py"],
                        "language": "general",
                        "test_command": "pytest {tests_path} -q",
                    },
                },
                {
                    "name": "Focused API hardening",
                    "payload": {
                        "prompt": "Harden a narrow API path with validation and deterministic tests.",
                        "target_files": ["app_v3.py"],
                        "language": "general",
                        "test_command": "pytest {tests_path} -q",
                    },
                },
            ]
        }
    )


@app.post("/rehearsal/run")
def rehearsal_run():
    payload = request.get_json(silent=True) or {}
    profile = str(payload.get("profile", "balanced"))
    config = _config_from_payload(payload)
    result = controller.run_rehearsal(
        profile=profile,
        config=config,
        apply_if_better=bool(payload.get("apply_if_better", True)),
    )
    return jsonify({"result": result, "status": controller.status()})


@app.get("/rehearsal/status")
def rehearsal_status():
    return jsonify(controller.status())


@app.post("/stage-manifest/apply")
def apply_stage_manifest():
    payload = request.get_json(silent=True) or {}
    manifest = payload.get("manifest", payload)
    accepted = controller.apply_stage_manifest(manifest)
    return jsonify({"accepted": accepted, "status": controller.status()})


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
        port = int(os.getenv("PORT", "8001"))
        app.run(host="localhost", port=port, debug=True)
    except Exception as exc:
        print(f"An error occurred: {exc}")
