"""Tests for agents/profiles.py."""

from conveyor.agents.profiles import build_swarm_profiles


class TestBuildSwarmProfiles:
    def test_returns_12_profiles(self):
        profiles = build_swarm_profiles()
        assert len(profiles) == 12

    def test_has_expected_roles(self):
        profiles = build_swarm_profiles()
        expected = {
            "test", "coder", "judge", "chat",
            "context_guard", "pattern_finder", "compression",
            "novelty", "stability_guard",
            "seed_prep", "directive_prep", "stability_prep",
        }
        assert set(profiles.keys()) == expected

    def test_all_profiles_have_required_fields(self):
        profiles = build_swarm_profiles()
        for role, prof in profiles.items():
            assert isinstance(prof.name, str), f"{role} name must be str"
            assert isinstance(prof.model, str), f"{role} model must be str"
            assert isinstance(prof.system_prompt, str), f"{role} system_prompt must be str"
            assert isinstance(prof.fallback_models, list), f"{role} fallback_models must be list"
            assert prof.name, f"{role} name must not be empty"
            assert prof.model, f"{role} model must not be empty"
            assert prof.system_prompt, f"{role} system_prompt must not be empty"

    def test_test_agent_uses_groq_model(self):
        profiles = build_swarm_profiles()
        assert "instant" in profiles["test"].model  # Groq model ID pattern

    def test_all_non_test_have_fallback(self):
        profiles = build_swarm_profiles()
        for role, prof in profiles.items():
            if role != "test":
                assert len(prof.fallback_models) > 0, f"{role} must have fallback models"

    def test_prep_agents_have_json_system_prompt(self):
        profiles = build_swarm_profiles()
        for role in ("seed_prep", "directive_prep", "stability_prep"):
            prompt = profiles[role].system_prompt
            assert "JSON" in prompt, f"{role} prompt must mention JSON output"
            assert "title" in prompt.lower(), f"{role} prompt must mention title field"
            assert "do not write code" in prompt.lower(), f"{role} prompt must say no code"
