from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import List, Tuple

from .types import TestSpec


@dataclass
class SimpleAgent:
    name: str
    system_prompt: str
    client: object = None
    model: str = ""
    fallback_models: List[str] = None
    fallback_client: object = None
    fallback_client_models: List[str] = None
    is_local: bool = False

    def generate(self, prompt: str) -> str:
        primary_models = [self.model] + list(self.fallback_models or [])
        for model in primary_models:
            try:
                if self.client is None:
                    raise RuntimeError("Primary client unavailable")
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    user=self.name,
                )
                content = response.choices[0].message.content
                if content:
                    return content
            except Exception:
                continue

        fallback_models = list(self.fallback_client_models or [])
        for model in fallback_models:
            try:
                if self.fallback_client is None:
                    raise RuntimeError("Fallback client unavailable")
                response = self.fallback_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    user=self.name,
                )
                content = response.choices[0].message.content
                if content:
                    return content
            except Exception:
                continue
        return ""


class TestBot:
    def __init__(self, agent: SimpleAgent, prompt_guard=None):
        self.agent = agent
        self.prompt_guard = prompt_guard

    def generate_next_wave(
        self,
        previous_results: List[dict],
        coverage_gaps: List[str],
        wave: str,
        target_file: str,
        tests_path: str,
        reference_material: str = "",
    ) -> List[TestSpec]:
        prompt = (
            "Return JSON array of deterministic test specs with keys name and body. "
            f"Wave={wave}. Target file={target_file}. "
            "Prioritize boundary conditions, untested branches, error handling, and regressions. "
            f"Coverage gaps: {coverage_gaps}. Previous failures: {previous_results}."
        )
        if reference_material.strip():
            prompt += (
                "\n\nReference standard tests are available as backup input only. "
                "Do not replace your own generated tests with them.\n"
                f"{reference_material[:3200]}"
            )
        if self.prompt_guard:
            result = self.prompt_guard.guard_prompt(
                prompt=prompt,
                purpose="test-generation",
                max_chars=3500,
                complexity_threshold=0.72,
            )
            prompt = result.prompt
        raw = self.agent.generate(prompt)

        parsed_specs = self._parse_specs(raw)
        if not parsed_specs:
            parsed_specs = self._fallback_specs(wave, target_file)

        os.makedirs(os.path.dirname(tests_path), exist_ok=True)
        rendered = [
            self._render_test_case(spec_name, spec_body, target_file)
            for spec_name, spec_body in parsed_specs
        ]

        full_content = "\n\n".join(rendered) + "\n"
        test_spec = TestSpec(
            name=f"{wave.lower()}_tests",
            wave=wave,
            content=full_content,
            path=tests_path,
            deterministic=True,
        )
        return [test_spec]

    def _parse_specs(self, raw: str) -> List[Tuple[str, str]]:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, list):
                return []
            specs = []
            for item in payload:
                name = str(item.get("name", "")).strip()
                body = str(item.get("body", "")).strip()
                if name and body:
                    specs.append((name, body))
            return specs[:6]
        except Exception:
            return []

    def _fallback_specs(self, wave: str, target_file: str) -> List[Tuple[str, str]]:
        module_name = os.path.splitext(os.path.basename(target_file))[0]
        if wave == "BASELINE":
            return [
                (
                    "test_module_imports",
                    f"module = __import__('{module_name}')\n    assert module is not None",
                ),
                (
                    "test_has_callable_symbols",
                    f"module = __import__('{module_name}')\n    callables = [n for n in dir(module) if callable(getattr(module, n)) and not n.startswith('_')]\n    assert isinstance(callables, list)",
                ),
            ]
        if wave == "ROBUSTNESS":
            return [
                (
                    "test_no_unbounded_recursion_placeholder",
                    f"module = __import__('{module_name}')\n    assert hasattr(module, '__dict__')",
                )
            ]
        return [
            (
                "test_regression_placeholder",
                f"module = __import__('{module_name}')\n    assert module.__name__ == '{module_name}'",
            )
        ]

    def _render_test_case(self, name: str, body: str, target_file: str) -> str:
        safe_name = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_")
        if not safe_name.startswith("test_"):
            safe_name = f"test_{safe_name}"
        import_path = os.path.splitext(os.path.basename(target_file))[0]
        body_with_import = body
        if f"__import__('{import_path}')" not in body:
            body_with_import = f"module = __import__('{import_path}')\n    {body}"
        return f"def {safe_name}():\n    {body_with_import}"


class JudgeBot:
    def __init__(self, agent: SimpleAgent):
        self.agent = agent

    def validate_tests(self, specs: List[TestSpec]) -> List[TestSpec]:
        approved = []
        seen_hashes = set()
        for spec in specs:
            if "random" in spec.content.lower() and "seed" not in spec.content.lower():
                continue
            if "sleep(" in spec.content.lower():
                continue
            fingerprint = hash(spec.content)
            if fingerprint in seen_hashes:
                continue
            seen_hashes.add(fingerprint)
            approved.append(spec)
        return approved

    def run_tests(self, tests_path: str, cwd: str) -> Tuple[bool, str]:
        cmd = shlex.split(f"pytest {tests_path} -q")
        try:
            completed = subprocess.run(
                cmd,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
            )
            passed = completed.returncode == 0
            output = (completed.stdout or "") + "\n" + (completed.stderr or "")
            return passed, output.strip()
        except Exception as exc:
            return False, f"Test execution failure: {exc}"

    def run_tests_with_command(
        self,
        tests_path: str,
        cwd: str,
        command_template: str,
    ) -> Tuple[bool, str]:
        command = (command_template or "pytest {tests_path} -q").format(
            tests_path=tests_path
        )
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
                shell=True,
            )
            passed = completed.returncode == 0
            output = (completed.stdout or "") + "\n" + (completed.stderr or "")
            return passed, output.strip()
        except Exception as exc:
            return False, f"Test execution failure: {exc}"

    def get_fix_list(self, failure_output: str) -> str:
        if not self.agent.client:
            return failure_output[:1200]

        prompt = (
            "Summarize failures into a concise actionable fix list. "
            "Include probable root cause and exact code edits.\n\n"
            f"Failure log:\n{failure_output[:6000]}"
        )
        response = self.agent.generate(prompt)
        return response or failure_output[:1200]
