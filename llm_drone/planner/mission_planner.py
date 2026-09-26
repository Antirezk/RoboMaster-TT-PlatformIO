from __future__ import annotations

from typing import Any

from models.task import Task, TaskType
from planner.goto_mission_pad import GotoMissionPadMission


class MissionPlanner:
    """Routes structured tasks; it performs no PID or SDK operations."""

    def __init__(self, drone: Any, tracker: Any, motion: Any, config: dict[str, Any], logger: Any) -> None:
        self.drone, self.tracker, self.motion = drone, tracker, motion
        self.config, self.logger = config, logger

    def create_mission(self, task: Task) -> GotoMissionPadMission:
        if task.type in {TaskType.GOTO_MISSION_PAD, TaskType.SEARCH_MISSION_PAD}:
            return GotoMissionPadMission(
                task, self.drone, self.tracker, self.motion, self.config, self.logger
            )
        raise ValueError(f"task {task.type.value} is not a planned mission")
