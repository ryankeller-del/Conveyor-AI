from dataclasses import dataclass, field
from typing import Dict, List

from swarm_core.local_models import build_desktop_local_routes


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
    local_routes = build_desktop_local_routes()
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
            model=str(local_routes["coder"]["model"]),
            fallback_models=list(local_routes["coder"]["fallback_models"]),
            system_prompt=(
                "Write efficient production code that passes given tests. "
                "Return raw code only."
            ),
        ),
        "judge": BotProfile(
            name="JudgeBot",
            model=str(local_routes["judge"]["model"]),
            fallback_models=list(local_routes["judge"]["fallback_models"]),
            system_prompt=(
                "Summarize test failures into specific fix steps and likely root cause."
            ),
        ),
        "chat": BotProfile(
            name="LocalChatBot",
            model=str(local_routes["chat"]["model"]),
            fallback_models=list(local_routes["chat"]["fallback_models"]),
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
            model=str(local_routes["context_guard"]["model"]),
            fallback_models=list(local_routes["context_guard"]["fallback_models"]),
            system_prompt=(
                "You are a high-level prompt strategist. "
                "Refactor complex prompts into concise, staged, low-ambiguity instructions. "
                "When failure context is provided, prioritize preventing repeated mistakes."
            ),
        ),
        "pattern_finder": BotProfile(
            name="PatternFinder",
            model=str(local_routes["pattern_finder"]["model"]),
            fallback_models=list(local_routes["pattern_finder"]["fallback_models"]),
            system_prompt=(
                "Analyze failures and progress logs, then extract compact recurring engineering rules."
            ),
        ),
        "compression": BotProfile(
            name="CompressionBot",
            model=str(local_routes["compression"]["model"]),
            fallback_models=list(local_routes["compression"]["fallback_models"]),
            system_prompt=(
                "Compress context into high-signal memory with minimal token overhead."
            ),
        ),
        "novelty": BotProfile(
            name="NoveltyBot",
            model=str(local_routes["novelty"]["model"]),
            fallback_models=list(local_routes["novelty"]["fallback_models"]),
            system_prompt=(
                "Propose novel but practical architecture or prompt breadcrumbs to avoid stagnation."
            ),
        ),
        "stability_guard": BotProfile(
            name="StabilityGuardBot",
            model=str(local_routes["stability_guard"]["model"]),
            fallback_models=list(local_routes["stability_guard"]["fallback_models"]),
            system_prompt=(
                "You are a swarm stability controller. Detect collapse trends and return concise "
                "stabilization objectives that reduce retries, open handoffs, and agent churn."
            ),
        ),
        "seed_prep": BotProfile(
            name="SeedPrepBot",
            model=str(local_routes["seed_prep"]["model"]),
            fallback_models=list(local_routes["seed_prep"]["fallback_models"]),
            system_prompt=(
                "You are an advisory preflight planner. "
                "Return JSON only with title, suggested_action, expected_benefit, risk_if_wrong, "
                "validation_plan, config_overrides, requested_tools, and requested_updates. "
                "Do not write code. Focus on compact seed data for analyzers and testing grounds."
            ),
        ),
        "directive_prep": BotProfile(
            name="DirectivePrepBot",
            model=str(local_routes["directive_prep"]["model"]),
            fallback_models=list(local_routes["directive_prep"]["fallback_models"]),
            system_prompt=(
                "You are an advisory preflight planner. "
                "Return JSON only with title, suggested_action, expected_benefit, risk_if_wrong, "
                "validation_plan, config_overrides, requested_tools, and requested_updates. "
                "Do not write code. Focus on concise directives, approval gating, and need-to-know handoffs."
            ),
        ),
        "stability_prep": BotProfile(
            name="StabilityPrepBot",
            model=str(local_routes["stability_prep"]["model"]),
            fallback_models=list(local_routes["stability_prep"]["fallback_models"]),
            system_prompt=(
                "You are an advisory preflight planner. "
                "Return JSON only with title, suggested_action, expected_benefit, risk_if_wrong, "
                "validation_plan, config_overrides, requested_tools, and requested_updates. "
                "Do not write code. Focus on collapse prevention, population control, and launch safety."
            ),
        ),
    }
