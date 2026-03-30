from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List


@dataclass
class FailureRecord:
    timestamp: str
    prompt: str
    code_excerpt: str
    error_message: str
    wave_name: str
    target_file: str
    fix_summary: str = ""


class FailureMemory:
    def __init__(self, base_dir: str):
        os.makedirs(base_dir, exist_ok=True)
        self.library_path = os.path.join(base_dir, "failure_library.jsonl")
        self.rules_path = os.path.join(base_dir, "failure_rules.md")

    def log_failure(
        self,
        prompt: str,
        code: str,
        error_message: str,
        wave_name: str,
        target_file: str,
        fix_summary: str = "",
    ) -> None:
        record = FailureRecord(
            timestamp=datetime.utcnow().isoformat() + "Z",
            prompt=prompt,
            code_excerpt=code[:4000],
            error_message=error_message[:4000],
            wave_name=wave_name,
            target_file=target_file,
            fix_summary=fix_summary[:2000],
        )
        with open(self.library_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.__dict__, ensure_ascii=True) + "\n")

    def retrieve_similar(self, prompt: str, limit: int = 3) -> List[Dict]:
        if not os.path.exists(self.library_path):
            return []

        query_tokens = self._tokens(prompt)
        rows: List[Dict] = []
        with open(self.library_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                text = f"{row.get('prompt', '')} {row.get('error_message', '')}"
                score = self._similarity(query_tokens, self._tokens(text))
                if score > 0:
                    row["_score"] = score
                    rows.append(row)

        rows.sort(key=lambda item: item.get("_score", 0), reverse=True)
        return rows[:limit]

    def format_guidance(self, prompt: str, limit: int = 3) -> str:
        similar = self.retrieve_similar(prompt, limit=limit)
        if not similar:
            return ""

        lines = [
            "PAST FAILURE WARNINGS (avoid repeating these mistakes):",
        ]
        for item in similar:
            error = str(item.get("error_message", "")).replace("\n", " ")[:180]
            fix = str(item.get("fix_summary", "")).replace("\n", " ")[:180]
            if not fix:
                fix = "Add defensive checks and align implementation with failing tests."
            lines.append(f"- Prior failure: {error}")
            lines.append(f"  Prevention: {fix}")
        return "\n".join(lines)

    def append_rule(self, rule_text: str) -> None:
        clean = rule_text.strip()
        if not clean:
            return
        line = f"- [{datetime.utcnow().isoformat()}Z] {clean}\n"
        with open(self.rules_path, "a", encoding="utf-8") as handle:
            handle.write(line)

    def _tokens(self, text: str) -> List[str]:
        return re.findall(r"[a-zA-Z_]{3,}", (text or "").lower())

    def _similarity(self, a: List[str], b: List[str]) -> float:
        if not a or not b:
            return 0.0
        sa = set(a)
        sb = set(b)
        overlap = sa.intersection(sb)
        return len(overlap) / max(1, len(sa))
