from dataclasses import dataclass
import time


@dataclass(frozen=True)
class MissionPadObservation:
    detected: bool
    pad_id: int
    x: float | None
    y: float | None
    z: float | None
    timestamp: float

    @classmethod
    def missing(cls, timestamp: float | None = None) -> "MissionPadObservation":
        return cls(False, -1, None, None, None, time.monotonic() if timestamp is None else timestamp)
