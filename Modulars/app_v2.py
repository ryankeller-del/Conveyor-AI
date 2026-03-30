import os
import sys

import chainlit as cl
from dotenv import load_dotenv
from openai import OpenAI

from modular_belt import Agent, ContextBelt, Orchestrator

load_dotenv()

port = 8000
if "--port" in sys.argv:
    try:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    except (ValueError, IndexError):
        pass

openrouter_client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)
groq_client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
)
local_client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")


@cl.on_chat_start
async def start():
    scout = Agent(
        name="ScoutBot",
        client=groq_client,
        model="llama-3.1-8b-instant",
        system_prompt=(
            "High-Speed Strategic Planner. "
            "Refine user requests into concise, execution-ready technical queries "
            "optimized for openrouter/free routing and rapid downstream research. "
            "Prioritize speed, precision, and actionable command phrasing. "
            "Output only the research command and execute-oriented query text. "
            "Do not ask follow-up questions."
        ),
    )

    loader = Agent(
        name="LoaderBot",
        client=openrouter_client,
        model="mistralai/mistral-large-latest",
        fallback_models=["meta-llama/llama-3.1-70b-instruct"],
        system_prompt=(
            "Deep-Dive Technical Data Harvester. "
            "Prioritize official documentation and runnable examples. "
            "Deliver concise, high-signal technical findings with implementation-ready details."
        ),
    )

    writer = Agent(
        name="LocalCoder",
        client=local_client,
        model="qwen2.5-coder:14b",
        system_prompt=(
            "High-Performance Qwen 2.5-Coder (14b). "
            "Output ONLY clean, efficient, production-ready code. "
            "Never include markdown backticks. "
            "Include robust error handling. "
            "Follow established conventions in the existing repository."
        ),
    )

    compactor = Agent(
        name="Compactor",
        client=groq_client,
        model="llama-3.1-8b-instant",
        system_prompt="Compress technical facts while preserving implementation detail.",
    )

    belt = ContextBelt(max_size=5, compactor_agent=compactor)
    manager = Orchestrator(belt=belt, loader_agent=loader, local_writer_agent=writer)

    manager.reindex_active_agents([scout, loader, writer, compactor])

    cl.user_session.set("manager", manager)
    cl.user_session.set("scout", scout)
    cl.user_session.set("project_status", "Project: Codex")

    await cl.Message(
        content=(
            "Initiate Codex Activation Protocol (Apx: 3.14). "
            "Re-index all active agents immediately.\n\n"
            "Project: Codex\n"
            "\U0001F6F0\ufe0f Codex Online. Systems optimized for high-speed local generation."
        )
    ).send()


@cl.on_message
async def main(message: cl.Message):
    manager = cl.user_session.get("manager")
    scout = cl.user_session.get("scout")

    async with cl.Step(name="Scouting and Research") as step:
        refined_query = await cl.make_async(scout.generate)(message.content)
        research_result = await cl.make_async(manager.execute_research_task)(refined_query)

        filename = "GeneratedOutput.txt"
        saved_path = await cl.make_async(manager.write_local_code)(filename, message.content)
        step.output = (
            f"Research command executed instantly.\n"
            f"Refined query: {refined_query}\n"
            f"Research bytes captured: {len(research_result)}\n"
            f"Code materialized at: {saved_path}"
        )

    elements = [cl.File(name=filename, path=saved_path, display="inline")]
    await cl.Message(content="Task complete for Project: Codex.", elements=elements).send()
