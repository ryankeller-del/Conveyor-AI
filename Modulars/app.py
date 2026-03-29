import chainlit as cl
import os
from dotenv import load_dotenv
from modular_belt import Agent, ContextBelt, Orchestrator 
from openai import OpenAI

# Load the keys from the .env vault
load_dotenv()

# --- Setup Clients ---
# Notice how we use os.getenv() now instead of pasting the string
openrouter_client = OpenAI(api_key=os.getenv("OPENROUTER_API_KEY"), base_url="https://openrouter.ai/api/v1")
groq_client = OpenAI(api_key=os.getenv("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1")
local_client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

@cl.on_chat_start
async def start():
    # Use your GLM model name here
    loader = Agent(
    name="LoaderBot", 
    client=openrouter_client, 
    model="openrouter/free", 
    system_prompt="You are a data extraction engine. Output ONLY raw technical facts. Never ask follow-up questions."
)
    compactor = Agent(name="CompactorBot", client=groq_client, model="llama-3.1-8b-instant", system_prompt="Data Archivist.")
    writer = Agent(name="LocalCoder", client=local_client, model="qwen2.5-coder:14b", system_prompt="Senior Unity Dev. Output ONLY C# code. No backticks.")

    belt = ContextBelt(max_size=3, compactor_agent=compactor)
    manager = Orchestrator(belt=belt, loader_agent=loader, local_writer_agent=writer)
    
    cl.user_session.set("manager", manager)
    await cl.Message(content="🏗️ **Unity Factory Online.** What are we building?").send()

@cl.on_message
async def main(message: cl.Message):
    manager = cl.user_session.get("manager")
    
    # THE FIX: Wrap the sync manager calls in 'make_async' and AWAIT them
    async with cl.Step(name="LoaderBot Research") as step:
        await cl.make_async(manager.execute_research_task)(message.content)
        step.output = manager.belt.belt[0]

    async with cl.Step(name="LocalCoder Generation") as step:
        filename = "UnityGeneratedScript.cs"
        # We await the offloaded task so the UI stays alive
        await cl.make_async(manager.write_local_code)(filename, message.content)
        step.output = f"C# Code written to `{filename}`"

    await cl.Message(content=f"✅ Task complete! Check your folder.").send()