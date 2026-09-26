from __future__ import annotations

from enum import Enum
from typing import Any


class MissionState(str, Enum):
    IDLE = "IDLE"
    PRECHECK = "PRECHECK"
    TAKEOFF = "TAKEOFF"
    SEARCH = "SEARCH"
    ALIGN = "ALIGN"
    DESCEND = "DESCEND"
    LAND = "LAND"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EMERGENCY = "EMERGENCY"


class StateMachine:
    TERMINAL_STATES = {MissionState.COMPLETED, MissionState.FAILED}

    def __init__(self, logger: Any) -> None:
        self.logger = logger
        self.state = MissionState.IDLE
        self.history: list[tuple[MissionState, MissionState]] = []

    def transition(self, new_state: MissionState) -> None:
        if new_state == self.state:
            return
        old_state = self.state
        self.state = new_state
        self.history.append((old_state, new_state))
        self.logger.info("STATE %s -> %s", old_state.value, new_state.value)

    @property
    def finished(self) -> bool:
        return self.state in self.TERMINAL_STATES
