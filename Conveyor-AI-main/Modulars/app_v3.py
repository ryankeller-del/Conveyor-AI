import os

import chainlit as cl
from dotenv import load_dotenv
from openai import OpenAI

from bot_profiles_v3 import build_profiles
from modular_belt import Agent, ContextBelt, Orchestrator
from planner_v3 import parse_task_plan

load_dotenv()

OUTPUT_FILENAME = os.getenv("CODEX_V3_OUTPUT", "app_v3.py")


def discover_stack():
    files = os.listdir(".")
    exts = {os.path.splitext(file_name)[1].lower() for file_name in files}
    if ".cs" in exts:
        return "Unity/C#"
    if ".py" in exts:
        return "Python"
    if ".js" in exts:
        return "Web/JS"
    return "General Tech"


def _build_agent(profile, client):
    return Agent(
        name=profile.name,
        client=client,
        model=profile.model,
        fallback_models=profile.fallback_models,
        system_prompt=profile.system_prompt,
    )


@cl.on_chat_start
async def start():
    stack = discover_stack()
    profiles = build_profiles(stack)

    openrouter_client = OpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
    )
    local_client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

    scout = _build_agent(profiles["scout"], openrouter_client)
    loader = _build_agent(profiles["loader"], openrouter_client)
    writer = _build_agent(profiles["coder"], local_client)
    compactor = _build_agent(profiles["compactor"], openrouter_client)

    belt = ContextBelt(max_size=6, compactor_agent=compactor)
    manager = Orchestrator(belt=belt, loader_agent=loader, local_writer_agent=writer)
    manager.reindex_active_agents([scout, loader, writer, compactor])

    cl.user_session.set("scout", scout)
    cl.user_session.set("manager", manager)
    cl.user_session.set("stack", stack)

    await cl.Message(
        content=(
            "Project: Codex V3 online. Free-model routing enabled. "
            f"Coder target file: {OUTPUT_FILENAME}"
        )
    ).send()


@cl.on_message
async def main(message: cl.Message):
    scout = cl.user_session.get("scout")
    manager = cl.user_session.get("manager")

    async with cl.Step(name="Plan") as plan_step:
        raw_plan = await cl.make_async(scout.generate)(message.content)
        task_plan = parse_task_plan(raw_plan, message.content)
        plan_step.output = (
            f"route={task_plan.route}, skip_research={task_plan.skip_research}, "
            f"keywords={task_plan.context_keywords}"
        )

    if task_plan.route == "CHAT":
        await cl.Message(
            content="Standing by. Send a build/change request when ready."
        ).send()
        return

    research_bytes = 0
    if not task_plan.skip_research:
        async with cl.Step(name="Research") as research_step:
            research_result = await cl.make_async(manager.execute_research_task)(
                task_plan.research_query
            )
            research_bytes = len(research_result or "")
            research_step.output = (
                f"Research complete with {research_bytes} chars. "
                f"query={task_plan.research_query}"
            )

    async with cl.Step(name="Code") as code_step:
        saved_path = await cl.make_async(manager.write_local_code_scoped)(
            OUTPUT_FILENAME,
            task_plan.coding_task,
            task_plan.context_keywords,
            3500,
        )
        code_step.output = f"Wrote code to {saved_path}"

    elements = [cl.File(name=os.path.basename(saved_path), path=saved_path, display="inline")]
    await cl.Message(
        content=(
            "V3 pipeline complete. "
            f"Route={task_plan.route}. Research bytes={research_bytes}. "
            f"Output={saved_path}"
        ),
        elements=elements,
    ).send()
