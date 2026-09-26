from __future__ import annotations

import socket
import threading
import time
from typing import Any, Callable

from control.esp32_serial_controller import Esp32SerialController
from core.exceptions import DroneError


class Esp32UdpController(Esp32SerialController):
    """Wireless high-level transport to the ESP32 SoftAP/UDP endpoint."""

    def __init__(self, host: str = "192.168.4.1", port: int = 8889,
                 dry_run: bool = False, connect_timeout_sec: float = 15.0,
                 telemetry_timeout_sec: float = 0.6,
                 socket_factory: Callable[..., Any] = socket.socket) -> None:
        super().__init__(
            port="UDP", dry_run=dry_run, connect_timeout_sec=connect_timeout_sec,
            telemetry_timeout_sec=telemetry_timeout_sec,
        )
        self.host, self.udp_port = host, port
        self.socket_factory = socket_factory
        self._socket: Any | None = None

    def connect(self) -> None:
        try:
            self._socket = self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.settimeout(0.1)
            self._socket.bind(("", 0))
        except Exception as exc:
            raise DroneError(f"cannot create ESP32 UDP transport: {exc}") from exc
        self.state.connected = True
        self._stop_reader.clear()
        self._reader = threading.Thread(target=self._udp_reader_loop, name="esp32-udp-reader", daemon=True)
        self._reader.start()
        hello_deadline = time.monotonic() + min(3.0, self.connect_timeout_sec)
        while not self._hello.is_set() and time.monotonic() < hello_deadline:
            self._write_line("hello")
            self._hello.wait(0.2)
        if not self._hello.is_set():
            self.disconnect()
            raise DroneError(
                "ESP32 UDP HELLO timeout; connect Windows Wi-Fi to the configured TT-HighLevel AP"
            )
        if not self._wait_for(lambda: self.telemetry.mission_state == "READY", self.connect_timeout_sec):
            state = self.telemetry.mission_state
            self.disconnect()
            raise DroneError(f"ESP32 Mission Executive did not become READY (state={state})")
        self.state.mission_pads_enabled = True

    def disconnect(self) -> None:
        self._stop_reader.set()
        udp_socket, self._socket = self._socket, None
        if udp_socket is not None:
            try:
                udp_socket.close()
            except Exception:
                pass
        if self._reader and self._reader.is_alive() and self._reader is not threading.current_thread():
            self._reader.join(timeout=1)
        self.state.connected = False

    def _udp_reader_loop(self) -> None:
        while not self._stop_reader.is_set() and self._socket is not None:
            try:
                payload, _ = self._socket.recvfrom(512)
            except socket.timeout:
                continue
            except Exception:
                if not self._stop_reader.is_set():
                    self.state.connected = False
                return
            self._process_line(payload.decode("utf-8", errors="replace").strip())

    def _write_line(self, line: str) -> None:
        self._require_connected()
        with self._write_lock:
            try:
                self._socket.sendto(line.encode("ascii"), (self.host, self.udp_port))
            except Exception as exc:
                raise DroneError(f"ESP32 UDP send failed: {exc}") from exc

    def _require_connected(self) -> None:
        if not self.state.connected or self._socket is None:
            raise DroneError("ESP32 UDP controller is not connected")
