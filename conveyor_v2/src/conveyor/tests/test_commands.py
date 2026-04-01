"""Tests for command_handlers.py."""

import pytest
from conveyor.ui.command_handlers import (
    CommandDef,
    CommandResult,
    dispatch_command,
    get_command,
    list_commands,
    register_command,
    _handle_filesystem_request,
    _build_registry,
)


class TestCommandRegistry:
    def setup_method(self):
        """Reset registry before each test."""
        _build_registry()

    def test_dispatch_normal_message(self):
        result = dispatch_command("hello world")
        assert result.handled is False
        assert result.message == ""
        assert result.config_overrides == {}

    def test_dispatch_status(self):
        result = dispatch_command("/status")
        assert result.handled is True
        assert "showing swarm status" in result.message.lower() or "status" in result.message.lower()

    def test_dispatch_testcmd(self):
        result = dispatch_command("/testcmd pytest -q")
        assert result.handled is True
        assert "test command updated" in result.message.lower()

    def test_dispatch_testcmd_empty(self):
        result = dispatch_command("/testcmd")
        assert result.handled is True
        assert "usage" in result.message.lower()

    def test_dispatch_memory_fast(self):
        result = dispatch_command("/memory fast")
        assert result.handled is True
        assert "fast" in result.message.lower()
        assert result.config_overrides.get("memory_distillation_enabled") is True
        assert result.config_overrides.get("compaction_interval_waves") == 2

    def test_dispatch_memory_off(self):
        result = dispatch_command("/memory off")
        assert result.handled is True
        assert result.config_overrides.get("memory_distillation_enabled") is False

    def test_dispatch_memory_invalid(self):
        result = dispatch_command("/memory turbo")
        assert result.handled is True
        assert "usage" in result.message.lower()

    def test_dispatch_adaptive_on(self):
        result = dispatch_command("/adaptive on")
        assert result.handled is True
        assert result.config_overrides.get("adaptive_compaction_enabled") is True

    def test_dispatch_adaptive_off(self):
        result = dispatch_command("/adaptive off")
        assert result.handled is True
        assert result.config_overrides.get("adaptive_compaction_enabled") is False

    def test_dispatch_unknown_command_rejected(self):
        result = dispatch_command("/launch_swarm")
        assert result.handled is True
        assert "console" in result.message.lower()

    def test_filesystem_request_detected(self):
        result = dispatch_command("create a folder called test-project")
        assert result.handled is True
        assert "file" in result.message.lower() or "folder" in result.message.lower()

    def test_non_filesystem_message_not_handled(self):
        """Normal message that mentions a file should not be rejected."""
        result = dispatch_command("can you review the code in main.py?")
        # This doesn't match the filesystem pattern (no "called X" or "named X")
        assert result.handled is False


class TestFilesystemRequest:
    def test_detects_folder_request(self):
        result = _handle_filesystem_request("create a folder named my-app", {})
        assert result
        assert result["folder_name"] == "my-app"

    def test_detects_called_pattern(self):
        result = _handle_filesystem_request("make a directory called lib", {})
        assert result
        assert result["folder_name"] == "lib"

    def test_no_match_without_pattern(self):
        result = _handle_filesystem_request("I need a folder but didn't name it", {})
        assert not result

    def test_no_match_without_keywords(self):
        result = _handle_filesystem_request("create something named hello", {})
        assert not result

    def test_javascript_detection(self):
        result = _handle_filesystem_request(
            "create a folder called js-app with a javascript file named hello world", {}
        )
        assert result["files"][0]["name"] == "hello.js"

    def test_typescript_detection(self):
        result = _handle_filesystem_request(
            "create a folder called ts-app with a typescript file", {}
        )
        assert result["files"][0]["name"] == "hello.ts"

    def test_default_txt(self):
        result = _handle_filesystem_request("create a folder called test", {})
        assert result["files"][0]["name"] == "hello.txt"


class TestCommandLookups:
    def setup_method(self):
        _build_registry()

    def test_get_existing_command(self):
        cmd = get_command("status")
        assert cmd is not None
        assert cmd.name == "status"

    def test_get_missing_command(self):
        cmd = get_command("nonexistent")
        assert cmd is None

    def test_list_commands(self):
        cmds = list_commands()
        names = {c.name for c in cmds}
        assert "status" in names
        assert "memory" in names
        assert "testcmd" in names
        assert "adaptive" in names
        assert "recap" in names
        assert "health" in names
