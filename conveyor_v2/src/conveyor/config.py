"""Centralised configuration loader for Conveyor v2.

Precedence (highest to lowest):
  1. Environment variables (CONVEYOR_*)
  2. YAML config file
  3. Code defaults in RunConfig

Legacy source: _parse_config() in app.py + load_dotenv().
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc]


def get_conveyor_home() -> Path:
    """Return the base directory for Conveyor data.

    Respects CONVEYOR_HOME env var. Falls back to ~/.conveyor.
    Never hardcodes — profile-safe path resolution.
    """
    env_home = os.environ.get("CONVEYOR_HOME")
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / ".conveyor"


def _load_env_file(env_path: Path | None = None) -> None:
    """Load .env file if present. Does not overwrite existing env vars."""
    path = env_path or Path.cwd() / ".env"
    if path.is_file() and load_dotenv is not None:
        load_dotenv(dotenv_path=path, override=False)


def _parse_yaml(path: Path) -> dict[str, Any]:
    """Parse YAML config file. Returns empty dict if file missing."""
    if not path.is_file():
        return {}
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {}


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load full configuration with precedence applied.

    Args:
        config_path: Optional path to config YAML.
                     Auto-detected if not provided.

    Returns:
        Merged configuration dict with env vars taking precedence.
    """
    _load_env_file()

    # Detect YAML config
    if config_path is None:
        candidates = [
            Path.cwd() / "config" / "default_config.yaml",
            Path.cwd() / "config.yaml",
            get_conveyor_home() / "config.yaml",
        ]
        for c in candidates:
            if c.is_file():
                config_path = c
                break

    yaml_cfg = _parse_yaml(config_path) if config_path else {}

    # Environment variable overrides
    prefix = "CONVEYOR_"
    env_overrides = {
        k[len(prefix):].lower(): v
        for k, v in os.environ.items()
        if k.startswith(prefix)
    }

    # Merge: YAML < env
    merged: dict[str, Any] = {**yaml_cfg}
    merged.update(env_overrides)
    return merged


__all__ = ["load_config", "get_conveyor_home"]
