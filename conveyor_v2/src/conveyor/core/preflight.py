"""Preflight analysis — bundle generation, proposals, tool requirements.

Analyzes a task goal before execution to:
  - Determine what tools the swarm will need
  - Generate proposals from specialist agents
  - Assess readiness for launch

Legacy source: preflight methods inside SwarmController.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PreflightProposal:
    """A single preflight proposal from a specialist agent.

    Matches the legacy prep_proposals list structure:
      agent_name, status, title, suggested_action,
      requested_tools, requested_updates
    """
    agent_name: str
    status: str = "PENDING"
    title: str = ""
    suggested_action: str = ""
    requested_tools: list[str] = field(default_factory=list)
    requested_updates: list[str] = field(default_factory=list)


@dataclass
class PreflightBundle:
    """A complete preflight analysis package.

    Matches the legacy prep_* status keys.
    """
    bundle_id: str = ""
    goal: str = ""
    status: str = "NONE"  # NONE, PENDING, READY, LAUNCHED
    ready_to_launch: bool = False
    proposals: list[PreflightProposal] = field(default_factory=list)
    requested_tools: list[str] = field(default_factory=list)
    requested_updates: list[str] = field(default_factory=list)
    required_testing_tools: list[str] = field(default_factory=list)
    required_reporting_tools: list[str] = field(default_factory=list)
    required_diagnostics_tools: list[str] = field(default_factory=list)
    error: str = ""


class PreflightAnalyzer:
    """Analyzes task goals and generates preflight bundles.

    Thread-safe: each analysis produces a new bundle.
    """

    def __init__(self, tools: dict[str, Any] | None = None) -> None:
        """
        Args:
            tools: Dict mapping tool name to tool metadata.
                   None means no tools are available.
        """
        self.tools = tools or {}

    def generate_bundle(self, goal_text: str) -> PreflightBundle:
        """Generate a preflight bundle for a task goal.

        Analyzes the goal text to determine requirements.
        This is intentionally minimal — the real analysis
        happens when the swarm agents review the bundle.

        Args:
            goal_text: The task description to analyze.

        Returns:
            PreflightBundle with initial analysis.
        """
        bundle = PreflightBundle(
            bundle_id=uuid.uuid4().hex[:12],
            goal=goal_text,
            status="PENDING",
        )

        # --- Tool requirement detection ---
        normalized = goal_text.lower()
        if any(kw in normalized for kw in ("test", "pytest", "verify")):
            bundle.required_testing_tools.append("test_runner")
            bundle.requested_tools.append("test_runner")

        if any(kw in normalized for kw in ("report", "summary", "analyze")):
            bundle.required_reporting_tools.append("reporter")
            bundle.requested_tools.append("reporter")

        if any(kw in normalized for kw in ("diagnose", "debug", "inspect")):
            bundle.required_diagnostics_tools.append("inspector")
            bundle.requested_tools.append("inspector")

        if any(kw in normalized for kw in ("code", "implement", "build")):
            bundle.requested_tools.append("code_executor")
            bundle.required_testing_tools.append("test_runner")

        # Readiness: at least one tool must be available
        has_tools = any(t in self.tools for t in bundle.requested_tools)
        if not bundle.requested_tools:
            bundle.status = "READY"
            bundle.ready_to_launch = True
        elif has_tools:
            bundle.status = "READY"
            bundle.ready_to_launch = True
        else:
            bundle.status = "PENDING"

        return bundle

    def review_decision(
        self, bundle: PreflightBundle, decision: str, note: str = ""
    ) -> PreflightBundle:
        """Process a user decision on a preflight bundle.

        Args:
            bundle: The bundle being reviewed.
            decision: "approve" or "reject".
            note: Optional human reason.

        Returns:
            Updated bundle.
        """
        if decision.lower() == "approve":
            bundle.status = "LAUNCHED"
            bundle.ready_to_launch = True
        elif decision.lower() == "reject":
            bundle.status = "NONE"
            bundle.ready_to_launch = False
            bundle.error = note or "Rejected by user"
        else:
            bundle.error = f"Unknown decision: {decision}"

        return bundle

    def get_status(self, bundle: PreflightBundle | None) -> dict[str, Any]:
        """Return preflight status dict matching legacy keys.

        Args:
            bundle: Current bundle, or None if no preflight is active.

        Returns:
            Dict with all prep_* keys ready for inclusion in controller.status().
        """
        if bundle is None:
            return {
                "prep_bundle_id": "",
                "prep_goal": "",
                "prep_status": "NONE",
                "prep_ready_to_launch": False,
                "prep_requested_tools": [],
                "prep_required_testing_tools": [],
                "prep_required_reporting_tools": [],
                "prep_required_diagnostics_tools": [],
                "prep_requested_updates": [],
                "prep_proposals": [],
            }

        proposals_dict = [
            {
                "agent_name": p.agent_name,
                "status": p.status,
                "title": p.title,
                "suggested_action": p.suggested_action,
                "requested_tools": p.requested_tools,
                "requested_updates": p.requested_updates,
            }
            for p in bundle.proposals
        ]

        return {
            "prep_bundle_id": bundle.bundle_id,
            "prep_goal": bundle.goal,
            "prep_status": bundle.status,
            "prep_ready_to_launch": bundle.ready_to_launch,
            "prep_requested_tools": bundle.requested_tools,
            "prep_required_testing_tools": bundle.required_testing_tools,
            "prep_required_reporting_tools": bundle.required_reporting_tools,
            "prep_required_diagnostics_tools": bundle.required_diagnostics_tools,
            "prep_requested_updates": bundle.requested_updates,
            "prep_proposals": proposals_dict,
        }
