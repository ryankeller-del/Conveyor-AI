"""Local model runtime detection and routing.

Handles local Ollama availability checks, URL construction,
and model route resolution for the agent fallback chain.

Legacy source: swarm_core/local_models.py
  - desktop_ollama_base_url()
  - desktop_ollama_target()
  - _is_local_llm_available()
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from typing import Any


def desktop_ollama_host() -> str:
    """Default Ollama host."""
    return os.environ.get("OLLAMA_HOST", "localhost")


def desktop_ollama_port() -> int:
    """Default Ollama port."""
    raw = os.environ.get("OLLAMA_PORT", "11434")
    try:
        return int(raw)
    except ValueError:
        return 11434


def desktop_ollama_base_url() -> str:
    """OpenAI-compatible base URL for local Ollama.

    Legacy behaviour: returned a URL string used as OpenAI(base_url=...).
    """
    return f"http://{desktop_ollama_host()}:{desktop_ollama_port()}/v1"


def desktop_ollama_target() -> tuple[str, int]:
    """(host, port) tuple for socket availability check.

    Legacy behaviour: returned tuple used in socket.create_connection().
    """
    return (desktop_ollama_host(), desktop_ollama_port())


def is_local_llm_available(
    host: str | None = None,
    port: int | None = None,
    timeout: float = 0.5,
) -> bool:
    """Check if local Ollama is reachable via TCP.

    Legacy behaviour: socket.create_connection with 0.5s default timeout.
    Returns True if connection succeeds, False otherwise.
    """
    h = host or desktop_ollama_host()
    p = port or desktop_ollama_port()
    try:
        with socket.create_connection((h, p), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass
class ModelRoute:
    """Routing configuration for a single role.

    Matches the legacy local_model_routes dict structure:
      {role: {"primary": model_name, "fallback": [model_names]}}
    """
    primary: str
    fallback: list[str] = field(default_factory=list)


# Known role -> model mappings (from legacy bot_profiles_v3.py).
# These are the DEFAULTS. The actual profiles are built from
# bot_profiles_v3.py at runtime via build_swarm_profiles().
# This module only handles the LOCAL routing concern.

def get_model_routes() -> dict[str, dict[str, Any]]:
    """Build the model routes dict matching legacy format.

    Returns a dict: role -> {"primary": str, "fallback": list[str]}.
    Actual model names come from bot profiles, not hardcoded here.
    This provides the ROUTING STRUCTURE — the profiles provide the MODELS.
    """
    # Placeholder structure. Real routes are built from agent profiles
    # in the orchestrator layer. This function exists so the status()
    # aggregation has the right shape even with no routes configured.
    return {}
