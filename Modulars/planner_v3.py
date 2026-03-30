import json
from dataclasses import dataclass, field
from typing import List


@dataclass
class TaskPlan:
    route: str
    research_query: str
    coding_task: str
    context_keywords: List[str] = field(default_factory=list)
    skip_research: bool = False


def _infer_route(user_prompt: str) -> str:
    lowered = user_prompt.lower()
    revise_markers = ["fix", "revise", "update", "change", "refactor", "patch"]
    if any(marker in lowered for marker in revise_markers):
        return "REVISE"
    chat_markers = ["hello", "thanks", "thank you", "how are you", "ok"]
    if any(marker in lowered for marker in chat_markers):
        return "CHAT"
    return "NEW"


def parse_task_plan(raw_text: str, user_prompt: str) -> TaskPlan:
    default_route = _infer_route(user_prompt)
    default_plan = TaskPlan(
        route=default_route,
        research_query=user_prompt,
        coding_task=user_prompt,
        context_keywords=[],
        skip_research=(default_route == "REVISE"),
    )

    try:
        payload = json.loads(raw_text)
        route = str(payload.get("route", default_plan.route)).upper().strip()
        if route not in {"NEW", "REVISE", "CHAT"}:
            route = default_plan.route

        context_keywords = payload.get("context_keywords", [])
        if not isinstance(context_keywords, list):
            context_keywords = []

        return TaskPlan(
            route=route,
            research_query=str(payload.get("research_query", default_plan.research_query)),
            coding_task=str(payload.get("coding_task", default_plan.coding_task)),
            context_keywords=[str(keyword) for keyword in context_keywords][:8],
            skip_research=bool(payload.get("skip_research", route == "REVISE")),
        )
    except Exception:
        return default_plan
