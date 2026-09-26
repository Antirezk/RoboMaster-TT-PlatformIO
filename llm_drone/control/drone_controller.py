from __future__ import annotations

from typing import Any

from control.rc_command import RCCommand
from core.exceptions import DroneError, SafetyError
from models.drone_state import DroneState


class DroneController:
    """The only production module allowed to call DJITelloPy."""

    def __init__(self, dry_run: bool = False, tello: Any | None = None) -> None:
        self.dry_run = dry_run
        self._tello = tello
        self.state = DroneState(dry_run=dry_run)

    def connect(self) -> None:
        if self._tello is None:
            try:
                from djitellopy import Tello
            except ImportError as exc:
                raise DroneError("DJITelloPy is required for real drone mode") from exc
            self._tello = Tello()
        self._tello.connect()
        self.state.connected = True
        self.state.battery = self.get_battery()

    def disconnect(self) -> None:
        if self._tello is not None:
            try:
                self.stop()
            finally:
                self._tello.end()
        self.state.connected = False

    def takeoff(self) -> None:
        if self.dry_run:
            raise SafetyError("dry-run mode blocks takeoff")
        self._require_connected()
        self._tello.takeoff()
        self.state.flying = True

    def land(self) -> None:
        if self.dry_run:
            raise SafetyError("dry-run mode blocks landing commands")
        self._require_connected()
        self._tello.land()
        self.state.flying = False

    def stop(self) -> None:
        if self._tello is not None and self.state.connected:
            self._tello.send_rc_control(0, 0, 0, 0)

    def send_rc(self, command: RCCommand) -> None:
        self._require_connected()
        if self.dry_run and not command.is_zero:
            raise SafetyError("dry-run mode blocks non-zero RC commands")
        self._tello.send_rc_control(
            command.left_right, command.forward_backward, command.up_down, command.yaw
        )

    def get_battery(self) -> int:
        self._require_connected()
        value = int(self._tello.get_battery())
        self.state.battery = value
        return value

    def get_height(self) -> int:
        self._require_connected()
        value = int(self._tello.get_height())
        self.state.height_cm = value
        return value

    def enable_mission_pads(self) -> None:
        self._require_connected()
        self._tello.enable_mission_pads()
        self.state.mission_pads_enabled = True

    def disable_mission_pads(self) -> None:
        if self._tello is not None and self.state.connected:
            self._tello.disable_mission_pads()
        self.state.mission_pads_enabled = False

    def set_mission_pad_direction(self, direction: int) -> None:
        self._require_connected()
        self._tello.set_mission_pad_detection_direction(direction)

    def get_mission_pad_id(self) -> int:
        self._require_connected()
        return int(self._tello.get_mission_pad_id())

    def get_mission_pad_distance_x(self) -> int:
        self._require_connected()
        return int(self._tello.get_mission_pad_distance_x())

    def get_mission_pad_distance_y(self) -> int:
        self._require_connected()
        return int(self._tello.get_mission_pad_distance_y())

    def get_mission_pad_distance_z(self) -> int:
        self._require_connected()
        return int(self._tello.get_mission_pad_distance_z())

    def get_safety_status(self) -> str:
        return "NORMAL"

    def _require_connected(self) -> None:
        if not self.state.connected or self._tello is None:
            raise DroneError("drone is not connected")
