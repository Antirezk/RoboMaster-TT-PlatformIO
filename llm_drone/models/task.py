from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskType(str, Enum):
    TAKEOFF = "takeoff"
    LAND = "land"
    STOP = "stop"
    BATTERY = "battery"
    STATUS = "status"
    PAD_TELEMETRY = "pad_telemetry"
    SEARCH_MISSION_PAD = "search_mission_pad"
    GOTO_MISSION_PAD = "goto_mission_pad"


@dataclass(frozen=True)
class Task:
    type: TaskType
    pad_id: int | None = None
    final_action: str | None = None
    raw_command: str = ""

    def __post_init__(self) -> None:
        if self.pad_id is not None and not 1 <= self.pad_id <= 8:
            raise ValueError("pad_id must be between 1 and 8")
        if self.type in {TaskType.SEARCH_MISSION_PAD, TaskType.GOTO_MISSION_PAD}:
            if self.pad_id is None:
                raise ValueError(f"{self.type.value} requires pad_id")
        elif self.pad_id is not None:
            raise ValueError(f"{self.type.value} does not accept pad_id")
        if self.final_action not in {None, "land"}:
            raise ValueError("final_action must be None or 'land'")
        if self.final_action is not None and self.type != TaskType.GOTO_MISSION_PAD:
            raise ValueError("final_action is only valid for goto_mission_pad")
