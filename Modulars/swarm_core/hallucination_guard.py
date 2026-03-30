from __future__ import annotations

import builtins
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass
class HallucinationResult:
    confidence: float
    unknown_symbols: List[str] = field(default_factory=list)
    unknown_apis: List[str] = field(default_factory=list)
    missing_doc_grounding: bool = False
    alerts: List[str] = field(default_factory=list)


class HallucinationGuard:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir

    def evaluate(
        self,
        target_file: str,
        code: str,
        prompt: str,
        doc_grounding_enabled: bool = False,
    ) -> HallucinationResult:
        project_graph = self._build_project_graph()
        reachable_symbols = self._reachable_symbols(project_graph, target_file)

        referenced_symbols = self._extract_call_symbols(code)
        unknown_symbols = sorted(
            symbol for symbol in referenced_symbols if symbol not in reachable_symbols
        )

        imported_roots = self._extract_import_roots(code)
        unknown_apis = self._detect_unknown_apis(code, imported_roots, reachable_symbols)

        missing_doc_grounding = False
        if doc_grounding_enabled and self._contains_external_claims(prompt):
            missing_doc_grounding = not self._contains_citation(prompt)

        confidence = 1.0
        confidence -= min(0.6, 0.08 * len(unknown_symbols))
        confidence -= min(0.3, 0.07 * len(unknown_apis))
        if missing_doc_grounding:
            confidence -= 0.2
        confidence = max(0.0, round(confidence, 4))

        alerts: List[str] = []
        if unknown_symbols:
            alerts.append(f"Unknown symbols detected: {', '.join(unknown_symbols[:6])}")
        if unknown_apis:
            alerts.append(f"Unknown APIs detected: {', '.join(unknown_apis[:6])}")
        if missing_doc_grounding:
            alerts.append("External claim lacks explicit citation.")

        return HallucinationResult(
            confidence=confidence,
            unknown_symbols=unknown_symbols,
            unknown_apis=unknown_apis,
            missing_doc_grounding=missing_doc_grounding,
            alerts=alerts,
        )

    def _build_project_graph(self) -> Dict[str, Dict[str, Set[str]]]:
        graph: Dict[str, Dict[str, Set[str]]] = {}
        for dirpath, _, filenames in os.walk(self.root_dir):
            if any(part in dirpath for part in ["__pycache__", ".git", "swarm_runs", "reports", ".pytest_cache"]):
                continue
            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in {".py", ".js", ".ts", ".cs"}:
                    continue
                path = os.path.join(dirpath, filename)
                rel = os.path.relpath(path, self.root_dir)
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        content = handle.read()
                except Exception:
                    continue
                exports = set(self._extract_defined_symbols(content))
                imports = set(self._extract_import_roots(content))
                graph[rel] = {"exports": exports, "imports": imports}
        return graph

    def _reachable_symbols(self, graph: Dict[str, Dict[str, Set[str]]], target_file: str) -> Set[str]:
        rel_target = os.path.relpath(target_file, self.root_dir) if os.path.isabs(target_file) else target_file
        known = set(dir(builtins))
        keywords = {
            "if", "for", "while", "return", "class", "def", "new", "try", "catch",
            "switch", "case", "throw", "await", "async", "lambda", "with",
        }
        known.update(keywords)

        target_node = graph.get(rel_target)
        if target_node:
            known.update(target_node.get("exports", set()))
            imported_roots = target_node.get("imports", set())
            known.update(imported_roots)
            for node in graph.values():
                if node.get("exports") & imported_roots:
                    known.update(node.get("exports", set()))

        for node in graph.values():
            known.update(node.get("exports", set()))

        return known

    def _extract_defined_symbols(self, code: str) -> List[str]:
        py_defs = re.findall(r"^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", code, flags=re.M)
        py_classes = re.findall(r"^class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[:(]", code, flags=re.M)
        js_defs = re.findall(r"\bfunction\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", code)
        js_classes = re.findall(r"\bclass\s+([a-zA-Z_][a-zA-Z0-9_]*)\b", code)
        cs_members = re.findall(
            r"(?:public|private|protected|internal)\s+(?:static\s+)?[a-zA-Z0-9_<>,\[\]]+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
            code,
        )
        return list(set(py_defs + py_classes + js_defs + js_classes + cs_members))

    def _extract_import_roots(self, code: str) -> List[str]:
        roots: List[str] = []
        py_imports = re.findall(r"^import\s+([a-zA-Z0-9_\.]+)", code, flags=re.M)
        py_from = re.findall(r"^from\s+([a-zA-Z0-9_\.]+)\s+import", code, flags=re.M)
        js_imports = re.findall(r"import\s+.*?from\s+['\"]([^'\"]+)['\"]", code)
        cs_using = re.findall(r"^using\s+([a-zA-Z0-9_\.]+)\s*;", code, flags=re.M)
        for item in py_imports + py_from + js_imports + cs_using:
            root = item.split(".")[0].split("/")[-1]
            if root:
                roots.append(root)
        return list(set(roots))

    def _extract_call_symbols(self, code: str) -> List[str]:
        calls = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", code)
        ignore = {"if", "for", "while", "return", "switch", "catch", "typeof"}
        return list({call for call in calls if call not in ignore})

    def _detect_unknown_apis(
        self,
        code: str,
        imported_roots: List[str],
        reachable_symbols: Set[str],
    ) -> List[str]:
        known_roots = set(imported_roots) | reachable_symbols
        dotted = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", code)
        unknown = []
        for root, method in dotted:
            if root not in known_roots and root not in dir(builtins):
                unknown.append(f"{root}.{method}")
        return sorted(set(unknown))

    def _contains_external_claims(self, text: str) -> bool:
        lowered = (text or "").lower()
        markers = ["according to", "official docs", "documentation says", "latest release"]
        return any(marker in lowered for marker in markers)

    def _contains_citation(self, text: str) -> bool:
        return bool(re.search(r"https?://", text or ""))
