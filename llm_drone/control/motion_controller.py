from __future__ import annotations

from typing import Any

from control.rc_command import RCCommand


class MotionController:
    """Converts mission velocity requests into bounded TT RC commands."""

    def __init__(self, drone: Any, max_xy_speed: int = 25, max_vertical_speed: int = 20, telemetry: Any = None) -> None:
        self.drone = drone
        self.max_xy_speed = abs(max_xy_speed)
        self.max_vertical_speed = abs(max_vertical_speed)
        self.telemetry = telemetry
        self.last_command = RCCommand.zero()

    @staticmethod
    def _limit(value: float, limit: int) -> float:
        return max(-limit, min(limit, value))

    def send_velocity(self, left_right: float = 0, forward_backward: float = 0,
                      up_down: float = 0, yaw: float = 0) -> RCCommand:
        command = RCCommand(
            self._limit(left_right, self.max_xy_speed),
            self._limit(forward_backward, self.max_xy_speed),
            self._limit(up_down, self.max_vertical_speed),
            yaw,
        )
        self.drone.send_rc(command)
        self.last_command = command
        if self.telemetry:
            self.telemetry.rc(command)
        return command

    def hover(self) -> RCCommand:
        return self.send_velocity()

    def stop(self) -> None:
        self.hover()
