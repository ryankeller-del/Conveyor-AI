"""Tests for compaction.py."""

from conveyor.core.compaction import (
    CompactionConfig,
    CompactionResult,
    run_compaction,
    should_compact_on_interval,
    calculate_adaptive_interval,
)


class TestIntervalCompaction:
    def test_interval_multiple(self):
        assert should_compact_on_interval(3, 3) is True
        assert should_compact_on_interval(6, 3) is True
        assert should_compact_on_interval(9, 3) is True

    def test_interval_non_multiple(self):
        assert should_compact_on_interval(1, 3) is False
        assert should_compact_on_interval(2, 3) is False
        assert should_compact_on_interval(4, 3) is False

    def test_zero_interval(self):
        assert should_compact_on_interval(1, 0) is False

    def test_one_interval(self):
        assert should_compact_on_interval(1, 1) is True
        assert should_compact_on_interval(2, 1) is True


class TestRunCompaction:
    def test_no_compaction_needed(self):
        config = CompactionConfig()
        result = run_compaction(
            config=config,
            memory_pressure=0.3,
            rule_count=4,
            breadcrumb_count=3,
            packet_count=10,
        )
        assert result.reason == "no compaction needed"
        assert result.rules_kept == 4
        assert result.breadcrumbs_kept == 3
        assert result.was_adaptive is False

    def test_rule_limit_exceeded(self):
        config = CompactionConfig(memory_rule_limit=4)
        result = run_compaction(
            config=config,
            memory_pressure=0.3,
            rule_count=8,
            breadcrumb_count=2,
            packet_count=10,
        )
        assert "rule limit" in result.reason
        assert result.rules_kept == 4
        assert result.packets_compacted == 4

    def test_breadcrumb_limit_exceeded(self):
        config = CompactionConfig(memory_breadcrumb_limit=3)
        result = run_compaction(
            config=config,
            memory_pressure=0.3,
            rule_count=2,
            breadcrumb_count=7,
            packet_count=10,
        )
        assert "breadcrumb" in result.reason
        assert result.breadcrumbs_kept == 3

    def test_distillation_disabled(self):
        config = CompactionConfig(memory_distillation_enabled=False)
        result = run_compaction(
            config=config,
            memory_pressure=0.9,
            rule_count=20,
            breadcrumb_count=20,
            packet_count=100,
        )
        assert result.reason == "distillation disabled"
        assert result.rules_kept == 0

    def test_adaptive_high_pressure(self):
        config = CompactionConfig(
            adaptive_compaction_enabled=True,
            memory_rule_limit=20,
            memory_breadcrumb_limit=20,
            compaction_interval_waves=3,
        )
        result = run_compaction(
            config=config,
            memory_pressure=0.85,
            rule_count=5,
            breadcrumb_count=3,
            packet_count=10,
        )
        # High pressure triggers adaptive adjustment
        assert result.was_adaptive is True
        assert result.wave_interval < 3


class TestAdaptiveInterval:
    def test_no_adjustment(self):
        interval = calculate_adaptive_interval(3, pressure=0.3, failure_streak=0)
        assert interval == 3

    def test_high_pressure_halves(self):
        interval = calculate_adaptive_interval(4, pressure=0.9, failure_streak=0)
        assert interval == 2  # 4 // 2 = 2

    def test_moderate_pressure_reduces(self):
        interval = calculate_adaptive_interval(3, pressure=0.7, failure_streak=0)
        assert interval == 2  # 3 - 1 = 2

    def test_failure_streak_reduces(self):
        interval = calculate_adaptive_interval(3, pressure=0.3, failure_streak=5)
        assert interval == 2  # 3 - 1 = 2

    def test_minimum_is_one(self):
        interval = calculate_adaptive_interval(1, pressure=0.9, failure_streak=5)
        assert interval == 1
