"""Tests for status_formatter.py."""

from conveyor.ui.status_formatter import format_status
from conveyor.core.types import SwarmStatus


class TestFormatStatus:
    def test_formats_minimal_status(self):
        status = SwarmStatus().flatten()
        output = format_status(status)
        assert "Swarm status" in output
        assert "[Chat Lane]" in output
        assert "[Swarm Health]" in output
        assert "[Memory]" in output
        assert "[Model Routing]" in output

    def test_formats_running_state(self):
        status = SwarmStatus(
            state="running",
            phase="execution",
            wave_index=5,
            wave_name="wave-test",
            spawn_count=23,
        ).flatten()
        output = format_status(status)
        assert "State: running" in output
        assert "Phase: execution" in output
        assert "Spawns: 23" in output

    def test_formats_warnings(self):
        status = SwarmStatus(
            ui_warnings=["test warning"],
            ui_suggestions=["check health"],
        ).flatten()
        output = format_status(status)
        assert "test warning" in output
        assert "check health" in output

    def test_formats_rehearsal_section(self):
        status = SwarmStatus(
            rehearsal_state="COMPLETE",
            rehearsal_profile="mixed",
        ).flatten()
        output = format_status(status)
        assert "[Rehearsal]" in output
        assert "State: COMPLETE" in output

    def test_preflight_section(self):
        status = SwarmStatus(
            prep_bundle_id="abc123",
            prep_status="READY",
            prep_ready_to_launch=True,
        ).flatten()
        output = format_status(status)
        assert "[Preflight]" in output
        assert "Bundle: abc123" in output
        assert "Ready: True" in output


class TestFormatHelpers:
    def test_truncation(self):
        from conveyor.ui.status_formatter import _truncate
        short = _truncate("hello", 10)
        assert short == "hello"
        long_msg = _truncate("a" * 200, 50)
        assert len(long_msg) == 50
        assert long_msg.endswith("...")

    def test_enabled_str(self):
        from conveyor.ui.status_formatter import _enabled_str
        assert _enabled_str(True) == "enabled"
        assert _enabled_str(False) == "disabled"

    def test_fmt(self):
        from conveyor.ui.status_formatter import _fmt
        assert _fmt(True) == "True"
        assert _fmt(1.5) == "1.50"
        assert _fmt(1) == "1"  # int stays int
        assert _fmt("hello") == "hello"
