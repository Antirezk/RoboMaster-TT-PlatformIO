import time

import pytest

from control.esp32_serial_controller import Esp32SerialController
from control.rc_command import RCCommand
from core.exceptions import SafetyError


class FakeSerial:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def flush(self): pass
    def close(self): pass


def controller_with_line(line):
    controller = Esp32SerialController("TEST", telemetry_timeout_sec=0.6)
    controller._serial = FakeSerial()
    controller.state.connected = True
    controller._process_line(line)
    return controller


VALID_LINE = (
    "HL TEL ms=100 mission=READY airborne=1 fresh=1 safety=BLOCKED override=1 "
    "f=BLOCKED:420 b=NORMAL:1200 l=NORMAL:1100 r=NORMAL:1000 "
    "mid=1 x=12 y=-4 z=76 bat=80 h=74 age=20"
)


def test_parses_esp32_machine_telemetry():
    controller = controller_with_line(VALID_LINE)
    assert controller.state.flying
    assert controller.state.safety_status == "BLOCKED"
    assert controller.state.safety_intervening
    assert controller.get_battery() == 80
    assert controller.get_height() == 74
    assert controller.get_mission_pad_id() == 1
    assert controller.get_mission_pad_distance_y() == -4
    assert controller.telemetry.directions["f"] == ("BLOCKED", 420)


def test_stale_tt_telemetry_hides_pad():
    controller = controller_with_line(VALID_LINE.replace("age=20", "age=700"))
    assert controller.get_mission_pad_id() == -1


def test_malformed_machine_line_is_ignored():
    controller = controller_with_line("HL TEL incomplete")
    assert controller.state.firmware_state == "UNKNOWN"


def test_serial_dry_run_blocks_nonzero_but_writes_zero():
    controller = controller_with_line(VALID_LINE)
    controller.dry_run = True
    with pytest.raises(SafetyError, match="non-zero RC"):
        controller.send_rc(RCCommand(yaw=1))
    controller.send_rc(RCCommand.zero())
    assert controller._serial.writes[-1] == b"rc 0 0 0 0\n"


def test_stale_host_telemetry_is_rejected():
    controller = controller_with_line(VALID_LINE)
    controller.telemetry.received_at = time.monotonic() - 1
    with pytest.raises(Exception, match="stale"):
        controller.get_battery()
