import os
import time
from collections import deque

from openai import OpenAI
import phoenix as px
from openinference.instrumentation.openai import OpenAIInstrumentor

# ==========================================
# 0. Arize Phoenix Observability Setup
# ==========================================
# Launching this at the very top ensures we catch all early handshakes.
print("?? Launching Arize Phoenix Dashboard...")
session = px.launch_app()
OpenAIInstrumentor().instrument()
print(f"?? Dashboard live! View your chat trees at: {session.url}")


# ==========================================
# 1. The Agent Class (The Workers)
# ==========================================
class Agent:
    """A generic worker with metadata for real-time chat tracing."""

    def __init__(
        self,
        name,
        client,
        model,
        system_prompt="",
        fallback_models=None,
    ):
        self.name = name
        self.client = client
        self.model = model
        self.system_prompt = system_prompt
        self.fallback_models = fallback_models or []

    def generate(self, user_prompt):
        model_chain = [self.model] + self.fallback_models
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        errors = []

        for current_model in model_chain:
            print(f"DEBUG: [{self.name}] ??? Sending request to {current_model}...")
            try:
                start_time = time.time()
                response = self.client.chat.completions.create(
                    model=current_model,
                    messages=messages,
                    user=self.name,
                )
                duration = time.time() - start_time
                print(
                    f"DEBUG: [{self.name}] ? Response received in {duration:.2f}s"
                )
                content = response.choices[0].message.content
                return (
                    content
                    if content is not None
                    else "Error: Engine returned empty response."
                )
            except Exception as exc:
                print(
                    f"DEBUG: [{self.name}] ? Model {current_model} failed: {exc}"
                )
                errors.append(f"{current_model}: {exc}")

        return f"ERROR: All model attempts failed. {' | '.join(errors)}"


# ==========================================
# 2. The Context Belt Class (The Memory)
# ==========================================
class ContextBelt:
    """A self-managing memory queue that prevents VRAM bloat[cite: 120, 121]."""

    def __init__(self, max_size, compactor_agent):
        self.belt = deque()
        self.max_size = max_size
        self.compactor = compactor_agent
        print(f"?? ContextBelt initialized. Max size: {self.max_size}")

    def load(self, data):
        self.belt.appendleft(data)
        print(f"[Belt] Data loaded. Current size: {len(self.belt)}/{self.max_size}")
        if len(self.belt) > self.max_size:
            self._compact_tail()

    def _compact_tail(self):
        print(f"[Belt] ?? Overflow! Engaging {self.compactor.name} for tail cleaning...")
        oldest = self.belt.pop()
        second_oldest = self.belt.pop()

        payload = (
            "Archive these factual blocks:\n"
            f"BLOCK A: {second_oldest}\n"
            f"BLOCK B: {oldest}"
        )
        compressed_summary = self.compactor.generate(payload)

        self.belt.append(compressed_summary)
        print(f"[Belt] ? Tail compacted. Size is back to {len(self.belt)}.")

    def get_full_context(self):
        return "\n\n".join(list(self.belt))

    def get_recent(self, limit=3):
        if limit <= 0:
            return []
        return list(self.belt)[:limit]

    def get_relevant_context(self, keywords=None, max_chars=4000):
        entries = list(self.belt)
        if not entries:
            return ""

        normalized_keywords = [
            str(keyword).strip().lower()
            for keyword in (keywords or [])
            if str(keyword).strip()
        ]

        if not normalized_keywords:
            payload = "\n\n".join(entries)
            return payload[:max_chars]

        scored = []
        for entry in entries:
            lowered = entry.lower()
            score = sum(1 for keyword in normalized_keywords if keyword in lowered)
            if score > 0:
                scored.append((score, entry))

        if not scored:
            payload = "\n\n".join(entries[:2])
            return payload[:max_chars]

        scored.sort(key=lambda pair: pair[0], reverse=True)
        selected = []
        current_size = 0
        for _, entry in scored:
            addition = len(entry) + 2
            if current_size + addition > max_chars:
                break
            selected.append(entry)
            current_size += addition

        if not selected:
            return scored[0][1][:max_chars]

        return "\n\n".join(selected)


# ==========================================
# 3. The Orchestrator Class (The Manager)
# ==========================================
class Orchestrator:
    def __init__(self, belt, loader_agent, local_writer_agent, output_root=None):
        self.belt = belt
        self.loader = loader_agent
        self.writer = local_writer_agent
        self.agent_index = {}
        self.last_reindex_epoch = None
        self.output_root = output_root or os.path.dirname(os.path.abspath(__file__))

    def reindex_active_agents(self, agents):
        self.agent_index = {
            agent.name: {
                "model": agent.model,
                "fallback_models": agent.fallback_models,
            }
            for agent in agents
        }
        self.last_reindex_epoch = time.time()

    def execute_research_task(self, task):
        print(f"\n[{self.loader.name}] ?? Researching: '{task}'")
        result = self.loader.generate(task)
        self.belt.load(result)
        return result

    def write_local_code(self, filename, coding_task):
        print(f"\n[{self.writer.name}] ?? Analyzing belt and writing to: '{filename}'")
        context = self.belt.get_full_context()
        prompt = f"BACKGROUND CONTEXT:\n{context}\n\nCODING TASK:\n{coding_task}"
        code = self.writer.generate(prompt)

        clean_code = (
            code.replace("```", "")
            .strip()
        )

        target_path = (
            filename if os.path.isabs(filename) else os.path.join(self.output_root, filename)
        )
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        with open(target_path, "w", encoding="utf-8") as file:
            file.write(clean_code)

        print(f"[{self.writer.name}] ? File saved successfully: {target_path}")
        return target_path

    def write_local_code_scoped(
        self,
        filename,
        coding_task,
        context_keywords=None,
        max_context_chars=4000,
    ):
        context = self.belt.get_relevant_context(
            keywords=context_keywords,
            max_chars=max_context_chars,
        )
        prompt = (
            f"SCOPED BACKGROUND CONTEXT:\n{context}\n\n"
            f"CODING TASK:\n{coding_task}"
        )
        return self.write_local_code(filename, prompt)
