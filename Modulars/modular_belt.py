import time
from collections import deque
from openai import OpenAI
import phoenix as px
from openinference.instrumentation.openai import OpenAIInstrumentor

# ==========================================
# 0. Arize Phoenix Observability Setup
# ==========================================
# Launching this at the very top ensures we catch all early handshakes.
print("🚀 Launching Arize Phoenix Dashboard...")
session = px.launch_app()
OpenAIInstrumentor().instrument() 
print(f"📊 Dashboard live! View your chat trees at: {session.url}")

# ==========================================
# 1. The Agent Class (The Workers)
# ==========================================
class Agent:
    """A generic worker with metadata for real-time chat tracing."""
    def __init__(self, name, client, model, system_prompt=""):
        self.name = name
        self.client = client
        self.model = model
        self.system_prompt = system_prompt

    def generate(self, user_prompt):
        print(f"DEBUG: [{self.name}] 🛰️ Sending request to {self.model}...")
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        try:
            start_time = time.time()
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                user=self.name 
            )
            duration = time.time() - start_time
            print(f"DEBUG: [{self.name}] ✅ Response received in {duration:.2f}s")
            return response.choices[0].message.content
            
        except Exception as e:
            print(f"DEBUG: [{self.name}] ❌ CRITICAL FAILURE: {e}")
            return f"ERROR: {e}"
# ==========================================
# 2. The Context Belt Class (The Memory)
# ==========================================
class ContextBelt:
    """A self-managing memory queue that prevents VRAM bloat[cite: 120, 121]."""
    def __init__(self, max_size, compactor_agent):
        self.belt = deque()
        self.max_size = max_size
        self.compactor = compactor_agent
        print(f"⚙️ ContextBelt initialized. Max size: {self.max_size}")

    def load(self, data):
        self.belt.appendleft(data)
        print(f"[Belt] Data loaded. Current size: {len(self.belt)}/{self.max_size}")
        if len(self.belt) > self.max_size:
            self._compact_tail()

    def _compact_tail(self):
        print(f"[Belt] 🧹 Overflow! Engaging {self.compactor.name} for tail cleaning...")
        oldest = self.belt.pop()
        second_oldest = self.belt.pop()
        
        # Use a fast background model to compress tokens [cite: 122]
        payload = f"Archive these factual blocks:\nBLOCK A: {second_oldest}\nBLOCK B: {oldest}"
        compressed_summary = self.compactor.generate(payload)
        
        self.belt.append(compressed_summary)
        print(f"[Belt] ✅ Tail compacted. Size is back to {len(self.belt)}.")

    def get_full_context(self):
        return "\n\n".join(list(self.belt))

# ==========================================
# 3. The Orchestrator Class (The Manager)
# ==========================================
class Orchestrator:
    def __init__(self, belt, loader_agent, local_writer_agent):
        self.belt = belt
        self.loader = loader_agent
        self.writer = local_writer_agent

    def execute_research_task(self, task):
        print(f"\n[{self.loader.name}] 📥 Researching: '{task}'")
        result = self.loader.generate(task)
        self.belt.load(result)
        
    def write_local_code(self, filename, coding_task):
        print(f"\n[{self.writer.name}] 💻 Analyzing belt and writing to: '{filename}'")
        context = self.belt.get_full_context()
        
        # Pass belt data to local LLM while staying under VRAM limits [cite: 111, 114]
        prompt = f"BACKGROUND CONTEXT:\n{context}\n\nCODING TASK:\n{coding_task}"
        code = self.writer.generate(prompt)
        
        with open(filename, "w", encoding="utf-8") as file:
            file.write(code)
        print(f"[{self.writer.name}] ✅ File saved successfully.")

# ==========================================
# 4. Factory Floor Setup & Execution
# ==========================================
if __name__ == "__main__":
    
    # --- A. Setup API Clients ---
    # OpenRouter serves as an aggregator for free-tier models [cite: 66, 67]
    openrouter_client = OpenAI(api_key="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", base_url="https://openrouter.ai/api/v1")
    # Groq provides ultra-low latency for background summarization [cite: 45, 122]
    groq_client = OpenAI(api_key="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", base_url="https://api.groq.com/openai/v1")
    # Local client points to Ollama port [cite: 80]
    local_client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

    # --- B. Instantiate Agents ---
    loader_bot = Agent(name="LoaderBot", client=openrouter_client, model="openrouter/free")

    # This prompt ensures factual separation and prevents semantic drift 
    compactor_prompt = """You are a sterile, highly precise data archivist. 
    Output ONLY a concise bulleted list of raw facts. Do NOT weave topics together.
    Strip out all metaphors, narratives, and conversational filler."""
    
    compactor_bot = Agent(name="CompactorBot", client=groq_client, model="llama-3.1-8b-instant", system_prompt=compactor_prompt)

    # Writer focuses on raw code output to avoid markdown parsing issues
    writer_prompt = """You are an expert local coding agent. 
    Output ONLY valid, executable code. IMPORTANT: Do NOT use markdown backticks (```).
    Begin the code output immediately without conversational filler."""
    
    local_writer = Agent(name="LocalCoder", client=local_client, model="Qwen2.5-Coder:14bL", system_prompt=writer_prompt)

    # --- C. Assemble the Machine ---
    memory_belt = ContextBelt(max_size=3, compactor_agent=compactor_bot)
    factory_manager = Orchestrator(belt=memory_belt, loader_agent=loader_bot, local_writer_agent=local_writer)

    # --- D. Run the Workflow ---
    tasks = [
        "What are the XYZ coordinates for a standard Unity Vector3?",
        "What is a Quaternion in 1 sentence?",
        "Explain what a C# Monobehaviour is briefly.",
        "What is the standard framerate for a VR application?"
    ]
    
    for task in tasks:
        factory_manager.execute_research_task(task)
        
    factory_manager.write_local_code(
        filename="unity_math_notes.py", 
        coding_task="Write a Python script with variables for standard Unity Vector3 and standard VR framerate."
    )

    print("\n✅ Session Complete.")
    input("⚠️ Press Enter to close. (Keeping alive for Phoenix trace sync...)")