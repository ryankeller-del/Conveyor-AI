from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List


@dataclass
class VersionSection:
    label: str
    path: str


class DailyReportGenerator:
    def __init__(self, repo_dir: str):
        self.repo_dir = repo_dir
        self.report_dir = os.path.join(repo_dir, "reports")
        self.state_path = os.path.join(self.report_dir, ".daily_report_state.json")
        os.makedirs(self.report_dir, exist_ok=True)

    def generate(self) -> str:
        state = self._load_state()
        baseline_commit = state.get("last_commit")

        sections = [
            VersionSection("Version app.py", "app.py"),
            VersionSection("Version app_v2.py", "app_v2.py"),
            VersionSection("Version app_v3.py", "app_v3.py"),
            VersionSection("Swarm Core", "swarm_core"),
            VersionSection("Tests", "tests"),
        ]

        title_date = datetime.now().strftime("%Y-%m-%d")
        lines: List[str] = [
            f"# Daily Improvement Report - {title_date}",
            "",
            f"Generated at: {datetime.now().isoformat()}",
            "",
        ]

        for section in sections:
            lines.extend(self._section_lines(section, baseline_commit))

        lines.append("## Summary")
        lines.append("- Report generated from git diff and working tree status.")
        lines.append("- Includes improvements per version and core swarm modules.")

        filename = f"daily_report_{datetime.now().strftime('%Y%m%d')}.md"
        report_path = os.path.join(self.report_dir, filename)
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

        latest_commit = self._git("rev-parse HEAD").strip() or baseline_commit
        self._save_state({"last_commit": latest_commit, "last_report": report_path})
        return report_path

    def _section_lines(self, section: VersionSection, baseline_commit: str | None) -> List[str]:
        header = [f"## {section.label}"]
        diff_text = self._diff_for_path(section.path, baseline_commit)
        if not diff_text.strip():
            return header + ["- No changes detected since last report.", ""]

        summary = self._summarize_diff(diff_text)
        lines = header + [f"- {item}" for item in summary]
        lines.append("")
        return lines

    def _diff_for_path(self, path: str, baseline_commit: str | None) -> str:
        if baseline_commit:
            cmd = f"git diff --unified=0 {baseline_commit} -- {path}"
        else:
            cmd = f"git diff --unified=0 -- {path}"
        return self._git(cmd)

    def _summarize_diff(self, diff_text: str) -> List[str]:
        additions = 0
        deletions = 0
        touched_hunks = 0
        notable: List[str] = []

        for line in diff_text.splitlines():
            if line.startswith("@@"):
                touched_hunks += 1
            elif line.startswith("+") and not line.startswith("+++"):
                additions += 1
                if len(notable) < 3 and line[1:].strip():
                    notable.append(f"Added: `{line[1:].strip()[:120]}`")
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        base = [
            f"Changed hunks: {touched_hunks}",
            f"Lines added: {additions}, lines removed: {deletions}",
        ]
        if notable:
            base.extend(notable)
        return base

    def _git(self, cmd: str) -> str:
        full = ["powershell", "-NoProfile", "-Command", cmd]
        try:
            result = subprocess.run(
                full,
                cwd=self.repo_dir,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                return ""
            return result.stdout
        except Exception:
            return ""

    def _load_state(self) -> Dict:
        if not os.path.exists(self.state_path):
            return {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def _save_state(self, state: Dict) -> None:
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
