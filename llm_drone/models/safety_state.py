from dataclasses import dataclass, field
import time


@dataclass
class Esp32Telemetry:
    mission_state: str = "UNKNOWN"
    airborne: bool = False
    command_fresh: bool = False
    safety: str = "UNKNOWN"
    safety_override: bool = False
    directions: dict[str, tuple[str, int]] = field(default_factory=dict)
    pad_id: int = -1
    x: int = 0
    y: int = 0
    z: int = 0
    battery: int = -1
    height: int = -1
    tt_age_ms: int = 2**32 - 1
    received_at: float = field(default_factory=time.monotonic)
