from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .bots import SimpleAgent


@dataclass
class CompactionResult:
    cycle_index: int
    golden_rules: List[str]
    breadcrumbs: List[str]
    format_payloads: Dict[str, str]
    format_scores: Dict[str, float]
    selected_format: str


class DistillationLoop:
    def __init__(
        self,
        pattern_agent: Optional[SimpleAgent] = None,
        compression_agent: Optional[SimpleAgent] = None,
        novelty_agent: Optional[SimpleAgent] = None,
    ):
        self.pattern_agent = pattern_agent
        self.compression_agent = compression_agent
        self.novelty_agent = novelty_agent

    def run(
        self,
        cycle_index: int,
        failure_library_path: str,
        progress_path: str,
        historic_format_scores: Dict[str, float],
        rule_limit: int = 6,
        breadcrumb_limit: int = 5,
    ) -> CompactionResult:
        failures = self._read_jsonl_rows(failure_library_path, max_rows=120)
        progress_lines = self._read_lines(progress_path, max_lines=160)

        rules = self._extract_rules(failures, progress_lines, limit=rule_limit)
        breadcrumbs = self._build_breadcrumbs(rules, progress_lines, limit=breadcrumb_limit)

        formats = self._build_format_payloads(rules, breadcrumbs)
        scores = self._score_formats(formats, historic_format_scores, rules, breadcrumbs)
        selected = max(scores, key=scores.get)

        return CompactionResult(
            cycle_index=cycle_index,
            golden_rules=rules,
            breadcrumbs=breadcrumbs,
            format_payloads=formats,
            format_scores=scores,
            selected_format=selected,
        )

    def _extract_rules(
        self,
        failures: List[Dict],
        progress_lines: List[str],
        limit: int,
    ) -> List[str]:
        rule_candidates: List[str] = []
        for row in failures[-20:]:
            error = str(row.get("error_message", "")).strip().replace("\n", " ")
            fix = str(row.get("fix_summary", "")).strip().replace("\n", " ")
            if not error:
                continue
            if fix:
                rule_candidates.append(
                    f"If failure resembles '{error[:80]}', prefer fix pattern '{fix[:100]}'."
                )
            else:
                rule_candidates.append(
                    f"Guard against repeat of '{error[:90]}' by adding explicit validation and tests."
                )

        for line in progress_lines[-80:]:
            if "Spawned " in line:
                rule_candidates.append(
                    "When retries recur, delegate to a specialist instead of increasing prompt scope."
                )
            if "Hallucination alert" in line:
                rule_candidates.append(
                    "Resolve unknown symbols/APIs before execution; do not proceed on unresolved names."
                )

        normalized = []
        seen = set()
        for item in rule_candidates:
            key = re.sub(r"\W+", " ", item.lower()).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append(item)
            if len(normalized) >= limit:
                break

        if not normalized:
            normalized = [
                "Prefer small, test-oriented edits that preserve existing behavior.",
                "Treat each failure as a reusable constraint for future prompts.",
            ]
        return normalized

    def _build_breadcrumbs(
        self,
        rules: List[str],
        progress_lines: List[str],
        limit: int,
    ) -> List[str]:
        breadcrumbs = []
        for rule in rules:
            breadcrumbs.append(f"Catalyst: {rule}")

        if progress_lines:
            last = progress_lines[-1].strip()
            if last:
                breadcrumbs.append(
                    f"Re-anchor: verify latest action against project objective before next wave ({last[:80]})."
                )

        return breadcrumbs[:limit]

    def _build_format_payloads(self, rules: List[str], breadcrumbs: List[str]) -> Dict[str, str]:
        blueprint = {
            "memory_contract": {
                "golden_rules": rules,
                "breadcrumbs": breadcrumbs,
                "instruction": "Apply one breadcrumb per wave and validate via tests.",
            }
        }
        narrative_lines = [
            "# Distilled Memory",
            "",
            "## Lessons",
        ]
        for rule in rules:
            narrative_lines.append(f"- {rule}")
        narrative_lines.append("")
        narrative_lines.append("## Active Breadcrumbs")
        for crumb in breadcrumbs:
            narrative_lines.append(f"- {crumb}")

        command_lines = [
            "RULESET:",
            *[f"{idx + 1}. {rule}" for idx, rule in enumerate(rules)],
            "EXECUTION:",
            "1. Select one breadcrumb.",
            "2. Apply focused code change.",
            "3. Run tests and keep change only if deterministic and green.",
            "BREADCRUMBS:",
            *[f"- {crumb}" for crumb in breadcrumbs],
        ]

        return {
            "BLUEPRINT": json.dumps(blueprint, ensure_ascii=True, indent=2),
            "NARRATIVE": "\n".join(narrative_lines),
            "COMMAND": "\n".join(command_lines),
        }

    def _score_formats(
        self,
        format_payloads: Dict[str, str],
        historic: Dict[str, float],
        rules: List[str],
        breadcrumbs: List[str],
    ) -> Dict[str, float]:
        scores = {}
        for key, payload in format_payloads.items():
            structure_bonus = 0.2 if key in {"BLUEPRINT", "COMMAND"} else 0.1
            density = (len(rules) * 0.08) + (len(breadcrumbs) * 0.06)
            brevity_bonus = max(0.0, 0.3 - (len(payload) / 5000.0))
            historic_bonus = historic.get(key, 0.0) * 0.8
            scores[key] = round(structure_bonus + density + brevity_bonus + historic_bonus, 6)
        return scores

    def _read_jsonl_rows(self, path: str, max_rows: int) -> List[Dict]:
        if not path or not os.path.exists(path):
            return []
        rows = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        return rows[-max_rows:]

    def _read_lines(self, path: str, max_lines: int) -> List[str]:
        if not path or not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as handle:
            lines = [line.rstrip() for line in handle]
        return lines[-max_lines:]
