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
            fallback_models=[],
            system_prompt=(
                "Write efficient production code that passes given tests. "
                "Return raw code only."
            ),
        ),
        "judge": BotProfile(
            name="JudgeBot",
            model="qwen2.5-coder:14b",
            fallback_models=[],
            system_prompt=(
                "Summarize test failures into specific fix steps and likely root cause."
            ),
        ),
    }
