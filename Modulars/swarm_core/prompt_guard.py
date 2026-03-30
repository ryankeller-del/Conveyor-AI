from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class PromptGuardResult:
    prompt: str
    changed: bool
    note: str


class PromptGuard:
    def __init__(self, guard_agent=None):
        self.guard_agent = guard_agent

    def complexity_score(self, prompt: str) -> float:
        text = prompt or ""
        length_score = min(1.0, len(text) / 9000)
        line_score = min(1.0, text.count("\n") / 120)
        clause_score = min(1.0, len(re.findall(r"\b(and|or|unless|except|while|whereas)\b", text, re.I)) / 30)
        codeblock_score = 0.25 if "```" in text else 0.0
        bullet_score = min(1.0, len(re.findall(r"^\s*[-*]\s+", text, re.M)) / 40)
        return round(min(1.0, 0.4 * length_score + 0.2 * line_score + 0.2 * clause_score + 0.1 * bullet_score + codeblock_score), 4)

    def guard_prompt(
        self,
        prompt: str,
        purpose: str,
        max_chars: int,
        complexity_threshold: float,
    ) -> PromptGuardResult:
        score = self.complexity_score(prompt)
        if score < complexity_threshold and len(prompt) <= max_chars:
            return PromptGuardResult(prompt=prompt, changed=False, note=f"complexity={score}")

        rewritten = self._rewrite_with_agent(prompt, purpose, max_chars)
        if not rewritten:
            rewritten = self._heuristic_refactor(prompt, max_chars)
        note = f"complexity={score} refactored for {purpose}"
        return PromptGuardResult(prompt=rewritten, changed=True, note=note)

    def refactor_on_failure(
        self,
        prompt: str,
        purpose: str,
        failure_message: str,
        failure_context: str,
        max_chars: int,
    ) -> PromptGuardResult:
        guided = (
            "Refactor this prompt to reduce reasoning load and ambiguity. "
            "Keep only essential constraints and concrete output shape. "
            "Write this as a high-level staged objective with clear success criteria. "
            f"Purpose: {purpose}. Failure: {failure_message[:600]}.\n"
            f"Failure Context: {failure_context[:1200]}.\n\n"
            f"PROMPT:\n{prompt[:12000]}"
        )
        rewritten = self._rewrite_with_agent(guided, purpose, max_chars)
        if not rewritten:
            rewritten = self._heuristic_refactor(
                f"{prompt}\n\nKnown failure context:\n{failure_context}",
                max_chars,
            )
        return PromptGuardResult(
            prompt=rewritten,
            changed=True,
            note=f"refactored after failure for {purpose}",
        )

    def _rewrite_with_agent(self, prompt: str, purpose: str, max_chars: int) -> Optional[str]:
        if self.guard_agent is None:
            return None
        request = (
            "You are ContextGuardBot. Rewrite the prompt so weaker models can execute it reliably. "
            "Rules: preserve intent, remove redundant context, keep explicit output format, "
            "and keep length under max chars. Return plain prompt only.\n\n"
            f"Purpose: {purpose}\nMax chars: {max_chars}\n\n"
            f"Original Prompt:\n{prompt[:16000]}"
        )
        try:
            rewritten = self.guard_agent.generate(request)
            if rewritten and rewritten.strip():
                return rewritten.strip()[:max_chars]
        except Exception:
            return None
        return None

    def _heuristic_refactor(self, prompt: str, max_chars: int) -> str:
        lines = [line.strip() for line in (prompt or "").splitlines() if line.strip()]
        key_lines = []
        priority_markers = ["goal", "task", "output", "must", "constraints", "tests", "fix"]
        for line in lines:
            if any(marker in line.lower() for marker in priority_markers):
                key_lines.append(line)
            if len(key_lines) >= 20:
                break

        if not key_lines:
            key_lines = lines[:20]

        compact = "\n".join(key_lines)
        compact = re.sub(r"\n{3,}", "\n\n", compact)
        return compact[:max_chars]
