import pytest

from control.drone_controller import DroneController
from control.rc_command import RCCommand
from core.exceptions import SafetyError


class FakeTello:
    def __init__(self):
        self.rc = []

    def connect(self): pass
    def get_battery(self): return 80
    def send_rc_control(self, *values): self.rc.append(values)


def test_dry_run_blocks_takeoff_and_nonzero_rc():
    tello = FakeTello()
    drone = DroneController(dry_run=True, tello=tello)
    drone.connect()
    with pytest.raises(SafetyError, match="takeoff"):
        drone.takeoff()
    with pytest.raises(SafetyError, match="non-zero RC"):
        drone.send_rc(RCCommand(yaw=1))


def test_dry_run_allows_zero_rc():
    tello = FakeTello()
    drone = DroneController(dry_run=True, tello=tello)
    drone.connect()
    drone.send_rc(RCCommand.zero())
    assert tello.rc == [(0, 0, 0, 0)]
