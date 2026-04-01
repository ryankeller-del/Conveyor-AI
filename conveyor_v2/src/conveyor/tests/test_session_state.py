"""Tests for session_state.py."""

from conveyor.models.session_state import SessionState


class TestSessionState:
    def test_defaults(self):
        state = SessionState()
        assert state.paused is False
        assert state.stopped is False
        assert state.active_test_command == "python -m pytest {tests_path} -q"
        assert state.run_config_overrides == {}
        assert len(state.session_id) == 8

    def test_unique_session_ids(self):
        s1 = SessionState()
        s2 = SessionState()
        assert s1.session_id != s2.session_id

    def test_config_overrides(self):
        state = SessionState()
        state.run_config_overrides["chat_history_limit"] = 20
        assert state.run_config_overrides["chat_history_limit"] == 20

    def test_pause_unpause(self):
        state = SessionState()
        state.paused = True
        assert state.paused is True
        state.paused = False
        assert state.paused is False

    def test_stop(self):
        state = SessionState()
        state.stopped = True
        assert state.stopped is True
