from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class RosettaResult:
    text: str
    warnings: List[str] = field(default_factory=list)


class RosettaStone:
    def __init__(self):
        self._jargon_map = {
            "idempotent": "safe to run repeatedly",
            "orthogonal": "independent",
            "eventual consistency": "may take time to sync",
            "heuristic": "best-effort rule",
            "backpressure": "slow down when overloaded",
            "hallucination": "made-up API or unsupported claim",
            "topology": "agent layout",
            "regression": "previously working behavior that broke",
            "churn": "amount of code changes",
            "orchestration": "coordination flow",
        }

    def mediate(self, text: str, audience: str, max_chars: int = 320) -> RosettaResult:
        source = (text or "").strip()
        if not source:
            return RosettaResult(text="")

        warnings = self._detect_vague_or_impossible(source)
        rewritten = source
        if audience == "language":
            rewritten = self._de_jargon(source)
        elif audience == "specialist":
            rewritten = self._specialist_brief(source)

        if warnings:
            # Convert warnings into actionable constraints instead of only flagging.
            rewritten = self._apply_constraints(rewritten, warnings)

        return RosettaResult(text=rewritten[: max(80, max_chars)], warnings=warnings)

    def _de_jargon(self, text: str) -> str:
        out = text
        for key, repl in self._jargon_map.items():
            out = re.sub(rf"\b{re.escape(key)}\b", repl, out, flags=re.IGNORECASE)
        return out

    def _specialist_brief(self, text: str) -> str:
        cleaned = self._de_jargon(text)
        return (
            "Objective: " + cleaned[:140]
            + " | Constraints: keep scope small, file-targeted, test-verifiable"
            + " | Done: deterministic passing tests and no new open handoffs"
        )

    def _apply_constraints(self, text: str, warnings: List[str]) -> str:
        clauses = []
        for item in warnings[:3]:
            clauses.append(f"Constraint: {item}")
        return f"{text} | " + " | ".join(clauses)

    def _detect_vague_or_impossible(self, text: str) -> List[str]:
        low = text.lower()
        warnings: List[str] = []
        vague_markers = [
            "make it better",
            "optimize everything",
            "fix all issues",
            "improve all",
            "do it perfectly",
            "just handle it",
        ]
        impossible_markers = [
            "zero bugs forever",
            "instant",
            "guarantee no failures",
            "solve all tasks",
            "infinite scale",
            "all edge cases ever",
        ]
        if any(token in low for token in vague_markers):
            warnings.append("narrow objective to one behavior and one file target")
        if any(token in low for token in impossible_markers):
            warnings.append("replace impossible guarantees with measurable acceptance criteria")
        has_action = any(word in low for word in ["add", "fix", "refactor", "implement", "test", "validate"])
        if not has_action:
            warnings.append("include a concrete action verb and expected output")
        return warnings
