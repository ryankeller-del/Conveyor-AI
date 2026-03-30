from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class BotProfile:
    name: str
    model: str
    system_prompt: str
    fallback_models: List[str] = field(default_factory=list)


def build_profiles(stack: str) -> Dict[str, BotProfile]:
    planner_prompt = (
        "You are ScoutBot, a high-speed planning router. "
        "Return JSON only with keys: route, research_query, coding_task, "
        "context_keywords, skip_research. "
        "route must be one of NEW, REVISE, CHAT. "
        "Optimize research_query for free OpenRouter-compatible search prompts. "
        "Use concise technical language. "
        "Do not ask follow-up questions."
    )

    loader_prompt = (
        "You are LoaderBot, a deep-dive technical data harvester. "
        "Prioritize official docs, runnable code examples, and implementation details. "
        "Return compact implementation facts for engineers."
    )

    coder_prompt = (
        f"You are LocalCoder for {stack}. "
        "Output only production-ready code with robust error handling. "
        "No markdown fences. "
        "Follow the dominant conventions already present in the project."
    )

    compactor_prompt = (
        "You are CompactorBot. Compress technical context while preserving "
        "facts needed for implementation."
    )

    return {
        "scout": BotProfile(
            name="ScoutBot",
            model="openrouter/free",
            fallback_models=[
                "meta-llama/llama-3.1-8b-instruct:free",
                "google/gemma-2-9b-it:free",
            ],
            system_prompt=planner_prompt,
        ),
        "loader": BotProfile(
            name="LoaderBot",
            model="openrouter/free",
            fallback_models=[
                "meta-llama/llama-3.1-70b-instruct:free",
                "meta-llama/llama-3.1-8b-instruct:free",
            ],
            system_prompt=loader_prompt,
        ),
        "coder": BotProfile(
            name="LocalCoder",
            model="qwen2.5-coder:14b",
            system_prompt=coder_prompt,
        ),
        "compactor": BotProfile(
            name="CompactorBot",
            model="openrouter/free",
            fallback_models=["meta-llama/llama-3.1-8b-instruct:free"],
            system_prompt=compactor_prompt,
        ),
    }


def build_swarm_profiles() -> Dict[str, BotProfile]:
    coder_local_fallbacks = [
        "qwen2.5-coder:7b",
        "qwen2.5-coder:3b",
        "gpt-oss:20b",
    ]
    reasoning_local_fallbacks = [
        "gpt-oss:20b",
        "phi4:latest",
        "qwen2.5-coder:7b",
    ]
    chat_local_fallbacks = [
        "phi4:latest",
        "qwen2.5-coder:7b",
        "qwen2.5-coder:3b",
    ]
    return {
        "test": BotProfile(
            name="TestBot",
            model="llama-3.1-8b-instant",
            fallback_models=[],
            system_prompt=(
                "Generate deterministic project tests in JSON with name/body fields. "
                "Prioritize boundaries, failures, and regressions."
            ),
        ),
        "coder": BotProfile(
            name="LocalCoder",
            model="qwen2.5-coder:14b",
            fallback_models=list(coder_local_fallbacks),
            system_prompt=(
                "Write efficient production code that passes given tests. "
                "Return raw code only."
            ),
        ),
        "judge": BotProfile(
            name="JudgeBot",
            model="qwen2.5-coder:14b",
            fallback_models=list(reasoning_local_fallbacks),
            system_prompt=(
                "Summarize test failures into specific fix steps and likely root cause."
            ),
        ),
        "chat": BotProfile(
            name="LocalChatBot",
            model="gpt-oss:20b",
            fallback_models=list(chat_local_fallbacks),
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
        ),
        "context_guard": BotProfile(
            name="ContextGuardBot",
            model="qwen2.5-coder:14b",
            fallback_models=list(reasoning_local_fallbacks),
            system_prompt=(
                "You are a high-level prompt strategist. "
                "Refactor complex prompts into concise, staged, low-ambiguity instructions. "
                "When failure context is provided, prioritize preventing repeated mistakes."
            ),
        ),
        "pattern_finder": BotProfile(
            name="PatternFinder",
            model="qwen2.5-coder:14b",
            fallback_models=list(reasoning_local_fallbacks),
            system_prompt=(
                "Analyze failures and progress logs, then extract compact recurring engineering rules."
            ),
        ),
        "compression": BotProfile(
            name="CompressionBot",
            model="qwen2.5-coder:14b",
            fallback_models=list(reasoning_local_fallbacks),
            system_prompt=(
                "Compress context into high-signal memory with minimal token overhead."
            ),
        ),
        "novelty": BotProfile(
            name="NoveltyBot",
            model="qwen2.5-coder:14b",
            fallback_models=list(reasoning_local_fallbacks),
            system_prompt=(
                "Propose novel but practical architecture or prompt breadcrumbs to avoid stagnation."
            ),
        ),
        "stability_guard": BotProfile(
            name="StabilityGuardBot",
            model="qwen2.5-coder:14b",
            fallback_models=list(reasoning_local_fallbacks),
            system_prompt=(
                "You are a swarm stability controller. Detect collapse trends and return concise "
                "stabilization objectives that reduce retries, open handoffs, and agent churn."
            ),
        ),
        "seed_prep": BotProfile(
            name="SeedPrepBot",
            model="qwen2.5-coder:14b",
            fallback_models=list(reasoning_local_fallbacks),
            system_prompt=(
                "You are an advisory preflight planner. "
                "Return JSON only with title, suggested_action, expected_benefit, risk_if_wrong, "
                "validation_plan, config_overrides, requested_tools, and requested_updates. "
                "Do not write code. Focus on compact seed data for analyzers and testing grounds."
            ),
        ),
        "directive_prep": BotProfile(
            name="DirectivePrepBot",
            model="qwen2.5-coder:14b",
            fallback_models=list(reasoning_local_fallbacks),
            system_prompt=(
                "You are an advisory preflight planner. "
                "Return JSON only with title, suggested_action, expected_benefit, risk_if_wrong, "
                "validation_plan, config_overrides, requested_tools, and requested_updates. "
                "Do not write code. Focus on concise directives, approval gating, and need-to-know handoffs."
            ),
        ),
        "stability_prep": BotProfile(
            name="StabilityPrepBot",
            model="qwen2.5-coder:14b",
            fallback_models=list(reasoning_local_fallbacks),
            system_prompt=(
                "You are an advisory preflight planner. "
                "Return JSON only with title, suggested_action, expected_benefit, risk_if_wrong, "
                "validation_plan, config_overrides, requested_tools, and requested_updates. "
                "Do not write code. Focus on collapse prevention, population control, and launch safety."
            ),
        ),
    }
