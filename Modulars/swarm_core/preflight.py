from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .bots import SimpleAgent
from .types import RunConfig, TaskGoal


@dataclass
class PrepProposal:
    proposal_id: str
    agent_name: str
    goal_key: str
    title: str
    suggested_action: str
    expected_benefit: str
    risk_if_wrong: str
    validation_plan: str
    config_overrides: Dict[str, Any] = field(default_factory=dict)
    requested_tools: List[str] = field(default_factory=list)
    requested_updates: List[str] = field(default_factory=list)
    status: str = "PENDING"
    review_note: str = ""
    validation_note: str = ""


@dataclass
class PrepBundle:
    bundle_id: str
    created_at: str
    goal_prompt: str
    target_files: List[str] = field(default_factory=list)
    language: str = "general"
    proposals: List[PrepProposal] = field(default_factory=list)
    status: str = "PENDING"
    ready_to_launch: bool = False
    launch_overrides: Dict[str, Any] = field(default_factory=dict)
    requested_tools: List[str] = field(default_factory=list)
    requested_updates: List[str] = field(default_factory=list)
    validation_note: str = ""


class SwarmPreflightManager:
    def __init__(
        self,
        root_dir: str,
        seed_agent: Optional[SimpleAgent] = None,
        directive_agent: Optional[SimpleAgent] = None,
        stability_agent: Optional[SimpleAgent] = None,
    ):
        self.root_dir = root_dir
        self.seed_agent = seed_agent
        self.directive_agent = directive_agent
        self.stability_agent = stability_agent
        self._bundles: Dict[str, PrepBundle] = {}
        self._allowed_override_keys = set(RunConfig().__dataclass_fields__.keys())
        self._required_testing_tools = [
            "pytest",
            "coverage",
            "dry-run harness",
            "failure replay",
            "artifact review",
        ]
        self._required_reporting_tools = [
            "progress log",
            "status snapshot",
            "spawn report",
            "efficiency report",
            "failure report",
        ]
        self._required_diagnostics_tools = [
            "log inspection",
            "diff review",
            "failure memory search",
            "hallucination report",
            "artifact browser",
        ]

    def build_bundle(self, goal: TaskGoal, config: RunConfig) -> PrepBundle:
        bundle_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        templates = self._templates(config)
        proposals = []
        for spec in templates:
            proposal = self._build_proposal(
                bundle_id=bundle_id,
                goal=goal,
                spec=spec,
            )
            proposals.append(proposal)

        bundle = PrepBundle(
            bundle_id=bundle_id,
            created_at=self._ts(),
            goal_prompt=goal.prompt,
            target_files=list(goal.target_files),
            language=goal.language,
            proposals=proposals,
        )
        self._bundles[bundle_id] = bundle
        self.auto_review_bundle(bundle_id)
        self._write_bundle(bundle)
        return bundle

    def auto_review_bundle(self, bundle_id: str) -> PrepBundle:
        bundle = self._require_bundle(bundle_id)
        for proposal in bundle.proposals:
            issues = self._validate_proposal(proposal)
            if issues:
                proposal.status = "REVISE"
                proposal.review_note = "; ".join(issues)
            else:
                proposal.status = "APPROVED"
                proposal.review_note = "Codex auto-approved: bounded, testable, and advisory only."
        return self.validate_bundle(bundle_id)

    def review_proposal(
        self,
        bundle_id: str,
        target: str,
        decision: str,
        note: str = "",
    ) -> PrepBundle:
        bundle = self._require_bundle(bundle_id)
        normalized = (decision or "PENDING").upper()
        matches = self._target_proposals(bundle, target)
        if not matches:
            raise ValueError(f"Unknown prep target: {target}")

        for proposal in matches:
            proposal.status = normalized
            proposal.review_note = note[:400]
        self._refresh_bundle_status(bundle)
        self.validate_bundle(bundle_id)
        self._write_bundle(bundle)
        return bundle

    def validate_bundle(self, bundle_id: str) -> PrepBundle:
        bundle = self._require_bundle(bundle_id)
        approved = [item for item in bundle.proposals if item.status == "APPROVED"]
        validation_notes: List[str] = []
        valid = True

        for proposal in bundle.proposals:
            issues = self._validate_proposal(proposal)
            if issues:
                valid = False
                proposal.validation_note = "; ".join(issues)
                validation_notes.append(f"{proposal.agent_name}: {proposal.validation_note}")
                if proposal.status == "APPROVED":
                    proposal.status = "REVISE"
            else:
                proposal.validation_note = "structure ok"
                if proposal.status == "APPROVED":
                    validation_notes.append(f"{proposal.agent_name}: approved")

        bundle.launch_overrides = self._merge_overrides(approved) if valid and approved else {}
        bundle.ready_to_launch = bool(valid and len(approved) == len(bundle.proposals))
        bundle.requested_tools = self._unique_preserve_order(
            list(self._required_testing_tools)
            + list(self._required_reporting_tools)
            + list(self._required_diagnostics_tools)
            + [
                tool
                for proposal in bundle.proposals
                for tool in proposal.requested_tools
            ]
        )
        bundle.requested_updates = self._unique_preserve_order(
            item
            for proposal in bundle.proposals
            for item in proposal.requested_updates
        )
        if bundle.ready_to_launch:
            bundle.status = "READY"
            validation_notes.append("Bundle ready to launch after approval gate.")
        elif any(item.status == "DENIED" for item in bundle.proposals):
            bundle.status = "DENIED"
        elif any(item.status == "REVISE" for item in bundle.proposals):
            bundle.status = "REVISE"
        elif approved:
            bundle.status = "APPROVED"
        else:
            bundle.status = "PENDING"
        bundle.validation_note = " | ".join(validation_notes)[:600]
        self._write_bundle(bundle)
        return bundle

    def launch_overrides(self, bundle_id: str) -> Dict[str, Any]:
        bundle = self._require_bundle(bundle_id)
        if not bundle.ready_to_launch:
            return {}
        return dict(bundle.launch_overrides)

    def summarize_bundle(self, bundle_id: str) -> Dict[str, Any]:
        bundle = self._require_bundle(bundle_id)
        return self._bundle_payload(bundle)

    def latest_bundle(self) -> Optional[PrepBundle]:
        if not self._bundles:
            return None
        latest_id = sorted(self._bundles.keys())[-1]
        return self._bundles[latest_id]

    def _templates(self, config: RunConfig) -> List[Dict[str, Any]]:
        return [
            {
                "goal_key": "seed",
                "agent_name": "SeedPrepBot",
                "agent": self.seed_agent,
                "title": "Seed the analyzers with compact failure exemplars",
                "suggested_action": (
                    "Build a short seed pack of representative failures, one known-good example, "
                    "and one boundary case so analyzers have stable input without slowing the main lane."
                ),
                "expected_benefit": (
                    "Improves failure-memory and compaction quality while keeping the prompt space small."
                ),
                "risk_if_wrong": (
                    "If the seed pack is too large or noisy, it will consume time and blur the swarm's starting objective."
                ),
                "validation_plan": (
                    "Verify the seed pack stays under 8 entries, each entry maps to a real failure class, "
                    "and the first dry-run yields at least one actionable rule."
                ),
                "config_overrides": {
                    "memory_distillation_enabled": True,
                    "memory_rule_limit": min(8, max(4, config.memory_rule_limit)),
                    "memory_breadcrumb_limit": min(6, max(3, config.memory_breadcrumb_limit)),
                },
                "requested_tools": ["pytest", "failure_memory", "progress log"],
                "testing_tools": ["pytest", "coverage", "dry-run harness", "artifact review"],
                "reporting_tools": ["progress log", "status snapshot", "failure report"],
                "diagnostics_tools": ["log inspection", "diff review", "failure memory search"],
                "requested_updates": [
                    "compact seed pack for analyzers",
                    "small fixture set for baseline failures",
                ],
            },
            {
                "goal_key": "directive",
                "agent_name": "DirectivePrepBot",
                "agent": self.directive_agent,
                "title": "Tighten the approval-gated directive flow",
                "suggested_action": (
                    "Convert the three goals into file-bounded prompts and force an approve/deny/revise step before launch."
                ),
                "expected_benefit": (
                    "Reduces vague asks and keeps specialists from receiving oversized tasks."
                ),
                "risk_if_wrong": (
                    "If too strict, the swarm may stall waiting for unnecessary approvals."
                ),
                "validation_plan": (
                    "Confirm each handoff brief contains only objective, affected files, and one success criterion."
                ),
                "config_overrides": {
                    "directive_mode_enabled": True,
                    "need_to_know_enabled": True,
                    "rosetta_enabled": True,
                    "testing_agents_exempt_from_directives": True,
                    "max_problem_scope_chars": min(1600, max(900, config.max_problem_scope_chars)),
                },
                "requested_tools": ["status view", "handoff review", "prompt_guard"],
                "testing_tools": ["dry-run harness", "failure replay", "artifact review"],
                "reporting_tools": ["status snapshot", "spawn report", "progress log"],
                "diagnostics_tools": ["artifact browser", "hallucination report", "failure memory search"],
                "requested_updates": [
                    "approval-gated directive workflow",
                    "compact launch brief in the chat UI",
                ],
            },
            {
                "goal_key": "stability",
                "agent_name": "StabilityPrepBot",
                "agent": self.stability_agent,
                "title": "Lock down collapse prevention and population control",
                "suggested_action": (
                    "Set lower initial agent ceilings, define earlier deflect triggers, and require fail-fast on repeated failed waves."
                ),
                "expected_benefit": (
                    "Prevents snowballing when errors or hallucinations begin to spike."
                ),
                "risk_if_wrong": (
                    "If thresholds are too low, the swarm will redirect too often and under-deliver."
                ),
                "validation_plan": (
                    "Simulate repeated failures and confirm the guard deflects before open handoffs and retries escalate."
                ),
                "config_overrides": {
                    "stability_guard_enabled": True,
                    "population_control_enabled": True,
                    "max_concurrent_agents": min(4, max(3, config.max_concurrent_agents)),
                    "guard_max_open_handoffs": min(4, max(3, config.guard_max_open_handoffs)),
                    "max_consecutive_failed_waves": min(6, max(4, config.max_consecutive_failed_waves)),
                },
                "requested_tools": ["failure trend monitor", "spawn report", "efficiency report"],
                "testing_tools": ["coverage", "failure replay", "artifact review"],
                "reporting_tools": ["spawn report", "efficiency report", "failure report"],
                "diagnostics_tools": ["log inspection", "diff review", "hallucination report"],
                "requested_updates": [
                    "earlier deflect threshold",
                    "launch readiness signal",
                ],
            },
        ]

    def _build_proposal(
        self,
        bundle_id: str,
        goal: TaskGoal,
        spec: Dict[str, Any],
    ) -> PrepProposal:
        default_payload = {
            "title": spec["title"],
            "suggested_action": spec["suggested_action"],
            "expected_benefit": spec["expected_benefit"],
            "risk_if_wrong": spec["risk_if_wrong"],
            "validation_plan": spec["validation_plan"],
            "config_overrides": spec["config_overrides"],
            "requested_tools": spec.get("requested_tools", []),
            "requested_updates": spec.get("requested_updates", []),
        }

        agent = spec.get("agent")
        if agent is not None:
            prompt = self._proposal_prompt(goal=goal, spec=spec)
            response = ""
            try:
                response = agent.generate(prompt)
            except Exception:
                response = ""
            parsed = self._parse_json_payload(response)
            if parsed:
                for key in default_payload:
                    if key in parsed and parsed[key]:
                        default_payload[key] = parsed[key]

        clean_overrides = self._clean_overrides(default_payload.get("config_overrides", {}))
        proposal_id = f"{bundle_id}-{spec['goal_key']}"
        return PrepProposal(
            proposal_id=proposal_id,
            agent_name=spec["agent_name"],
            goal_key=spec["goal_key"],
            title=self._clip(str(default_payload["title"]), 120),
            suggested_action=self._clip(str(default_payload["suggested_action"]), 360),
            expected_benefit=self._clip(str(default_payload["expected_benefit"]), 220),
            risk_if_wrong=self._clip(str(default_payload["risk_if_wrong"]), 220),
            validation_plan=self._clip(str(default_payload["validation_plan"]), 260),
            config_overrides=clean_overrides,
            requested_tools=self._clean_list(default_payload.get("requested_tools", [])),
            requested_updates=self._clean_list(default_payload.get("requested_updates", [])),
        )

    def _proposal_prompt(self, goal: TaskGoal, spec: Dict[str, Any]) -> str:
        return (
            "Return JSON only with keys title, suggested_action, expected_benefit, "
            "risk_if_wrong, validation_plan, config_overrides, requested_tools, requested_updates. "
            "Do not write code. Keep suggestions compact, concrete, and advisory only.\n"
            f"Goal: {goal.prompt}\n"
            f"Target files: {goal.target_files}\n"
            f"Focus area: {spec['goal_key']}\n"
            f"Suggested action seed: {spec['suggested_action']}\n"
        )

    def _parse_json_payload(self, raw: str) -> Dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            return {}
        if text.startswith("```"):
            text = text.strip("`")
        candidates = [text]
        if "{" in text and "}" in text:
            candidates.insert(0, text[text.find("{") : text.rfind("}") + 1])
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return {}

    def _clean_overrides(self, overrides: Any) -> Dict[str, Any]:
        if not isinstance(overrides, dict):
            return {}
        cleaned: Dict[str, Any] = {}
        for key, value in overrides.items():
            if key not in self._allowed_override_keys:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                cleaned[key] = value
            elif isinstance(value, list):
                cleaned[key] = [item for item in value if isinstance(item, (str, int, float, bool))]
        return cleaned

    def _validate_proposal(self, proposal: PrepProposal) -> List[str]:
        issues: List[str] = []
        if not proposal.title.strip():
            issues.append("missing title")
        if not proposal.suggested_action.strip():
            issues.append("missing suggested_action")
        if not proposal.expected_benefit.strip():
            issues.append("missing expected_benefit")
        if not proposal.risk_if_wrong.strip():
            issues.append("missing risk_if_wrong")
        if not proposal.validation_plan.strip():
            issues.append("missing validation_plan")
        unknown_keys = [key for key in proposal.config_overrides if key not in self._allowed_override_keys]
        if unknown_keys:
            issues.append(f"unknown override keys: {', '.join(sorted(unknown_keys))}")
        return issues

    def _merge_overrides(self, approved: List[PrepProposal]) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        for proposal in approved:
            merged.update(proposal.config_overrides)
        return merged

    def _target_proposals(self, bundle: PrepBundle, target: str) -> List[PrepProposal]:
        token = (target or "").strip().lower()
        if not token:
            return []
        if token == "all":
            return list(bundle.proposals)
        return [
            item
            for item in bundle.proposals
            if item.agent_name.lower() == token
            or item.goal_key.lower() == token
            or item.proposal_id.lower() == token
        ]

    def _refresh_bundle_status(self, bundle: PrepBundle) -> None:
        statuses = {item.status for item in bundle.proposals}
        if statuses == {"APPROVED"}:
            bundle.status = "APPROVED"
        elif "DENIED" in statuses:
            bundle.status = "DENIED"
        elif "REVISE" in statuses:
            bundle.status = "REVISE"
        elif statuses == {"PENDING"}:
            bundle.status = "PENDING"
        else:
            bundle.status = "PENDING"
        bundle.ready_to_launch = False

    def _clean_list(self, items: Any) -> List[str]:
        if not isinstance(items, list):
            return []
        cleaned: List[str] = []
        for item in items:
            if isinstance(item, str):
                value = item.strip()
                if value:
                    cleaned.append(self._clip(value, 140))
        return self._unique_preserve_order(cleaned)

    def _unique_preserve_order(self, items) -> List[str]:
        seen = set()
        ordered: List[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    def _bundle_payload(self, bundle: PrepBundle) -> Dict[str, Any]:
        payload = asdict(bundle)
        payload["proposals"] = [
            {
                "proposal_id": proposal.proposal_id,
                "agent_name": proposal.agent_name,
                "goal_key": proposal.goal_key,
                "title": proposal.title,
                "suggested_action": proposal.suggested_action,
                "expected_benefit": proposal.expected_benefit,
                "risk_if_wrong": proposal.risk_if_wrong,
                "validation_plan": proposal.validation_plan,
                "config_overrides": proposal.config_overrides,
                "requested_tools": proposal.requested_tools,
                "requested_updates": proposal.requested_updates,
                "status": proposal.status,
                "review_note": proposal.review_note,
                "validation_note": proposal.validation_note,
            }
            for proposal in bundle.proposals
        ]
        payload["requested_tools"] = bundle.requested_tools
        payload["requested_updates"] = bundle.requested_updates
        payload["required_testing_tools"] = list(self._required_testing_tools)
        payload["required_reporting_tools"] = list(self._required_reporting_tools)
        payload["required_diagnostics_tools"] = list(self._required_diagnostics_tools)
        return payload

    def _write_bundle(self, bundle: PrepBundle) -> None:
        base_dir = os.path.join(self.root_dir, "swarm_runs", "preflight", bundle.bundle_id)
        os.makedirs(base_dir, exist_ok=True)
        with open(os.path.join(base_dir, "prep_bundle.json"), "w", encoding="utf-8") as handle:
            json.dump(self._bundle_payload(bundle), handle, indent=2, ensure_ascii=True)
        with open(os.path.join(base_dir, "prep_bundle.md"), "w", encoding="utf-8") as handle:
            handle.write(self._render_markdown(bundle))

    def _render_markdown(self, bundle: PrepBundle) -> str:
        lines = [
            "# Preflight Bundle",
            "",
            f"Bundle ID: `{bundle.bundle_id}`",
            f"Created: {bundle.created_at}",
            f"Goal: {bundle.goal_prompt}",
            f"Target Files: {', '.join(bundle.target_files) if bundle.target_files else 'none'}",
            f"Language: {bundle.language}",
            f"Status: {bundle.status}",
            f"Ready To Launch: {bundle.ready_to_launch}",
            "",
            "## Proposals",
        ]
        for proposal in bundle.proposals:
            lines.extend(
                [
                    f"### {proposal.agent_name}",
                    f"- Goal Key: {proposal.goal_key}",
                    f"- Status: {proposal.status}",
                    f"- Title: {proposal.title}",
                    f"- Suggested Action: {proposal.suggested_action}",
                    f"- Expected Benefit: {proposal.expected_benefit}",
                    f"- Risk If Wrong: {proposal.risk_if_wrong}",
                    f"- Validation Plan: {proposal.validation_plan}",
                    f"- Review Note: {proposal.review_note or 'n/a'}",
                    f"- Validation Note: {proposal.validation_note or 'n/a'}",
                    f"- Config Overrides: {json.dumps(proposal.config_overrides, ensure_ascii=True)}",
                    "",
                ]
            )
        lines.extend(
            [
                "## Validation",
                "",
                bundle.validation_note or "n/a",
                "",
                "## Request List",
                "",
                f"Requested Tools: {', '.join(bundle.requested_tools) if bundle.requested_tools else 'none'}",
                f"Required Testing Tools: {', '.join(self._required_testing_tools)}",
                f"Required Reporting Tools: {', '.join(self._required_reporting_tools)}",
                f"Required Diagnostics Tools: {', '.join(self._required_diagnostics_tools)}",
                f"Requested Updates: {', '.join(bundle.requested_updates) if bundle.requested_updates else 'none'}",
                "",
            ]
        )
        return "\n".join(lines)

    def _require_bundle(self, bundle_id: str) -> PrepBundle:
        if bundle_id not in self._bundles:
            raise ValueError(f"Unknown preflight bundle: {bundle_id}")
        return self._bundles[bundle_id]

    def _clip(self, text: str, limit: int) -> str:
        return (text or "").strip()[:limit]

    def _ts(self) -> str:
        return datetime.utcnow().isoformat() + "Z"
