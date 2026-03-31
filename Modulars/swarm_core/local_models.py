from __future__ import annotations

import os
from typing import Dict, List
from urllib.parse import urlparse

DEFAULT_DESKTOP_OLLAMA_HOST = "http://192.168.0.150:11434"


def desktop_ollama_host() -> str:
    host = os.getenv("SWARM_OLLAMA_HOST", DEFAULT_DESKTOP_OLLAMA_HOST).strip()
    if not host:
        host = DEFAULT_DESKTOP_OLLAMA_HOST
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host.rstrip("/")


def desktop_ollama_base_url() -> str:
    return f"{desktop_ollama_host()}/v1"


def desktop_ollama_api_root() -> str:
    return desktop_ollama_host()


def desktop_ollama_target() -> tuple[str, int]:
    parsed = urlparse(desktop_ollama_host())
    host = parsed.hostname or "192.168.0.150"
    port = parsed.port or 11434
    return host, port


def build_desktop_local_routes() -> Dict[str, Dict[str, List[str] | str]]:
    return {
        "chat": {
            "model": "glm-4.7-flash:q4_K_M",
            "fallback_models": [
                "glm-4.7-flash:latest",
                "glm-4.7-flash-16k:latest",
                "qwen2.5-coder:14b",
            ],
        },
        "health": {
            "model": "glm-4.7-flash:q4_K_M",
            "fallback_models": [
                "glm-4.7-flash:latest",
                "glm-4.7-flash-16k:latest",
            ],
        },
        "architect": {
            "model": "glm-4.7-flash:latest",
            "fallback_models": [
                "glm-4.7-flash:q4_K_M",
                "glm-4.7-flash-16k:latest",
            ],
        },
        "coder": {
            "model": "qwen2.5-coder:14b",
            "fallback_models": [
                "qwen3-coder:30b",
                "glm-4.7-flash:q4_K_M",
                "glm-4.7-flash:latest",
            ],
        },
        "judge": {
            "model": "glm-4.7-flash-16k:latest",
            "fallback_models": [
                "deepseek-r1:32b",
                "glm-4.7-flash:q4_K_M",
            ],
        },
        "context_guard": {
            "model": "glm-4.7-flash-16k:latest",
            "fallback_models": [
                "deepseek-r1:32b",
                "glm-4.7-flash:q4_K_M",
            ],
        },
        "pattern_finder": {
            "model": "glm-4.7-flash-16k:latest",
            "fallback_models": [
                "deepseek-r1:32b",
                "qwen2.5-coder:14b",
            ],
        },
        "compression": {
            "model": "glm-4.7-flash-16k:latest",
            "fallback_models": [
                "glm-4.7-flash:q4_K_M",
                "deepseek-r1:32b",
            ],
        },
        "novelty": {
            "model": "glm-4.7-flash:latest",
            "fallback_models": [
                "glm-4.7-flash-16k:latest",
                "deepseek-r1:32b",
            ],
        },
        "stability_guard": {
            "model": "glm-4.7-flash-16k:latest",
            "fallback_models": [
                "deepseek-r1:32b",
                "glm-4.7-flash:q4_K_M",
            ],
        },
        "seed_prep": {
            "model": "glm-4.7-flash:latest",
            "fallback_models": [
                "glm-4.7-flash-16k:latest",
                "deepseek-r1:32b",
            ],
        },
        "directive_prep": {
            "model": "glm-4.7-flash:latest",
            "fallback_models": [
                "glm-4.7-flash-16k:latest",
                "deepseek-r1:32b",
            ],
        },
        "stability_prep": {
            "model": "glm-4.7-flash:latest",
            "fallback_models": [
                "glm-4.7-flash-16k:latest",
                "deepseek-r1:32b",
            ],
        },
    }


def is_text_model(model_name: str) -> bool:
    normalized = (model_name or "").lower()
    return not any(token in normalized for token in ("ocr", "vitpose", "pose"))
