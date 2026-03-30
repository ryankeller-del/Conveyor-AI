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
        "Prioritize official docs, Unity 6 specifics, and runnable code examples. "
        "Return compact implementation facts for engineers."
    )

    coder_prompt = (
        f"You are LocalCoder for {stack}. "
        "Output only production-ready code with robust error handling. "
        "No markdown fences. "
        "Follow PEP8 for Python and standard C# conventions for C#."
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
