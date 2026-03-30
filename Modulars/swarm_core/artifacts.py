from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List

from .types import RunSnapshot, SpawnRecord


class ArtifactStore:
    def __init__(self, base_dir: str, run_id: str):
        self.base_dir = os.path.join(base_dir, run_id)
        os.makedirs(self.base_dir, exist_ok=True)

        self.events_path = os.path.join(self.base_dir, "events.jsonl")
        self.progress_path = os.path.join(self.base_dir, "progress.md")
        self.spawn_report_path = os.path.join(self.base_dir, "spawn_report.md")
        self.team_report_path = os.path.join(self.base_dir, "team_brainstorm.md")
        self.failure_report_path = os.path.join(self.base_dir, "failure_memory.md")
        self.hallucination_report_path = os.path.join(self.base_dir, "hallucination_guard.md")
        self.prompt_guard_path = os.path.join(self.base_dir, "prompt_guard.md")
        self.memory_primitives_path = os.path.join(self.base_dir, "memory_primitives.md")
        self.memory_formats_path = os.path.join(self.base_dir, "memory_formats.json")
        self.eff_json_path = os.path.join(self.base_dir, "efficiency_report.json")
        self.eff_md_path = os.path.join(self.base_dir, "efficiency_report.md")
        self.state_path = os.path.join(self.base_dir, "session_state.json")

    def _ts(self) -> str:
        return datetime.utcnow().isoformat() + "Z"

    def append_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        row = {"timestamp": self._ts(), "event_type": event_type, "payload": payload}
        with open(self.events_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    def append_progress(self, line: str) -> None:
        with open(self.progress_path, "a", encoding="utf-8") as handle:
            handle.write(f"- [{self._ts()}] {line}\n")

    def append_spawn_record(self, record: SpawnRecord) -> None:
        content = (
            f"## {record.agent_name}\n"
            f"Parent: {record.parent_agent}\n\n"
            f"Handoff ID: {record.handoff_id or 'n/a'}\n\n"
            f"Return Required: {record.return_required}\n\n"
            f"Status: {record.status}\n\n"
            f"Reason: {record.reason}\n\n"
            f"Task Scope: {record.task_scope}\n\n"
            f"Files Touched: {', '.join(record.files_touched) if record.files_touched else 'none'}\n\n"
            f"Result: {record.result_summary}\n\n"
            f"Duration Seconds: {record.duration_seconds:.2f}\n\n"
            f"Quality Delta: {record.quality_delta:.3f}\n\n"
            f"Calls Used: {record.calls_used}\n\n"
        )
        with open(self.spawn_report_path, "a", encoding="utf-8") as handle:
            handle.write(content)

    def append_team_brainstorm(
        self,
        cycle_index: int,
        team_a_files: List[str],
        team_b_files: List[str],
        ideas: List[Dict[str, str]],
    ) -> None:
        lines = [
            f"## Cycle {cycle_index}",
            "",
            f"Team A Files: {', '.join(team_a_files) if team_a_files else 'none'}",
            f"Team B Files: {', '.join(team_b_files) if team_b_files else 'none'}",
            "",
            "### Idea Transfers",
        ]
        for idx, idea in enumerate(ideas, start=1):
            lines.append(
                f"{idx}. Source={idea.get('source_team')} Target={idea.get('target_team')} "
                f"Idea={idea.get('idea')}"
            )
            lines.append(f"   Implementation Pattern: {idea.get('new_approach')}")
        lines.append("")
        with open(self.team_report_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def append_failure_memory_entry(
        self,
        prompt: str,
        error_message: str,
        guidance: str,
    ) -> None:
        lines = [
            "## Failure Memory Event",
            "",
            f"Prompt: {prompt[:300]}",
            "",
            f"Error: {error_message[:500]}",
            "",
            f"Guidance: {guidance[:800] if guidance else 'n/a'}",
            "",
        ]
        with open(self.failure_report_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def append_hallucination_check(
        self,
        cycle_index: int,
        confidence: float,
        alerts: List[str],
        unknown_symbols: List[str],
        unknown_apis: List[str],
    ) -> None:
        lines = [
            f"## Cycle {cycle_index}",
            "",
            f"Confidence: {confidence:.4f}",
            f"Alerts: {len(alerts)}",
            f"Unknown Symbols: {', '.join(unknown_symbols[:12]) if unknown_symbols else 'none'}",
            f"Unknown APIs: {', '.join(unknown_apis[:12]) if unknown_apis else 'none'}",
            "",
        ]
        if alerts:
            lines.append("### Alert Details")
            for item in alerts:
                lines.append(f"- {item}")
            lines.append("")
        with open(self.hallucination_report_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def append_prompt_guard_event(
        self,
        purpose: str,
        note: str,
        before_chars: int,
        after_chars: int,
    ) -> None:
        lines = [
            f"## {purpose}",
            "",
            f"Note: {note}",
            f"Chars Before: {before_chars}",
            f"Chars After: {after_chars}",
            "",
        ]
        with open(self.prompt_guard_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def append_memory_primitive(
        self,
        cycle_index: int,
        golden_rules: List[str],
        breadcrumbs: List[str],
        selected_format: str,
    ) -> None:
        lines = [
            f"## Cycle {cycle_index}",
            "",
            f"Selected Format: {selected_format}",
            "",
            "### Golden Rules",
        ]
        for item in golden_rules:
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### Breadcrumbs")
        for item in breadcrumbs:
            lines.append(f"- {item}")
        lines.append("")
        with open(self.memory_primitives_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def write_memory_format_benchmark(
        self,
        active_format: str,
        scores: Dict[str, float],
        payloads: Dict[str, str],
    ) -> None:
        data = {
            "generated_at": self._ts(),
            "active_format": active_format,
            "scores": scores,
            "payloads": payloads,
        }
        with open(self.memory_formats_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

    def write_efficiency_reports(
        self,
        scores: Dict[str, float],
        recommendation: str,
        details: List[Dict[str, Any]],
    ) -> None:
        payload = {
            "generated_at": self._ts(),
            "scores": scores,
            "recommendation": recommendation,
            "details": details,
        }
        with open(self.eff_json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

        lines = ["# Efficiency Report", "", f"Recommended Topology: `{recommendation}`", ""]
        lines.append("## Scores")
        for key, value in sorted(scores.items(), key=lambda pair: pair[1], reverse=True):
            lines.append(f"- {key}: {value:.4f}")
        lines.append("")
        lines.append("## Evidence")
        for detail in details:
            lines.append(
                f"- Cycle {detail.get('cycle')}: topology={detail.get('topology')} "
                f"pass_rate={detail.get('pass_rate')} duration={detail.get('duration_seconds')}"
            )

        with open(self.eff_md_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def save_snapshot(self, snapshot: RunSnapshot) -> None:
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(asdict(snapshot), handle, indent=2, default=str)
