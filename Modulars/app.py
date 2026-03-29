import chainlit as cl
import os
from dotenv import load_dotenv
from modular_belt import Agent, ContextBelt, Orchestrator 
from openai import OpenAI

# 1. Securely load the keys from the .env vault
load_dotenv()

# --- Setup Clients ---
openrouter_client = OpenAI(api_key=os.getenv("OPENROUTER_API_KEY"), base_url="https://openrouter.ai/api/v1")
groq_client = OpenAI(api_key=os.getenv("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1")
local_client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

@cl.on_chat_start
async def start():
    # 2. The Traffic Cop (Fast Groq model)
    router_prompt = """You are a traffic controller for an AI factory. 
    Classify the user's prompt into exactly ONE of these three words:
    NEW - Asking to create a completely new script, feature, or concept.
    REVISE - Asking to change, fix, or update the code we just wrote.
    CHAT - Casual conversation (e.g., hello, thanks, ok).
    Output ONLY the single word. No punctuation."""
    router_bot = Agent(name="RouterBot", client=groq_client, model="llama-3.1-8b-instant", system_prompt=router_prompt)

    # 3. The Factory Workers
    loader = Agent(name="LoaderBot", client=openrouter_client, model="openrouter/free", system_prompt="Unity XR Research Agent. Raw facts only.")
    compactor = Agent(name="CompactorBot", client=groq_client, model="llama-3.1-8b-instant", system_prompt="Technical Architect. Bullet points only.")
    writer = Agent(name="LocalCoder", client=local_client, model="qwen2.5-coder:14b", system_prompt="Senior Unity Dev. Output ONLY C# code. No markdown backticks.")

    belt = ContextBelt(max_size=3, compactor_agent=compactor)
    manager = Orchestrator(belt=belt, loader_agent=loader, local_writer_agent=writer)
    
    cl.user_session.set("manager", manager)
    cl.user_session.set("router", router_bot)
    
    await cl.Message(content="🚦 **Smart Factory Online.** I will now route your requests automatically.").send()

@cl.on_message
async def main(message: cl.Message):
    manager = cl.user_session.get("manager")
    router = cl.user_session.get("router")
    
    # --- ROUTING DECISION ---
    decision = await cl.make_async(router.generate)(message.content)
    route = decision.strip().upper()
    
    if "NEW" in route:
        async with cl.Step(name="🚦 Route: NEW TASK") as step:
            await cl.make_async(manager.execute_research_task)(message.content)
            filename = "UnityGeneratedScript.cs"
            await cl.make_async(manager.write_local_code)(filename, message.content)
            step.output = f"Executed full pipeline. Created {filename}."
            
        elements = [cl.File(name=filename, path=filename, display="inline")]
        await cl.Message(content=f"✅ **New Script Generated:** Download it below.", elements=elements).send()

    elif "REVISE" in route:
        async with cl.Step(name="🚦 Route: REVISION") as step:
            filename = "UnityGeneratedScript.cs"
            revision_prompt = f"Update the previous code based on this new request: {message.content}"
            await cl.make_async(manager.write_local_code)(filename, revision_prompt)
            step.output = "Skipped research. Code updated locally."
            
        elements = [cl.File(name=filename, path=filename, display="inline")]
        await cl.Message(content=f"🔧 **Script Revised:** Download the updated version below.", elements=elements).send()

    else:
        async with cl.Step(name="🚦 Route: CHAT") as step:
            step.output = "Standard conversation detected. Bypassing factory."
        await cl.Message(content="System standing by. Let me know when you need to code or revise something!").send()