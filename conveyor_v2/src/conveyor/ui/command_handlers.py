"""Command handlers — slash command parsing and dispatch.

Replaces the scattered if/elif chain in legacy app.py's on_message handler.
Uses the CommandDef registry pattern (from antigravity-awesome-skills spec).

This module is PURE — no UI framework imports, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class CommandDef:
    """A single slash command definition.

    All consumers (help text, autocomplete, UI menus) derive from
    this single source of truth — matching the antigravity-awesome-skills pattern.
    """
    name: str
    description: str
    handler: Callable[[str, dict[str, Any]], dict[str, Any]]
    usage: str = ""
    payload_keys: list[str] = field(default_factory=list)


@dataclass
class CommandResult:
    """Result from command dispatch."""
    handled: bool
    message: str = ""
    config_overrides: dict[str, Any] = field(default_factory=dict)


# -----------------------------------------------------------------------
# Command registry — declarative, single source of truth
# -----------------------------------------------------------------------

COMMAND_REGISTRY: list[CommandDef] = []


def register_command(cmd: CommandDef) -> None:
    """Register a command in the global registry."""
    COMMAND_REGISTRY.append(cmd)


def get_command(name: str) -> CommandDef | None:
    """Look up a command by name (without the leading /)."""
    key = name.lstrip("/").lower()
    for cmd in COMMAND_REGISTRY:
        if cmd.name.lower() == key:
            return cmd
    return None


def list_commands() -> list[CommandDef]:
    """Return all registered commands."""
    return list(COMMAND_REGISTRY)


# -----------------------------------------------------------------------
# Handlers
# -----------------------------------------------------------------------

def _handle_status(text: str, ctx: dict[str, Any]) -> dict[str, Any]:
    return {"action": "show_status"}


def _handle_testcmd(text: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """Parse: /testcmd <command>"""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return {"action": "error", "message": "Usage: /testcmd <command>"}
    return {"action": "set_test_command", "command": parts[1].strip()}


def _handle_memory(text: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """Parse: /memory <default|fast|deep|off>"""
    parts = text.split(maxsplit=1)
    valid = {"default", "fast", "deep", "off"}
    if len(parts) < 2 or parts[1].strip().lower() not in valid:
        return {"action": "error", "message": "Usage: /memory <default|fast|deep|off>"}
    profile = parts[1].strip().lower()
    overrides: dict[str, Any] = {}
    if profile == "fast":
        overrides["memory_distillation_enabled"] = True
        overrides["compaction_interval_waves"] = 2
        overrides["memory_rule_limit"] = 4
        overrides["memory_breadcrumb_limit"] = 3
    elif profile == "deep":
        overrides["memory_distillation_enabled"] = True
        overrides["compaction_interval_waves"] = 1
        overrides["memory_rule_limit"] = 10
        overrides["memory_breadcrumb_limit"] = 8
    elif profile == "off":
        overrides["memory_distillation_enabled"] = False
    return {"action": "set_memory_profile", "profile": profile, "overrides": overrides}


def _handle_adaptive(text: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """Parse: /adaptive <on|off>"""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or parts[1].strip().lower() not in ("on", "off"):
        return {"action": "error", "message": "Usage: /adaptive <on|off>"}
    enabled = parts[1].strip().lower() == "on"
    return {"action": "set_adaptive_compaction", "enabled": enabled,
            "overrides": {"adaptive_compaction_enabled": enabled}}


def _handle_recap(text: str, ctx: dict[str, Any]) -> dict[str, Any]:
    return {"action": "show_recap"}


def _handle_health(text: str, ctx: dict[str, Any]) -> dict[str, Any]:
    return {"action": "show_health"}


def _handle_console(text: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """Non-recognized slash command — belongs on the command console."""
    return {"action": "reject", "message": (
        "That command belongs on the swarm command console. "
        "Use the console for launches, approvals, rehearsals, and background work; "
        "this local chat lane is read-only for inspection."
    )}


def _handle_filesystem_request(text: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """Detect folder/file creation requests from natural language.

    Legacy behaviour: extracted from _parse_filesystem_request() in app.py.
    Simple heuristic — not a general filesystem API.
    """
    import re
    lowered = text.lower()
    keywords = ("folder", "directory", "file")
    if not any(kw in lowered for kw in keywords):
        return {}

    folder_match = re.search(r"(?:called|named)\s+([A-Za-z0-9_\-]+)", text, re.IGNORECASE)
    if not folder_match:
        return {}

    folder_name = folder_match.group(1)
    scope = "repo_root" if any(s in lowered for s in ("above modulars", "parent of modulars")) else "project_root"

    if "javascript" in lowered or lowered.endswith(".js"):
        file_name, content = "hello.js", 'const helloWorld = "Hello, world!";\nconsole.log(helloWorld);\n'
    elif "typescript" in lowered or lowered.endswith(".ts"):
        file_name, content = "hello.ts", 'const helloWorld: string = "Hello, world!";\nconsole.log(helloWorld);\n'
    else:
        file_name, content = "hello.txt", "Hello, world!\n"

    if "hello world" not in lowered and "hello-world" not in lowered:
        content = content.replace("Hello, world!", "Ready to help.")

    return {
        "action": "filesystem_request",
        "folder_name": folder_name,
        "scope": scope,
        "files": [{"name": file_name, "content": content}],
    }


# -----------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------

def _build_registry() -> None:
    """Populate the global COMMAND_REGISTRY."""
    COMMAND_REGISTRY.clear()

    register_command(CommandDef(
        name="status",
        description="Show swarm status",
        handler=_handle_status,
    ))
    register_command(CommandDef(
        name="testcmd",
        description="Set the test command: /testcmd <command>",
        handler=_handle_testcmd,
        usage="/testcmd python -m pytest -q",
    ))
    register_command(CommandDef(
        name="memory",
        description="Set memory profile: /memory <default|fast|deep|off>",
        handler=_handle_memory,
        usage="/memory fast",
    ))
    register_command(CommandDef(
        name="adaptive",
        description="Toggle adaptive compaction: /adaptive <on|off>",
        handler=_handle_adaptive,
        usage="/adaptive on",
    ))
    register_command(CommandDef(
        name="recap",
        description="Summarize recent chat history",
        handler=_handle_recap,
    ))
    register_command(CommandDef(
        name="health",
        description="Show swarm health summary",
        handler=_handle_health,
    ))


# -----------------------------------------------------------------------
# Public dispatch
# -----------------------------------------------------------------------

def dispatch_command(text: str, ctx: dict[str, Any] | None = None) -> CommandResult:
    """Dispatch a slash command and return the result.

    This is the single entry point. It:
    1. Builds the registry (idempotent) on first call.
    2. Detects if the text is a slash command.
    3. Looks up the command in the registry.
    4. Calls the handler or returns a rejection.

    Args:
        text: The raw user message text.
        ctx: Optional context dict (e.g., current config).

    Returns:
        CommandResult describing what should happen next.
    """
    if COMMAND_REGISTRY:
        _ensure_built()
    else:
        _build_registry()

    ctx = ctx or {}

    # Check for filesystem request before slash commands
    fs_result = _handle_filesystem_request(text, ctx)
    if fs_result:
        return CommandResult(
            handled=True,
            message=(
                "That looks like a file or folder change request. "
                "Use the swarm command console to queue filesystem work; "
                "this local chat lane is read-only for inspection."
            ),
        )

    normalized = text.strip().lower()
    if not normalized.startswith("/"):
        return CommandResult(handled=False)  # Not a command — normal chat

    # Strip the slash
    cmd_text = normalized.lstrip("/")

    # Find the command
    # Command names are the first token (e.g., "testcmd" from "/testcmd python -m pytest")
    parts = cmd_text.split(maxsplit=1)
    cmd_name = parts[0]
    cmd = get_command(cmd_name)

    if cmd is None:
        # Known command not found → reject as console command
        return CommandResult(
            handled=True,
            message=(
                "That command belongs on the swarm command console. "
                "Use the console for launches, approvals, rehearsals, and background work; "
                "this local chat lane is read-only for inspection."
            ),
        )

    # Execute the handler
    try:
        handler_result = cmd.handler(text, ctx)
    except Exception as exc:
        return CommandResult(handled=True, message=f"Command error: {exc}")

    action = handler_result.get("action", "")

    if action == "error":
        return CommandResult(handled=True, message=handler_result.get("message", "Unknown error"))

    if action == "reject":
        return CommandResult(handled=True, message=handler_result.get("message", ""))

    # Success — return any config overrides
    return CommandResult(
        handled=True,
        message=_action_message(action, handler_result),
        config_overrides=handler_result.get("overrides", {}),
    )


def _action_message(action: str, result: dict[str, Any]) -> str:
    """Generate a human-readable confirmation message for the action."""
    messages = {
        "show_status": "Showing swarm status...",
        "set_test_command": f"Test command updated: {result.get('command', '')}",
        "set_memory_profile": f"Memory profile set to: {result.get('profile', '')}",
        "set_adaptive_compaction": (
            f"Adaptive compaction {'enabled' if result.get('enabled') else 'disabled'}"
        ),
        "show_recap": "Generating chat recap...",
        "show_health": "Showing swarm health...",
    }
    return messages.get(action, f"Command processed: {action}")


def _ensure_built() -> None:
    """Ensure registry is built. Idempotent — called once."""
    # Registry is already built by _build_registry which was called
    # during _build_registry's first invocation via dispatch_command.
    # This is a no-op guard for safety.
    pass
