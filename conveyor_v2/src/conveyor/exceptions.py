"""Conveyor exceptions.

All custom exception types. No module dependencies.
"""


class ConveyorError(Exception):
    """Base exception for all Conveyor errors."""


class SwarmError(ConveyorError):
    """Error during swarm execution or orchestration."""


class PreflightError(SwarmError):
    """Error during preflight analysis or bundle generation."""


class RehearsalError(SwarmError):
    """Error during rehearsal simulation or manifest comparison."""


class ModelRoutingError(ConveyorError):
    """Error selecting or switching between model backends."""


class AgentError(ConveyorError):
    """Error during agent execution or fallback chain."""


class GuardInterventionError(SwarmError):
    """Raised when a stability guard overrides normal execution."""


class SessionError(ConveyorError):
    """Error with session state or persistence."""


class ConfigError(ConveyorError):
    """Error loading or validating configuration."""
