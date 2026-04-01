"""Compaction and distillation logic for memory management.

Handles adaptive compaction triggers, memory rule pruning,
and breadcrumb generation. Separate from memory.py so the
compaction decision logic is isolated and testable.

Legacy source: compaction-related code inside SwarmController.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CompactionConfig:
    """Configuration for compaction behaviour.

    Matches the legacy config keys:
      memory_rule_limit
      memory_breadcrumb_limit
      compaction_interval_waves
      adaptive_compaction_enabled
      memory_distillation_enabled
    """
    memory_rule_limit: int = 6
    memory_breadcrumb_limit: int = 5
    compaction_interval_waves: int = 3
    adaptive_compaction_enabled: bool = True
    memory_distillation_enabled: bool = True


@dataclass
class CompactionResult:
    """Result of a compaction run."""
    rules_kept: int = 0
    breadcrumbs_kept: int = 0
    packets_compacted: int = 0
    reason: str = ""
    was_adaptive: bool = False
    wave_interval: int = 0


def should_compact_on_interval(wave_index: int, interval: int) -> bool:
    """Check if compaction should fire based on wave interval.

    Legacy behaviour: compaction runs every N waves (configurable).
    With adaptive enabled, the interval may change dynamically.

    Args:
        wave_index: Current wave number (1-based).
        interval: Number of waves between compactions.

    Returns:
        True if wave_index is a multiple of interval.
    """
    if interval <= 0:
        return False
    return wave_index % interval == 0


def run_compaction(
    config: CompactionConfig,
    memory_pressure: float,
    rule_count: int,
    breadcrumb_count: int,
    packet_count: int,
) -> CompactionResult:
    """Execute a compaction cycle.

    This function determines how many rules, breadcrumbs, and
    packets to prune based on the current counts and configured
    limits. It does NOT directly mutate any data — it returns
    a CompactionResult describing what SHOULD be done.

    This is the "decision" layer — the memory.py module handles
    the actual packet invalidation based on this result.

    Args:
        config: Compaction configuration.
        memory_pressure: Current memory pressure (0.0-1.0).
        rule_count: Current number of stored rules.
        breadcrumb_count: Current number of breadcrumbs.
        packet_count: Current number of stored packets.

    Returns:
        CompactionResult describing the pruning action.
    """
    result = CompactionResult()

    if not config.memory_distillation_enabled:
        result.reason = "distillation disabled"
        return result

    # --- Rule pruning ---
    if rule_count > config.memory_rule_limit:
        excess = rule_count - config.memory_rule_limit
        result.rules_kept = config.memory_rule_limit
        result.packets_compacted += excess
        result.reason = "rule limit exceeded"

    # --- Breadcrumb pruning ---
    if breadcrumb_count > config.memory_breadcrumb_limit:
        excess = breadcrumb_count - config.memory_breadcrumb_limit
        result.breadcrumbs_kept = config.memory_breadcrumb_limit
        result.reason += " + breadcrumb pruning" if result.reason else "breadcrumb limit exceeded"

    # --- Adaptive interval adjustment ---
    if config.adaptive_compaction_enabled and memory_pressure > 0.8:
        result.was_adaptive = True
        # High pressure → more aggressive compaction
        result.wave_interval = max(1, config.compaction_interval_waves - 1)
        if not result.reason:
            result.reason = "adaptive: high pressure"
    else:
        result.wave_interval = config.compaction_interval_waves

    if not result.reason:
        result.reason = "no compaction needed"
        result.rules_kept = rule_count
        result.breadcrumbs_kept = breadcrumb_count

    return result


def calculate_adaptive_interval(
    base_interval: int,
    memory_pressure: float,
    failure_streak: int = 0,
) -> int:
    """Dynamically adjust the compaction interval based on system state.

    Higher pressure or failure streaks → shorter interval (more aggressive).
    Lower pressure → longer interval (less overhead).

    Args:
        base_interval: The default compaction interval.
        memory_pressure: Current pressure (0.0-1.0).
        failure_streak: Number of consecutive failures.

    Returns:
        Adjusted interval in waves.
    """
    interval = base_interval

    # High pressure → halve the interval
    if memory_pressure > 0.8:
        interval = max(1, interval // 2)
    # Moderate pressure → reduce by 1
    elif memory_pressure > 0.6:
        interval = max(1, interval - 1)

    # Failure streak → more aggressive compaction
    if failure_streak >= 3:
        interval = max(1, interval - 1)

    return interval
