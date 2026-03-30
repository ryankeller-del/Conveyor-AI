from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class StandardTestTemplate:
    role: str
    code_type: str
    failure_pattern: str
    name: str
    body: str


@dataclass
class StandardTestPack:
    role: str
    code_type: str
    failure_pattern: str
    target_file: str
    templates: List[StandardTestTemplate] = field(default_factory=list)

    def render(self) -> str:
        if not self.templates:
            return ""
        lines = [
            "# Standard fallback tests",
            f"# Role: {self.role}",
            f"# Code Type: {self.code_type}",
            f"# Failure Pattern: {self.failure_pattern or 'none'}",
            "",
        ]
        module_name = os.path.splitext(os.path.basename(self.target_file))[0]
        for template in self.templates:
            safe_name = _safe_test_name(template.name)
            body = template.body.replace("{module_name}", module_name)
            if f"__import__('{module_name}')" not in body:
                body = f"module = __import__('{module_name}')\n    {body}"
            lines.append(f"def {safe_name}():")
            for line in body.splitlines():
                lines.append(f"    {line}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


class StandardTestLibrary:
    def __init__(self):
        self._templates = self._build_templates()

    def resolve(
        self,
        role: str,
        code_type: str,
        failure_pattern: str,
        target_file: str,
    ) -> StandardTestPack:
        role_key = self._normalize(role)
        code_key = self._normalize(code_type)
        failure_key = self._normalize(failure_pattern)
        templates = self._select_templates(role_key, code_key, failure_key)
        return StandardTestPack(
            role=role_key,
            code_type=code_key,
            failure_pattern=failure_key,
            target_file=target_file,
            templates=templates,
        )

    def _select_templates(self, role: str, code_type: str, failure_pattern: str) -> List[StandardTestTemplate]:
        selected: List[StandardTestTemplate] = []
        for template in self._templates:
            if template.role not in {role, "any"}:
                continue
            if template.code_type not in {code_type, "any"}:
                continue
            if template.failure_pattern not in {failure_pattern, "any"}:
                continue
            selected.append(template)
        if selected:
            return selected[:6]
        return [
            StandardTestTemplate(
                role=role,
                code_type=code_type,
                failure_pattern=failure_pattern or "any",
                name="smoke_imports",
                body="assert module is not None",
            ),
            StandardTestTemplate(
                role=role,
                code_type=code_type,
                failure_pattern=failure_pattern or "any",
                name="smoke_has_namespace",
                body="assert hasattr(module, '__dict__')",
            ),
        ]

    def _build_templates(self) -> List[StandardTestTemplate]:
        return [
            StandardTestTemplate(
                role="test",
                code_type="python",
                failure_pattern="any",
                name="smoke_imports",
                body="assert module is not None",
            ),
            StandardTestTemplate(
                role="test",
                code_type="python",
                failure_pattern="boundary",
                name="boundary_has_namespace",
                body="assert hasattr(module, '__dict__')",
            ),
            StandardTestTemplate(
                role="test",
                code_type="python",
                failure_pattern="regression",
                name="regression_module_name",
                body="assert module.__name__ == '{module_name}'",
            ),
            StandardTestTemplate(
                role="test",
                code_type="flask",
                failure_pattern="any",
                name="flask_app_imports",
                body="assert module is not None",
            ),
            StandardTestTemplate(
                role="test",
                code_type="flask",
                failure_pattern="unknown_api",
                name="flask_unknown_api_guard",
                body="assert hasattr(module, '__dict__')",
            ),
            StandardTestTemplate(
                role="coder",
                code_type="python",
                failure_pattern="any",
                name="coder_import_smoke",
                body="assert module is not None",
            ),
            StandardTestTemplate(
                role="judge",
                code_type="python",
                failure_pattern="any",
                name="judge_smoke",
                body="assert hasattr(module, '__dict__')",
            ),
            StandardTestTemplate(
                role="guard",
                code_type="any",
                failure_pattern="unknown_api",
                name="guard_unknown_api",
                body="assert hasattr(module, '__dict__')",
            ),
            StandardTestTemplate(
                role="support",
                code_type="any",
                failure_pattern="flaky",
                name="support_regression",
                body="assert module is not None",
            ),
        ]

    def _normalize(self, text: str) -> str:
        return (text or "any").strip().lower() or "any"


def _safe_test_name(text: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in text or "").strip("_")
    if not clean:
        clean = "test_case"
    if not clean.startswith("test_"):
        clean = f"test_{clean}"
    return clean
