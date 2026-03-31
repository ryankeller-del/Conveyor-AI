from __future__ import annotations

import json
import math
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from collections import Counter
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
        self._lock = threading.RLock()
        self._records: List[Dict] = []
        self._load_records()

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
        payload = record.__dict__
        with self._lock:
            self._records.append(self._prepare_record(payload))
            with open(self.library_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def retrieve_similar(
        self,
        prompt: str,
        limit: int = 3,
        failure_context: str = "",
    ) -> List[Dict]:
        with self._lock:
            if not self._records:
                return []

            query_text = self._normalize_text(f"{prompt}\n{failure_context}")
            query_features = self._features(query_text)
            if not query_features:
                return []

            rows: List[Dict] = []
            for row in self._records:
                score = self._semantic_similarity(query_features, row)
                if score > 0:
                    ranked = dict(row)
                    ranked["_score"] = round(score, 6)
                    rows.append(ranked)

        rows.sort(key=lambda item: item.get("_score", 0), reverse=True)
        return rows[:limit]

    def format_guidance(self, prompt: str, limit: int = 3, failure_context: str = "") -> str:
        similar = self.retrieve_similar(prompt, limit=limit, failure_context=failure_context)
        if not similar:
            return ""
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

    def _load_records(self) -> None:
        if not os.path.exists(self.library_path):
            return
        try:
            with open(self.library_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    self._records.append(self._prepare_record(row))
        except Exception:
            return

    def _prepare_record(self, row: Dict) -> Dict:
        prepared = dict(row)
        prepared["_semantic_text"] = self._normalize_text(
            " ".join(
                [
                    str(prepared.get("prompt", "")),
                    str(prepared.get("error_message", "")),
                    str(prepared.get("fix_summary", "")),
                    str(prepared.get("code_excerpt", "")),
                    str(prepared.get("target_file", "")),
                    str(prepared.get("wave_name", "")),
                ]
            )
        )
        prepared["_features"] = self._features(prepared["_semantic_text"])
        return prepared

    def _semantic_similarity(self, query_features: Dict[str, float], row: Dict) -> float:
        row_features = row.get("_features") or self._features(str(row.get("_semantic_text", "")))
        if not query_features or not row_features:
            return 0.0
        token_score = self._cosine(query_features.get("tokens", {}), row_features.get("tokens", {}))
        gram_score = self._cosine(query_features.get("grams", {}), row_features.get("grams", {}))
        needle = self._normalize_text(
            " ".join(
                [
                    str(row.get("error_message", "")),
                    str(row.get("fix_summary", "")),
                ]
            )
        )
        emphasis = 0.0
        if needle:
            emphasis = len(set(self._tokens(needle)).intersection(set(self._tokens(self._normalize_text(row.get("_semantic_text", "")))))) / max(
                1, len(set(self._tokens(needle)))
            )
        return (token_score * 0.55) + (gram_score * 0.35) + (emphasis * 0.1)

    def _features(self, text: str) -> Dict[str, Dict[str, float]]:
        tokens = self._tokens(text)
        if not tokens:
            return {"tokens": {}, "grams": {}}
        token_counts = Counter(tokens)
        gram_counts: Counter = Counter()
        for token in tokens:
            cleaned = re.sub(r"[^a-z0-9_]+", "", token.lower())
            if len(cleaned) >= 4:
                for idx in range(len(cleaned) - 2):
                    gram = cleaned[idx: idx + 3]
                    gram_counts[gram] += 1
        return {
            "tokens": self._normalize_counts(token_counts),
            "grams": self._normalize_counts(gram_counts),
        }

    def _normalize_counts(self, counts: Counter) -> Dict[str, float]:
        if not counts:
            return {}
        norm = math.sqrt(sum(float(v) * float(v) for v in counts.values()))
        if not norm:
            return {}
        return {key: float(value) / norm for key, value in counts.items()}

    def _cosine(self, left: Dict[str, float], right: Dict[str, float]) -> float:
        if not left or not right:
            return 0.0
        if len(left) > len(right):
            left, right = right, left
        total = 0.0
        for key, value in left.items():
            if key in right:
                total += value * right[key]
        return total

    def _normalize_text(self, text: str) -> str:
        clean = (text or "").lower()
        clean = re.sub(r"\b\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}(?:\.\d+)?z?\b", " ", clean)
        clean = re.sub(r"https?://\S+", " ", clean)
        clean = re.sub(r"\b(?:info|debug|warning|error|critical)\b[:\s-]*", " ", clean)
        clean = re.sub(r"\\+", " ", clean)
        clean = re.sub(r"[^\w\s]+", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean
