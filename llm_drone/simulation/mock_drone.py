from __future__ import annotations

from control.rc_command import RCCommand
from models.drone_state import DroneState


class MockDrone:
    """Deterministic, no-hardware TT simulator for workflow and unit tests."""

    def __init__(self, battery: int = 80, target_pad_id: int = 1) -> None:
        self.state = DroneState()
        self.battery = battery
        self.target_pad_id = target_pad_id
        self.commands: list[RCCommand] = []
        self.events: list[str] = []
        self._samples = 0
        self._x, self._y, self._z = 30.0, -20.0, 80.0
        self._sample_pad_id = -1

    def connect(self) -> None:
        self.state.connected = True
        self.state.battery = self.battery
        self.events.append("connect")

    def disconnect(self) -> None:
        self.state.connected = False
        self.events.append("disconnect")

    def takeoff(self) -> None:
        self.state.flying = True
        self.events.append("takeoff")

    def land(self) -> None:
        self.state.flying = False
        self.events.append("land")

    def stop(self) -> None:
        self.send_rc(RCCommand.zero())
        self.events.append("stop")

    def send_rc(self, command: RCCommand) -> None:
        self.commands.append(command)
        if self._sample_pad_id == self.target_pad_id:
            self._x += command.left_right * 0.12
            self._y += command.forward_backward * 0.12
            self._z = max(15.0, self._z + command.up_down * 0.16)

    def get_battery(self) -> int:
        self.state.battery = self.battery
        return self.battery

    def get_height(self) -> int:
        return int(self._z if self.state.flying else 0)

    def enable_mission_pads(self) -> None:
        self.state.mission_pads_enabled = True
        self.events.append("enable_mission_pads")

    def disable_mission_pads(self) -> None:
        self.state.mission_pads_enabled = False

    def set_mission_pad_direction(self, direction: int) -> None:
        self.events.append(f"pad_direction:{direction}")

    def get_mission_pad_id(self) -> int:
        self._samples += 1
        if self._samples <= 3:
            self._sample_pad_id = -1
        elif self._samples <= 5:
            self._sample_pad_id = 3
        else:
            self._sample_pad_id = self.target_pad_id
        return self._sample_pad_id

    def get_mission_pad_distance_x(self) -> int:
        return int(round(self._x))

    def get_mission_pad_distance_y(self) -> int:
        return int(round(self._y))

    def get_mission_pad_distance_z(self) -> int:
        return int(round(self._z))

    def get_safety_status(self) -> str:
        return "NORMAL"
