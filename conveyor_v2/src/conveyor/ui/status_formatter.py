"""Status formatter — converts the 100+ key status dict to human-readable text.

Extracted from the legacy _send_status() function in app.py (the massive
string-formatting block of ~120 lines).

This module has ZERO dependencies — pure dict-to-text formatting.
"""

from __future__ import annotations

from typing import Any


def format_status(status: dict[str, Any], prefix: str = "Swarm status") -> str:
    """Format a swarm status dict into a human-readable text block.

    Matches the legacy output format exactly so the parallel run phase
    can diff outputs directly.

    Args:
        status: The flat dict from SwarmStatus.flatten() or controller.status().
        prefix: Title line (e.g., "Swarm status", "Paused", "Resumed").
    """
    # --- Grouping helpers ---
    def section(title: str) -> str:
        return f"\n[{title}]"

    def kv(key: str, value: Any) -> str:
        return f"{key}: {_fmt(value)}"

    def list_kv(key: str, values: list[str]) -> str:
        items = "\n".join(f"  - {v}" for v in values) if values else "  (empty)"
        return f"{key}:\n{items}"

    def fmt_list(values: list[str]) -> str:
        return ", ".join(values) if values else "none"

    # --- Chat lane ---
    chat_lines = [
        section("Chat Lane"),
        kv("Mode", status.get("chat_mode", "chat")),
        kv("Turns", status.get("chat_turn_count", 0)),
        kv("Background Queue Depth", status.get("background_run_queue_depth", 0)),
        kv("Background Active Goal", _get_or_na(status, "background_run_active_goal")),
        kv("Background Last Run", _get_or_na(status, "background_run_last_run_id")),
        kv("Background Last Status", _get_or_na(status, "background_run_last_status")),
        kv("Latest Architect Instruction", _truncate(_get_or_na(status, "latest_architect_instruction"), 100)),
    ]

    # --- Swarm health ---
    health_lines = [
        section("Swarm Health"),
        kv("State", status.get("state", "idle")),
        kv("Phase", status.get("phase", "preflight")),
        kv("Wave", f"{status.get('wave_name', '')} (#{status.get('wave_index', 0)})"),
        kv("Active Topology", fmt_list(status.get("active_topology", []))),
        kv("Spawns", status.get("spawn_count", 0)),
        kv("Open Handoffs", status.get("open_handoff_count", 0)),
        kv("Failure Streak", status.get("returned_failure_streak", 0)),
        kv("Hallucination Confidence", f"{status.get('hallucination_confidence', 1.0):.3f}"),
        kv("Hallucination Alerts", status.get("hallucination_alert_count", 0)),
        kv("Latest Alert", _get_or_na(status, "latest_hallucination_alert")),
        kv("Team Ideas", status.get("team_ideas_count", 0)),
        kv("Recommendation", _get_or_na(status, "recommendation")),
    ]

    # --- Memory ---
    mem_lines = [
        section("Memory"),
        kv("Packets", status.get("local_memory_packet_count", 0)),
        kv("Reuses", status.get("local_memory_reuse_count", 0)),
        kv("Invalidations", status.get("local_memory_invalidations", 0)),
        kv("Pressure", f"{status.get('local_memory_pressure', 0.0):.2f}"),
        kv("Compaction Triggered", status.get("local_memory_compaction_triggered", False)),
        kv("Distillation", _enabled_str(status.get("memory_distillation_enabled", True))),
        kv("Adaptive", _enabled_str(status.get("adaptive_compaction_enabled", True))),
        kv("Interval (waves)", status.get("compaction_interval_waves", 3)),
        kv("Rule Limit", status.get("memory_rule_limit", 6)),
        kv("Breadcrumb Limit", status.get("memory_breadcrumb_limit", 5)),
        kv("Generation Records", status.get("generation_memory_records", 0)),
        kv("Generation Restores", status.get("generation_memory_restores", 0)),
        kv("Latest Generation", _get_or_na(status, "generation_memory_latest_generation_id")),
    ]

    # --- Model routing ---
    model_lines = [
        section("Model Routing"),
        kv("Local Host", _get_or_na(status, "local_model_host")),
        kv("Latest Model", _get_or_na(status, "latest_local_model_name")),
        kv("Latest Lane", _get_or_na(status, "latest_local_model_lane")),
        kv("Inflight", status.get("local_api_inflight", 0)),
        kv("Throttle Hits", status.get("local_api_throttle_hits", 0)),
        kv("User Waiting", status.get("local_api_user_waiting", 0)),
        kv("Swarm Waiting", status.get("local_api_swarm_waiting", 0)),
    ]

    routes = status.get("local_model_routes", {})
    if routes:
        route_lines = []
        for role, route in sorted(routes.items()):
            primary = route.get("primary", "n/a") if isinstance(route, dict) else str(route)
            fallback = route.get("fallback", []) if isinstance(route, dict) else []
            fb_str = f" -> {', '.join(fallback)}" if fallback else ""
            route_lines.append(f"  - {role}: {primary}{fb_str}")
        model_lines.append("Routes:\n" + "\n".join(route_lines))

    # --- Preflight ---
    prep_lines = [
        section("Preflight"),
        kv("Bundle", _get_or_na(status, "prep_bundle_id")),
        kv("Goal", _get_or_na(status, "prep_goal")),
        kv("Status", status.get("prep_status", "NONE")),
        kv("Ready", status.get("prep_ready_to_launch", False)),
        kv("Requested Tools", fmt_list(status.get("prep_requested_tools", []))),
        kv("Requested Updates", fmt_list(status.get("prep_requested_updates", []))),
    ]

    proposals = status.get("prep_proposals", [])
    if proposals:
        prep_lines.append("Proposals:")
        for p in proposals[:5]:
            prep_lines.append(
                f"  - {_safe(p, 'agent_name', 'unknown')} [{_safe(p, 'status', 'PENDING')}]: "
                f"{_safe(p, 'title', '')}"
            )

    # --- Rehearsal ---
    reh_lines = [
        section("Rehearsal"),
        kv("State", status.get("rehearsal_state", "IDLE")),
        kv("Profile", _get_or_na(status, "rehearsal_profile")),
        kv("Report", _get_or_na(status, "rehearsal_report_path")),
        kv("Manifest", _get_or_na(status, "rehearsal_manifest_path")),
    ]

    # --- Guards ---
    guard_lines = [
        section("Guards"),
        kv("Guard Mode", status.get("guard_mode", "NORMAL")),
        kv("Interventions", status.get("guard_interventions", 0)),
        kv("Ramp Level", status.get("ramp_level", 0)),
        kv("Latest Action", _get_or_na(status, "latest_guard_action")),
        kv("Latest Reason", _get_or_na(status, "latest_guard_reason")),
        kv("Handoff Mismatches", status.get("handoff_mismatch_count", 0)),
        kv("Rosetta Warnings", status.get("rosetta_warning_count", 0)),
    ]

    # --- Skills ---
    skill_lines = [
        section("Skills"),
        kv("Active", status.get("active_skill_count", 0)),
        kv("Retools", status.get("skill_retool_count", 0)),
        kv("Latest Event", _get_or_na(status, "latest_skill_event")),
    ]

    # --- Tests ---
    test_lines = [
        section("Tests"),
        kv("Command", status.get("test_command", "n/a")),
        kv("Fallbacks", status.get("standard_test_fallback_count", 0)),
        kv("Pack", _get_or_na(status, "latest_standard_test_pack")),
    ]

    # --- Warnings / Suggestions ---
    guidance_lines = [
        section("Guidance"),
    ]
    warnings = status.get("ui_warnings", [])
    if warnings:
        guidance_lines.append("Warnings:")
        for w in warnings:
            guidance_lines.append(f"  - {w}")
    suggestions = status.get("ui_suggestions", [])
    if suggestions:
        guidance_lines.append("Suggestions:")
        for s in suggestions:
            guidance_lines.append(f"  - {s}")
    if not warnings and not suggestions:
        guidance_lines.append("  (no warnings or suggestions)")

    # --- Assemble ---
    all_sections = [prefix, ""]
    all_sections.extend(chat_lines)
    all_sections.extend(health_lines)
    all_sections.extend(mem_lines)
    all_sections.extend(model_lines)
    all_sections.extend(prep_lines)
    all_sections.extend(reh_lines)
    all_sections.extend(guard_lines)
    all_sections.extend(skill_lines)
    all_sections.extend(test_lines)
    all_sections.extend(guidance_lines)

    return "\n".join(all_sections)


# -----------------------------------------------------------------------
# Helpers

def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}" if value != int(value) else str(int(value))
    return str(value)


def _get_or_na(status: dict[str, Any], key: str) -> str:
    v = status.get(key)
    if v is None or v == "":
        return "n/a"
    return str(v)


def _safe(obj: dict | None, key: str, default: str) -> str:
    if obj is None or not isinstance(obj, dict):
        return default
    v = obj.get(key)
    return str(v) if v is not None else default


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _enabled_str(flag: bool) -> str:
    return "enabled" if flag else "disabled"
