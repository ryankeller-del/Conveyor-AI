"""Specialist bot profile definitions for Conveyor v2.

Replaces legacy bot_profiles_v3.py. Extracts the 12 agent profiles
with their exact system prompts, model names, and fallback chains.

Agent roles map to SimpleAgent instances created by the controller.
Model names are configurable at runtime via the profiles dict.
"""

from __future__ import annotations

from conveyor.core.types import BotProfile


def build_swarm_profiles() -> dict[str, BotProfile]:
    """Build 12 specialist agent profiles.

    Returns a dict mapping role name -> BotProfile.
    Each profile has: name, model, fallback_models, system_prompt.

    Test agent uses Groq as primary (no local fallback).
    All other agents use local Ollama as primary with OpenRouter fallback.
    """
    # Default local model for roles that don't specify their own
    local_default = "qwen2.5:14b"

    # Test bot: uses Groq, not local
    test_profile = BotProfile(
        name="TestBot",
        model="llama-3.1-8b-instant",  # Groq model
        fallback_models=[
            "openrouter/free",
            "meta-llama/llama-3.1-8b-instruct:free",
        ],
        system_prompt=(
            "Generate deterministic project tests in JSON with name/body fields. "
            "Prioritize boundaries, failures, and regressions."
        ),
    )

    coder_profile = BotProfile(
        name="LocalCoder",
        model="qwen2.5-coder:14b",
        fallback_models=["openrouter/free"],
        system_prompt=(
            "Write efficient production code that passes given tests. "
            "Return raw code only."
        ),
    )

    judge_profile = BotProfile(
        name="JudgeBot",
        model=local_default,
        fallback_models=["openrouter/free"],
        system_prompt=(
            "Summarize test failures into specific fix steps and likely root cause."
        ),
    )

    chat_profile = BotProfile(
        name="LocalChatBot",
        model=local_default,
        fallback_models=["openrouter/free"],
        system_prompt=(
            "You are LocalChatBot, the user's priority local assistant for this swarm. "
            "Return JSON only with keys reply, background_instruction, swarm_health, and mode. "
            "Mode will be one of chat, health, or architect. "
            "If mode is chat, answer conversationally and keep the reply short. "
            "If mode is health, summarize swarm health, blockers, and the next useful action. "
            "If mode is architect, provide a concise instruction for the main swarm architect "
            "and a short user-facing reply. "
            "Keep background_instruction compact and actionable."
        ),
        fallback_client_models=["openrouter/free"],
    )

    context_guard_profile = BotProfile(
        name="ContextGuardBot",
        model=local_default,
        fallback_models=["openrouter/free"],
        system_prompt=(
            "You are a high-level prompt strategist. "
            "Refactor complex prompts into concise, staged, low-ambiguity instructions. "
            "When failure context is provided, prioritize preventing repeated mistakes."
        ),
    )

    pattern_profile = BotProfile(
        name="PatternFinder",
        model=local_default,
        fallback_models=["openrouter/free"],
        system_prompt=(
            "Analyze failures and progress logs, then extract compact recurring engineering rules."
        ),
    )

    compression_profile = BotProfile(
        name="CompressionBot",
        model=local_default,
        fallback_models=["openrouter/free"],
        system_prompt=(
            "Compress context into high-signal memory with minimal token overhead."
        ),
    )

    novelty_profile = BotProfile(
        name="NoveltyBot",
        model=local_default,
        fallback_models=["openrouter/free"],
        system_prompt=(
            "Propose novel but practical architecture or prompt breadcrumbs to avoid stagnation."
        ),
    )

    stability_guard_profile = BotProfile(
        name="StabilityGuardBot",
        model=local_default,
        fallback_models=["openrouter/free"],
        system_prompt=(
            "You are a swarm stability controller. Detect collapse trends and return concise "
            "stabilization objectives that reduce retries, open handoffs, and agent churn."
        ),
    )

    seed_prep_profile = BotProfile(
        name="SeedPrepBot",
        model=local_default,
        fallback_models=["openrouter/free"],
        system_prompt=(
            "You are an advisory preflight planner. "
            "Return JSON only with title, suggested_action, expected_benefit, risk_if_wrong, "
            "validation_plan, config_overrides, requested_tools, and requested_updates. "
            "Do not write code. Focus on compact seed data for analyzers and testing grounds."
        ),
    )

    directive_prep_profile = BotProfile(
        name="DirectivePrepBot",
        model=local_default,
        fallback_models=["openrouter/free"],
        system_prompt=(
            "You are an advisory preflight planner. "
            "Return JSON only with title, suggested_action, expected_benefit, risk_if_wrong, "
            "validation_plan, config_overrides, requested_tools, and requested_updates. "
            "Do not write code. Focus on concise directives, approval gating, and need-to-know handoffs."
        ),
    )

    stability_prep_profile = BotProfile(
        name="StabilityPrepBot",
        model=local_default,
        fallback_models=["openrouter/free"],
        system_prompt=(
            "You are an advisory preflight planner. "
            "Return JSON only with title, suggested_action, expected_benefit, risk_if_wrong, "
            "validation_plan, config_overrides, requested_tools, and requested_updates. "
            "Do not write code. Focus on collapse prevention, population control, and launch safety."
        ),
    )

    return {
        "test": test_profile,
        "coder": coder_profile,
        "judge": judge_profile,
        "chat": chat_profile,
        "context_guard": context_guard_profile,
        "pattern_finder": pattern_profile,
        "compression": compression_profile,
        "novelty": novelty_profile,
        "stability_guard": stability_guard_profile,
        "seed_prep": seed_prep_profile,
        "directive_prep": directive_prep_profile,
        "stability_prep": stability_prep_profile,
    }
