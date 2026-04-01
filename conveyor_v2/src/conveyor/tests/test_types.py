"""Tests for types.py — pure data validation."""

import pytest
from conveyor.core.types import (
    RunConfig,
    TaskGoal,
    SwarmStatus,
    RehearsalResults,
    SwarmState,
    Phase,
    GuardMode,
    MemoryProfile,
    BotProfile,
)


class TestRunConfig:
    def test_defaults(self):
        cfg = RunConfig()
        assert cfg.test_command == "python -m pytest {tests_path} -q"
        assert cfg.chat_history_limit == 8
        assert cfg.memory_distillation_enabled is True
        assert cfg.compaction_interval_waves == 3
        assert cfg.memory_rule_limit == 6
        assert cfg.memory_breadcrumb_limit == 5
        assert cfg.adaptive_compaction_enabled is True

    def test_apply_overrides_known_fields(self):
        cfg = RunConfig()
        overridden = cfg.apply_overrides({"chat_history_limit": 20})
        assert overridden.chat_history_limit == 20
        # Original unchanged (apply_overrides returns new instance)
        assert cfg.chat_history_limit == 8

    def test_apply_overrides_ignores_unknown_fields(self):
        cfg = RunConfig()
        overridden = cfg.apply_overrides({"nonexistent_field": 999, "chat_history_limit": 12})
        assert overridden.chat_history_limit == 12
        # Only the known field was applied

    def test_apply_overrides_empty(self):
        cfg = RunConfig()
        overridden = cfg.apply_overrides({})
        assert overridden == cfg

    def test_apply_overrides_multiple_fields(self):
        cfg = RunConfig()
        overridden = cfg.apply_overrides({
            "chat_history_limit": 5,
            "memory_distillation_enabled": False,
        })
        assert overridden.chat_history_limit == 5
        assert overridden.memory_distillation_enabled is False


class TestTaskGoal:
    def test_minimal(self):
        goal = TaskGoal(prompt="test")
        assert goal.prompt == "test"
        assert goal.target_files == []
        assert goal.language == "general"

    def test_full(self):
        goal = TaskGoal(
            prompt="fix bug",
            target_files=["app.py", "test_app.py"],
            language="python",
        )
        assert len(goal.target_files) == 2
        assert goal.language == "python"


class TestSwarmStatus:
    def test_defaults(self):
        status = SwarmStatus()
        result = status.flatten()
        assert result["state"] == "idle"
        assert result["phase"] == "preflight"
        assert result["guard_mode"] == "NORMAL"
        assert result["chat_mode"] == "chat"
        assert isinstance(result["active_topology"], list)
        assert isinstance(result["ui_suggestions"], list)
        assert isinstance(result["prep_proposals"], list)

    def test_flatten_preserves_all_keys(self):
        status = SwarmStatus(
            state="running",
            wave_index=42,
            wave_name="wave-alpha",
            local_memory_pressure=0.75,
        )
        flat = status.flatten()
        assert flat["state"] == "running"
        assert flat["wave_index"] == 42
        assert flat["wave_name"] == "wave-alpha"
        assert flat["local_memory_pressure"] == 0.75

    def test_flatten_key_count(self):
        """SwarmStatus has exactly the fields the legacy status() dict had."""
        status = SwarmStatus()
        flat = status.flatten()
        # The legacy system returned ~100 keys. Verify we have a comparable set.
        assert len(flat) >= 80  # Minimum sanity check


class TestRehearsalResults:
    def test_defaults(self):
        result = RehearsalResults(
            rehearsal_id="r1",
            profile="mixed",
            accepted=True,
            live_score=0.85,
            rehearsal_score=0.90,
        )
        assert result.rehearsal_id == "r1"
        assert result.accepted is True
        assert result.stage_manifest == {}
        assert result.report_path == ""


class TestBotProfile:
    def test_minimal(self):
        profile = BotProfile(name="test", model="llama3.2")
        assert profile.fallback_models == []
        assert profile.system_prompt == ""
        assert profile.fallback_client_models == []

    def test_full(self):
        profile = BotProfile(
            name="coder",
            model="codellama",
            fallback_models=["mistral7b"],
            system_prompt="You are a code expert.",
            fallback_client_models=["openrouter/free"],
        )
        assert len(profile.fallback_models) == 1
        assert "openrouter/free" in profile.fallback_client_models
