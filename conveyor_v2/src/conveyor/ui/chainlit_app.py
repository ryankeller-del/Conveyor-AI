"""Chainlit UI — thin callback layer for Conveyor v2.

ONLY contains @cl decorators. Zero business logic.
All logic is delegated to SwarmController, status_formatter,
and command_handlers.

Legacy source: app.py contained ~800 lines of mixed callbacks,
command parsing, and string formatting. This file is target ~150 lines.
"""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from conveyor.core.types import RunConfig, TaskGoal
from conveyor.ui.command_handlers import dispatch_command
from conveyor.ui.status_formatter import format_status

# Chainlit is only available at runtime when running the UI
# No runtime import — all @cl decorators are dynamically loaded
# via the chainlit run entry point
_cl: Any = None


def _load_chainlit() -> Any:
    """Lazy load chainlit so this module can be imported without it."""
    global _cl
    if _cl is None:
        import chainlit as cl
        _cl = cl
    return _cl


def _get_session(key: str, default: Any = None) -> Any:
    """Get value from chainlit session."""
    cl = _load_chainlit()
    return cl.user_session.get(key, default)


def _set_session(key: str, value: Any) -> None:
    """Set value in chainlit session."""
    cl = _load_chainlit()
    cl.user_session.set(key, value)


async def _send_message(content: str) -> None:
    """Send a message via chainlit."""
    cl = _load_chainlit()
    await cl.Message(content=content).send()


# ---------------------------------------------------------------------------
# Chat start
# ---------------------------------------------------------------------------

def on_chat_start():
    """Initialize session when Chatlit creates a new chat session."""
    cl = _load_chainlit()

    # Build controller (imported here, not at module level, so the
    # module can be imported without chainlit)
    from conveyor.core.controller import SwarmController
    from conveyor.agents.profiles import build_swarm_profiles
    from conveyor.agents.agent import SimpleAgent
    from conveyor.models.local_runtime import is_local_llm_available, desktop_ollama_base_url
    from openai import OpenAI

    profiles = build_swarm_profiles()

    # Build clients
    groq_client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=cl.user_session.get("groq_api_key", ""),
    )
    openrouter_client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=cl.user_session.get("openrouter_api_key", ""),
    )
    local_available = is_local_llm_available()
    local_client = OpenAI(
        base_url=desktop_ollama_base_url(),
        api_key="ollama",
    )

    # Build agents (13 specialists)
    def _make_agent(profile, primary_client, fallback_client, is_local=False):
        return SimpleAgent(
            name=profile.name,
            model=profile.model,
            fallback_models=profile.fallback_models,
            system_prompt=profile.system_prompt,
            client=primary_client,
            fallback_client=fallback_client,
            is_local=is_local,
            fallback_client_models=profile.fallback_client_models,
        )

    # Test agent uses Groq as primary
    agents = {
        "test": _make_agent(profiles["test"], groq_client, openrouter_client),
    }

    # All others use local or OpenRouter
    for role in profiles:
        if role == "test" or role not in agents:
            agents[role] = _make_agent(
                profiles[role],
                local_client if local_available else openrouter_client,
                openrouter_client if not local_available else None,
                is_local=local_available,
            )

    # Build controller
    controller = SwarmController(
        agents=agents,
        root_dir=cl.user_session.get("root_dir", "."),
    )
    cl.user_session.set("swarm_controller", controller)
    cl.user_session.set("active_test_command", "python -m pytest {tests_path} -q")
    cl.user_session.set("run_config_overrides", {})

    # Import RollingConversation from chat_lane
    from conveyor.core.chat_lane import RollingConversation
    cl.user_session.set(
        "chat_transcript",
        RollingConversation(limit=8),
    )

    # Welcome message
    cl = _load_chainlit()
    cl.Message(
        content=(
            "Conveyor v2 online.\n"
            "Normal messages chat with the local bot. "
            "Use /status, /health, /recap for inspection. "
            "Use command console for launches, approvals, rehearsals."
        ),
    ).send()


# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------

async def on_message(message: Any):
    """Handle incoming chat messages."""
    cl = _load_chainlit()
    text = message.content.strip()
    controller = cl.user_session.get("swarm_controller")

    if not controller:
        await cl.Message(content="Controller not initialized. Restart the session.").send()
        return

    # Dispatch command
    config_overrides = cl.user_session.get("run_config_overrides", {})
    cmd_result = dispatch_command(text, {"overrides": config_overrides})

    if cmd_result.handled:
        # Apply config overrides
        if cmd_result.config_overrides:
            overrides = cl.user_session.get("run_config_overrides", {})
            overrides.update(cmd_result.config_overrides)
            cl.user_session.set("run_config_overrides", overrides)

        # Check for special actions
        action = "show_status"  # default for status
        if "show_status" in cmd_result.message.lower():
            await _send_status(controller)
            return

        await cl.Message(content=cmd_result.message).send()
        return

    # Not a command — handle as chat
    from conveyor.core.chat_lane import detect_chat_mode

    chat_mode = detect_chat_mode(text)
    transcript = cl.user_session.get("chat_transcript")

    # Build config with overrides
    config = RunConfig()
    overrides = cl.user_session.get("run_config_overrides", {})
    config = config.apply_overrides(overrides)

    # Get conversation context
    context = ""
    if transcript:
        context = transcript.recent_context(limit=config.chat_history_limit)
        transcript.append("user", text, message)

    # Run chat response via controller (thread-safe)
    result = await asyncio.to_thread(
        controller.respond_to_chat,
        text,
        config,
        chat_mode,
        context,
    )

    reply = result.get("reply", "I'm here.")
    assistant_msg = await cl.Message(content=reply).send()

    if transcript:
        transcript.append("assistant", reply, assistant_msg)
        await transcript.trim()


# ---------------------------------------------------------------------------
# Status helper
# ---------------------------------------------------------------------------

async def _send_status(controller: Any):
    """Send formatted swarm status to the UI."""
    cl = _load_chainlit()
    status = controller.status()
    formatted = format_status(status)
    await cl.Message(content=formatted).send()
