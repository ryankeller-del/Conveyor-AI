from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class TeamIdea:
    source_team: str
    target_team: str
    idea: str
    new_approach: str


class TeamComparator:
    def _extract_signatures(self, code: str) -> List[str]:
        python_defs = re.findall(r"^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", code, flags=re.M)
        class_defs = re.findall(r"^class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[:(]", code, flags=re.M)
        cs_methods = re.findall(
            r"(?:public|private|protected|internal)\s+(?:static\s+)?[a-zA-Z0-9_<>,\[\]]+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
            code,
            flags=re.M,
        )
        return sorted(set(python_defs + class_defs + cs_methods))

    def compare(self, team_a_files: List[str], team_b_files: List[str]) -> Dict:
        a_code = self._read_all(team_a_files)
        b_code = self._read_all(team_b_files)

        a_sigs = set(self._extract_signatures(a_code))
        b_sigs = set(self._extract_signatures(b_code))

        novel_a = sorted(a_sigs - b_sigs)
        novel_b = sorted(b_sigs - a_sigs)

        return {
            "team_a_signatures": sorted(a_sigs),
            "team_b_signatures": sorted(b_sigs),
            "novel_a": novel_a,
            "novel_b": novel_b,
        }

    def _read_all(self, paths: List[str]) -> str:
        chunks = []
        for path in paths:
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    chunks.append(handle.read())
            except Exception:
                continue
        return "\n\n".join(chunks)


class BrainstormEngine:
    def brainstorm(self, comparison: Dict, top_n: int = 3) -> List[TeamIdea]:
        ideas: List[TeamIdea] = []

        for signature in comparison.get("novel_a", [])[:top_n]:
            ideas.append(
                TeamIdea(
                    source_team="Team A",
                    target_team="Team B",
                    idea=f"Adopt behavior similar to `{signature}`",
                    new_approach=(
                        "Implement equivalent capability using composition and helper "
                        "utilities instead of direct copy-paste."
                    ),
                )
            )

        for signature in comparison.get("novel_b", [])[:top_n]:
            ideas.append(
                TeamIdea(
                    source_team="Team B",
                    target_team="Team A",
                    idea=f"Adopt behavior similar to `{signature}`",
                    new_approach=(
                        "Implement equivalent capability using explicit interfaces and "
                        "guard clauses for stronger testability."
                    ),
                )
            )

        if not ideas:
            ideas.append(
                TeamIdea(
                    source_team="Team A",
                    target_team="Team B",
                    idea="No novel signatures detected; focus on refactor diversity",
                    new_approach=(
                        "Re-implement one shared function with a different control flow "
                        "pattern and benchmark readability."
                    ),
                )
            )
        return ideas
