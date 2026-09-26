class DroneError(RuntimeError):
    """Base error for the autonomous drone application."""


class SafetyError(DroneError):
    """Raised when an operation is rejected by a safety guard."""


class TaskParseError(DroneError):
    """Raised when neither the mechanical parser nor optional LLM can parse a command."""


class MissionError(DroneError):
    """Raised when a mission cannot be completed safely."""
