"""Loopback UDP peer for debugging, not a flight/vision simulator."""
from dataclasses import replace
import socket
import threading
import time


class Simulator:
    def __init__(self, config, pad_id=1):
        if type(pad_id) is not int or not 1 <= pad_id <= 8:
            raise ValueError("simulated Pad ID must be 1..8")
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(0.02)
        # Reserve client ports while allocating, then release for the channels.
        reservations = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(2)]
        try:
            for sock in reservations:
                sock.bind(("127.0.0.1", 0))
            self.config = replace(config, drone_ip="127.0.0.1", bind_host="127.0.0.1",
                                  command_port=self.socket.getsockname()[1],
                                  local_command_port=reservations[0].getsockname()[1],
                                  state_port=reservations[1].getsockname()[1])
        finally:
            for sock in reservations:
                sock.close()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="tt-simulator", daemon=True)
        self.history = []
        self.response_overrides = {}
        self.enabled = False
        self.pad_enabled = False
        self.flying = False
        self.mid = pad_id
        self.x, self.y, self.z = 25, -15, 60
        self.emit_state = True

    def start(self):
        self.thread.start()

    def _run(self):
        next_state = 0.0
        while not self.stop_event.is_set():
            try:
                packet, peer = self.socket.recvfrom(4096)
                command = packet.decode("ascii")
                self.history.append(command)
                response = "ok"
                if command == "command":
                    self.enabled = True
                elif not self.enabled:
                    response = "error"
                elif command == "battery?":
                    response = "85"
                elif command == "mon":
                    self.pad_enabled = True
                elif command == "takeoff":
                    self.flying = True
                elif command == "land":
                    self.flying = False
                elif command.startswith("go "):
                    if command.split()[-1] != f"m{self.mid}" or not self.flying:
                        response = "error pad"
                    else:
                        self.x, self.y, self.z = map(int, command.split()[1:4])
                response = self.response_overrides.get(command, response)
                if response is not None:
                    self.socket.sendto(response.encode("ascii"), peer)
            except socket.timeout:
                pass
            except OSError:
                return
            if self.enabled and self.emit_state and time.monotonic() >= next_state:
                mid = self.mid if self.pad_enabled else -2
                state = (f"mid:{mid};x:{self.x};y:{self.y};z:{self.z};mpry:0,0,0;"
                         f"pitch:0;roll:0;yaw:0;vgx:0;vgy:0;vgz:0;bat:85;"
                         f"h:{self.z if self.flying else 0};tof:{self.z if self.flying else 10};")
                self.socket.sendto(state.encode("ascii"), ("127.0.0.1", self.config.state_port))
                next_state = time.monotonic() + 0.05

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=1)
        self.socket.close()
