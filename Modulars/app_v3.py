import os
import socket
from typing import Any, Dict

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template_string, request
from openai import OpenAI

from bot_profiles_v3 import build_swarm_profiles
from swarm_core.bots import SimpleAgent
from swarm_core.controller import SwarmController
from swarm_core.local_models import desktop_ollama_base_url, desktop_ollama_target
from swarm_core.types import RunConfig, TaskGoal

load_dotenv()

app = Flask(__name__)


def _client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key)


def _is_local_llm_available(host: str | None = None, port: int | None = None, timeout: float = 0.5) -> bool:
    resolved_host, resolved_port = host, port
    if resolved_host is None or resolved_port is None:
        resolved_host, resolved_port = desktop_ollama_target()
    try:
        with socket.create_connection((resolved_host, resolved_port), timeout=timeout):
            return True
    except OSError:
        return False


def _build_controller() -> SwarmController:
    profiles = build_swarm_profiles()

    groq_client = _client("https://api.groq.com/openai/v1", os.getenv("GROQ_API_KEY", ""))
    openrouter_client = _client("https://openrouter.ai/api/v1", os.getenv("OPENROUTER_API_KEY", ""))
    local_client = _client(desktop_ollama_base_url(), "ollama")
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


def _render_swarm_narrative(limit: int = 40) -> str:
    entries = controller.recent_swarm_narrative(limit=limit)
    if not entries:
        return "No swarm events yet.\n\nStart a run or queue background work to see the narrative here."
    lines = []
    for item in entries:
        timestamp = item.get("timestamp", "")
        kind = item.get("kind", "event")
        headline = item.get("headline", "event")
        text = item.get("text", "")
        lines.append(f"[{timestamp}] {kind}: {headline}\n{text}".strip())
    return "\n\n".join(lines)


SWARM_MONITOR_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Codex Swarm Monitor</title>
    <style>
      :root {
        color-scheme: dark;
        --bg: #0d1117;
        --panel: #111826;
        --panel-soft: #182235;
        --text: #e6edf3;
        --muted: #9fb0c3;
        --accent: #ff4f8b;
        --border: #243044;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        min-height: 100vh;
        background: linear-gradient(180deg, #090c12 0%, #0d1117 100%);
        color: var(--text);
        font-family: "Segoe UI", Arial, sans-serif;
      }
      header {
        padding: 1rem 1.5rem;
        border-bottom: 1px solid var(--border);
        background: rgba(9, 12, 18, 0.92);
      }
      h1 { margin: 0; font-size: 1.1rem; letter-spacing: 0.04em; }
      .sub { color: var(--muted); font-size: 0.85rem; margin-top: 0.25rem; }
      .status {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 0.75rem;
        padding: 1rem 1.5rem 0.5rem;
      }
      .card {
        background: rgba(17, 24, 38, 0.9);
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 0.75rem 0.9rem;
      }
      .label {
        color: var(--muted);
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 0.35rem;
      }
      .value { font-size: 0.95rem; line-height: 1.35; word-break: break-word; white-space: pre-wrap; }
      .panel {
        margin: 0 1.5rem 1.5rem;
        background: rgba(17, 24, 38, 0.88);
        border: 1px solid var(--border);
        border-radius: 18px;
        overflow: hidden;
      }
      .panel-head {
        display: flex;
        justify-content: space-between;
        gap: 1rem;
        align-items: center;
        padding: 0.9rem 1rem;
        background: rgba(24, 34, 53, 0.9);
        border-bottom: 1px solid var(--border);
      }
      .panel-head strong { font-size: 0.96rem; }
      .panel-head span { color: var(--muted); font-size: 0.82rem; }
      .transcript {
        max-height: calc(100vh - 290px);
        overflow: auto;
        padding: 1rem;
      }
      .entry {
        padding: 0.8rem 0.85rem;
        margin-bottom: 0.75rem;
        background: rgba(9, 14, 24, 0.85);
        border: 1px solid var(--border);
        border-radius: 12px;
      }
      .entry .meta {
        display: flex;
        flex-wrap: wrap;
        gap: 0.6rem;
        color: var(--muted);
        font-size: 0.74rem;
        margin-bottom: 0.35rem;
      }
      .entry .message {
        white-space: pre-wrap;
        line-height: 1.45;
      }
    </style>
  </head>
  <body>
    <header>
      <h1>Codex Swarm Monitor</h1>
      <div class="sub">Narrative background swarm feed, separate from the local chat lane.</div>
    </header>
    <section class="status">
      <div class="card"><div class="label">Desktop Ollama</div><div class="value">{{ ollama_host }}</div></div>
      <div class="card"><div class="label">Current Stage</div><div class="value">{{ stage_manifest_current or 'n/a' }} → {{ stage_manifest_next or 'n/a' }}</div></div>
      <div class="card"><div class="label">Latest Local Model</div><div class="value">{{ latest_local_model_name or 'n/a' }} ({{ latest_local_model_lane or 'n/a' }})</div></div>
      <div class="card"><div class="label">Background Queue</div><div class="value">{{ background_run_queue_depth or 0 }}</div></div>
      <div class="card"><div class="label">Generation Memory</div><div class="value">{{ generation_memory_records or 0 }} records / {{ generation_memory_restores or 0 }} restores</div></div>
      <div class="card"><div class="label">Latest Aspiration</div><div class="value">{{ generation_memory_latest_aspiration or 'n/a' }}</div></div>
    </section>
    <main>
      <div class="panel">
        <div class="panel-head">
          <strong>Swarm Logs</strong>
          <span>{{ transcript_count }} entries</span>
        </div>
        <div class="transcript">
          {% if transcript_entries %}
            {% for entry in transcript_entries %}
              <div class="entry">
                <div class="meta">
                  <span>{{ entry.timestamp }}</span>
                  <span>{{ entry.kind }}</span>
                  <span>{{ entry.headline }}</span>
                </div>
                <div class="message">{{ entry.text }}</div>
              </div>
            {% endfor %}
          {% else %}
            <div class="entry">
              <div class="message">No swarm events yet. Start a run or queue background work to see the narrative here.</div>
            </div>
          {% endif %}
        </div>
      </div>
    </main>
  </body>
</html>
"""


@app.get("/")
def home():
    return jsonify(
        {
            "name": "Codex Autonomous Swarm v2",
            "status": "online",
            "endpoints": [
                "/run",
                "/status",
                "/swarm",
                "/swarm/transcript",
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


@app.get("/swarm")
def swarm_monitor():
    status = controller.status()
    entries = controller.recent_swarm_narrative(limit=40)
    return render_template_string(
        SWARM_MONITOR_TEMPLATE,
        ollama_host=status.get("local_model_host") or os.getenv("OLLAMA_BASE_URL", "http://192.168.0.150:11434"),
        stage_manifest_current=status.get("stage_manifest_current"),
        stage_manifest_next=status.get("stage_manifest_next"),
        latest_local_model_name=status.get("latest_local_model_name"),
        latest_local_model_lane=status.get("latest_local_model_lane"),
        background_run_queue_depth=status.get("background_run_queue_depth", 0),
        generation_memory_records=status.get("generation_memory_records", 0),
        generation_memory_restores=status.get("generation_memory_restores", 0),
        generation_memory_latest_aspiration=status.get("generation_memory_latest_aspiration"),
        transcript_entries=entries,
        transcript_count=len(entries),
    )


@app.get("/swarm/transcript")
def swarm_transcript():
    text = _render_swarm_narrative()
    return Response(text, mimetype="text/plain; charset=utf-8")


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
