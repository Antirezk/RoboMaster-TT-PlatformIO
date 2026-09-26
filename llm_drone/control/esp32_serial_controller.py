from __future__ import annotations

import threading
import time
from typing import Any, Callable

from control.rc_command import RCCommand
from core.exceptions import DroneError, SafetyError
from models.drone_state import DroneState
from models.safety_state import Esp32Telemetry


class Esp32SerialController:
    """High-level transport; the ESP32 remains the sole writer to the TT UART."""

    PROTOCOL_VERSION = 1

    def __init__(self, port: str, baudrate: int = 115200, dry_run: bool = False,
                 connect_timeout_sec: float = 15.0, telemetry_timeout_sec: float = 0.6,
                 serial_factory: Callable[..., Any] | None = None) -> None:
        self.port, self.baudrate, self.dry_run = port, baudrate, dry_run
        self.connect_timeout_sec = connect_timeout_sec
        self.telemetry_timeout_sec = telemetry_timeout_sec
        self.serial_factory = serial_factory
        self.state = DroneState(dry_run=dry_run)
        self.telemetry = Esp32Telemetry()
        self._serial: Any | None = None
        self._reader: threading.Thread | None = None
        self._stop_reader = threading.Event()
        self._hello = threading.Event()
        self._condition = threading.Condition()
        self._acks: dict[str, str] = {}
        self._write_lock = threading.Lock()

    def connect(self) -> None:
        if self.serial_factory is None:
            try:
                import serial
            except ImportError as exc:
                raise DroneError("pyserial is required for ESP32 serial mode") from exc
            factory = serial.Serial
        else:
            factory = self.serial_factory
        try:
            self._serial = factory(self.port, self.baudrate, timeout=0.1)
        except Exception as exc:
            raise DroneError(f"cannot open ESP32 serial port {self.port}: {exc}") from exc
        self.state.connected = True
        self._stop_reader.clear()
        self._reader = threading.Thread(target=self._reader_loop, name="esp32-serial-reader", daemon=True)
        self._reader.start()
        self._write_line("hello")
        if not self._hello.wait(min(3.0, self.connect_timeout_sec)):
            self.disconnect()
            raise DroneError("ESP32 did not answer HL HELLO; flash the integrated firmware")
        if not self._wait_for(lambda: self.telemetry.mission_state == "READY", self.connect_timeout_sec):
            state = self.telemetry.mission_state
            self.disconnect()
            raise DroneError(f"ESP32 Mission Executive did not become READY (state={state})")
        self.state.mission_pads_enabled = True

    def disconnect(self) -> None:
        self._stop_reader.set()
        serial_port, self._serial = self._serial, None
        if serial_port is not None:
            try:
                serial_port.close()
            except Exception:
                pass
        if self._reader and self._reader.is_alive() and self._reader is not threading.current_thread():
            self._reader.join(timeout=1)
        self.state.connected = False

    def takeoff(self) -> None:
        if self.dry_run:
            raise SafetyError("dry-run mode blocks takeoff")
        self._require_ready()
        if self.state.safety_status != "NORMAL":
            raise SafetyError(f"ESP32 ToF safety is {self.state.safety_status}")
        self._action("takeoff")
        if not self._wait_for_with_zero_heartbeat(
            lambda: self.telemetry.airborne and self.telemetry.mission_state == "READY", 18.0
        ):
            raise DroneError("ESP32 takeoff did not reach airborne READY state")

    def land(self) -> None:
        if self.dry_run:
            raise SafetyError("dry-run mode blocks landing commands")
        self._action("land")
        if not self._wait_for(lambda: not self.telemetry.airborne, 18.0):
            raise DroneError("ESP32 landing confirmation timeout")

    def stop(self) -> None:
        if self.state.connected:
            self._write_line("stop")

    def send_rc(self, command: RCCommand) -> None:
        self._require_connected()
        if self.dry_run and not command.is_zero:
            raise SafetyError("dry-run mode blocks non-zero RC commands")
        self._write_line(
            f"rc {command.left_right} {command.forward_backward} {command.up_down} {command.yaw}"
        )

    def get_battery(self) -> int:
        self._require_fresh_telemetry()
        if self.telemetry.battery < 0:
            raise DroneError("TT battery is not present in ESP32 telemetry")
        return self.telemetry.battery

    def get_height(self) -> int:
        self._require_fresh_telemetry()
        if self.telemetry.height < 0:
            raise DroneError("TT height is not present in ESP32 telemetry")
        return self.telemetry.height

    def enable_mission_pads(self) -> None:
        self._require_ready()
        self.state.mission_pads_enabled = True

    def disable_mission_pads(self) -> None:
        # Mission Executive owns mon/moff; do not mutate TT configuration during shutdown.
        self.state.mission_pads_enabled = False

    def set_mission_pad_direction(self, direction: int) -> None:
        if direction != 0:
            raise SafetyError("ESP32 firmware currently owns downward-only mdirection=0")

    def get_mission_pad_id(self) -> int:
        if not self._pad_fresh():
            return -1
        return self.telemetry.pad_id

    def get_mission_pad_distance_x(self) -> int:
        return self.telemetry.x

    def get_mission_pad_distance_y(self) -> int:
        return self.telemetry.y

    def get_mission_pad_distance_z(self) -> int:
        return self.telemetry.z

    def get_safety_status(self) -> str:
        self._require_fresh_telemetry()
        return self.telemetry.safety

    def _action(self, name: str) -> None:
        with self._condition:
            self._acks.pop(name, None)
        self._write_line(name)
        if not self._wait_for(lambda: name in self._acks, 2.0):
            raise DroneError(f"ESP32 did not acknowledge {name}")
        if self._acks.get(name) != "ACCEPTED":
            raise SafetyError(f"ESP32 rejected {name}")

    def _reader_loop(self) -> None:
        while not self._stop_reader.is_set() and self._serial is not None:
            try:
                raw = self._serial.readline()
            except Exception:
                self.state.connected = False
                return
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            self._process_line(line)

    def _process_line(self, line: str) -> None:
        if line == f"HL HELLO {self.PROTOCOL_VERSION}":
            self._hello.set()
            return
        if line.startswith("HL ACK "):
            parts = line.split()
            if len(parts) == 4:
                with self._condition:
                    self._acks[parts[2]] = parts[3]
                    self._condition.notify_all()
            return
        if not line.startswith("HL TEL "):
            return
        values: dict[str, str] = {}
        for token in line[7:].split():
            if "=" in token:
                key, value = token.split("=", 1)
                values[key] = value
        try:
            directions = {}
            for key in ("f", "b", "l", "r"):
                mode, distance = values.get(key, "FAULT:0").split(":", 1)
                directions[key] = (mode, int(distance))
            telemetry = Esp32Telemetry(
                mission_state=values["mission"], airborne=values["airborne"] == "1",
                command_fresh=values["fresh"] == "1", safety=values["safety"],
                safety_override=values["override"] == "1", directions=directions,
                pad_id=int(values["mid"]), x=int(values["x"]), y=int(values["y"]),
                z=int(values["z"]), battery=int(values["bat"]), height=int(values["h"]),
                tt_age_ms=int(values["age"]), received_at=time.monotonic(),
            )
        except (KeyError, ValueError):
            return
        with self._condition:
            self.telemetry = telemetry
            self.state.flying = telemetry.airborne
            self.state.battery = telemetry.battery if telemetry.battery >= 0 else None
            self.state.height_cm = telemetry.height if telemetry.height >= 0 else None
            self.state.safety_status = telemetry.safety
            self.state.safety_intervening = telemetry.safety_override
            self.state.firmware_state = telemetry.mission_state
            self._condition.notify_all()

    def _write_line(self, line: str) -> None:
        self._require_connected()
        with self._write_lock:
            self._serial.write((line + "\n").encode("ascii"))
            self._serial.flush()

    def _wait_for(self, predicate: Callable[[], bool], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while not predicate():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(min(remaining, 0.1))
        return True

    def _wait_for_with_zero_heartbeat(self, predicate: Callable[[], bool], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                return False
            self._write_line("rc 0 0 0 0")
            self._wait_for(predicate, min(0.2, max(0.0, deadline - time.monotonic())))
        return True

    def _require_connected(self) -> None:
        if not self.state.connected or self._serial is None:
            raise DroneError("ESP32 serial controller is not connected")

    def _require_ready(self) -> None:
        self._require_fresh_telemetry()
        if self.telemetry.mission_state != "READY":
            raise SafetyError(f"ESP32 Mission Executive is {self.telemetry.mission_state}")

    def _require_fresh_telemetry(self) -> None:
        self._require_connected()
        if time.monotonic() - self.telemetry.received_at > self.telemetry_timeout_sec:
            raise DroneError("ESP32 telemetry is stale")

    def _pad_fresh(self) -> bool:
        try:
            self._require_fresh_telemetry()
        except DroneError:
            return False
        return self.telemetry.tt_age_ms <= int(self.telemetry_timeout_sec * 1000)
