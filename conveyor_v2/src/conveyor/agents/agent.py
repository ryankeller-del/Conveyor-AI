"""Agent base class — SimpleAgent.

Wraps OpenAI-compatible clients with automatic fallback handling.
This is the only module that makes LLM API calls.

Legacy source: swarm_core/bots.py (SimpleAgent class)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI


@dataclass
class AgentResponse:
    """Response from a single agent run."""
    text: str
    model_used: str
    fallback_used: bool = False
    latency_ms: float = 0.0
    error: str | None = None


class SimpleAgent:
    """A single specialist agent with primary + fallback client routing.

    Legacy behaviour (from _build_controller() in app.py):
    - Each agent has a primary OpenAI client (Groq, OpenRouter, or local).
    - Optional fallback_client for when primary fails.
    - Optional fallback_client_models list for OpenRouter filtering.
    - is_local flag indicates local Ollama (api_key="ollama" convention).

    Fallback chain:
      1. Primary client with primary model
      2. Fallback client with primary model (if fallback_client provided)
      3. AgentResponse with error (all routes exhausted)
    """

    def __init__(
        self,
        name: str,
        model: str,
        system_prompt: str,
        client: OpenAI,
        fallback_models: list[str] | None = None,
        fallback_client: OpenAI | None = None,
        fallback_client_models: list[str] | None = None,
        is_local: bool = False,
    ) -> None:
        self.name = name
        self.model = model
        self.system_prompt = system_prompt
        self.client = client
        self.fallback_models = fallback_models or []
        self.fallback_client = fallback_client
        self.fallback_client_models = fallback_client_models or []
        self.is_local = is_local

    def run(self, prompt: str, context: str = "") -> AgentResponse:
        """Run the agent with a prompt and optional context.

        Synchronous to match legacy calling convention (asyncio.to_thread).

        Legacy: respond_to_local() in app.py called controller.respond_to_chat()
        which internally called agent completions via asyncio.to_thread.
        """
        full_prompt = self._build_prompt(prompt, context)
        tried_models: list[str] = []

        # Attempt 1: primary client with primary model
        tried_models.append(self.model)
        result = self._try_completion(
            client=self.client,
            model=self.model,
            prompt=full_prompt,
        )
        if result.error is None:
            return result

        # Attempt 2: fallback client with primary model
        if self.fallback_client is not None:
            tried_models.append(f"fb:{self.model}")
            result = self._try_completion(
                client=self.fallback_client,
                model=self.model,
                prompt=full_prompt,
            )
            if result.error is None:
                result.fallback_used = True
                return result

        # All routes exhausted — return error
        return AgentResponse(
            text="",
            model_used=tried_models[0],
            fallback_used=True,
            latency_ms=result.latency_ms,
            error=result.error or "All fallback routes exhausted",
        )

    # ----------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------

    def _build_prompt(self, prompt: str, context: str) -> str:
        if not context:
            return prompt
        return f"{context}\n\n{prompt}"

    def _try_completion(
        self,
        client: OpenAI,
        model: str,
        prompt: str,
    ) -> AgentResponse:
        """Attempt a single OpenAI-compatible completion."""
        start = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt},
                ],
            )
            text = response.choices[0].message.content or ""
            elapsed_ms = (time.monotonic() - start) * 1000
            return AgentResponse(
                text=text,
                model_used=model,
                latency_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            return AgentResponse(
                text="",
                model_used=model,
                latency_ms=elapsed_ms,
                error=str(exc),
            )
