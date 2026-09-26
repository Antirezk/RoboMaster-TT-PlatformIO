from __future__ import annotations

from typing import Any

from models.mission_pad import MissionPadObservation


class TelemetryManager:
    def __init__(self, logger: Any) -> None:
        self.logger = logger

    def command(self, text: str) -> None:
        self.logger.info("COMMAND: %s", text)

    def task(self, task: Any, source: str = "unknown") -> None:
        self.logger.info(
            "TASK: source=%s type=%s pad=%s final_action=%s",
            source, task.type.value, task.pad_id, task.final_action,
        )

    def pad(self, observation: MissionPadObservation, target_id: int | None = None) -> None:
        if not observation.detected:
            self.logger.debug("PAD mid=-1")
        elif target_id is not None and observation.pad_id != target_id:
            self.logger.info("PAD mid=%s ignored", observation.pad_id)
        else:
            self.logger.debug(
                "PAD mid=%s x=%s y=%s z=%s",
                observation.pad_id, observation.x, observation.y, observation.z,
            )

    def rc(self, command: Any) -> None:
        self.logger.debug(
            "RC lr=%s fb=%s ud=%s yaw=%s",
            command.left_right, command.forward_backward, command.up_down, command.yaw,
        )
