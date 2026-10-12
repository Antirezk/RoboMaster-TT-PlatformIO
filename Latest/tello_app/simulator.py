"""Loopback TT stand-in that speaks the SDK 3.0 subset this app uses.

Not a physics simulator: it only reproduces the protocol behaviour that matters
for the controller - replies after motions finish, stop interrupting a motion,
emergency without reply, motoron refused in the air, and the 15 s auto-land.
"""
from dataclasses import replace
import math
import socket
import threading
import time


class Simulator:
    def __init__(self, config, time_scale=0.5, autoland_sec=15.0):
        self.time_scale = time_scale
        self.autoland_sec = autoland_sec
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(0.02)
        reserve = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(2)]
        try:
            for s in reserve:
                s.bind(("127.0.0.1", 0))
            self.config = replace(config, drone_ip="127.0.0.1", bind_host="127.0.0.1",
                                  command_port=self.socket.getsockname()[1],
                                  local_command_port=reserve[0].getsockname()[1],
                                  state_port=reserve[1].getsockname()[1])
        finally:
            for s in reserve:
                s.close()
        self.history = []
        self.events = []
        self.overrides = {}          # command -> reply text, or None to stay silent
        self.enabled = self.flying = self.motors_on = False
        self.battery = 87
        self.speed = 10
        self.x = self.y = 0.0
        self.h = 0
        self.yaw = 0
        self.motion = None           # (finish_time, peer, command)
        self.last_command = time.monotonic()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="tt-simulator", daemon=True)

    def start(self):
        self.thread.start()
        return self

    # ------------------------------------------------------------------
    def _reply(self, peer, text):
        if text is not None:
            self.socket.sendto(text.encode("ascii"), peer)

    def _duration(self, command):
        name, _, arg = command.partition(" ")
        if name in {"takeoff", "land"}:
            return 3.0 * self.time_scale
        if name in {"forward", "back", "left", "right", "up", "down"}:
            return (1.0 + int(arg) / self.speed) * self.time_scale
        if name in {"cw", "ccw"}:
            return (0.5 + int(arg) / 90) * self.time_scale
        return 0.0

    def _delta(self, command, fraction):
        """Change of (x, y, h, yaw) after `fraction` of a motion command."""
        name, _, arg = command.partition(" ")
        if name in {"forward", "back", "left", "right"}:
            dist = int(arg) * fraction
            angle = math.radians(self.yaw + {"forward": 0, "right": 90, "back": 180, "left": -90}[name])
            return dist * math.cos(angle), dist * math.sin(angle), 0, 0
        if name in {"up", "down"}:
            return 0, 0, (int(arg) if name == "up" else -int(arg)) * fraction, 0
        if name in {"cw", "ccw"}:
            return 0, 0, 0, (int(arg) if name == "cw" else -int(arg)) * fraction
        return 0, 0, 0, 0

    def _apply(self, command, fraction):
        dx, dy, dh, dyaw = self._delta(command, fraction)
        self.x += dx
        self.y += dy
        self.h = max(20, self.h + dh) if dh else self.h
        self.yaw = (self.yaw + dyaw + 180) % 360 - 180

    def _progress(self, now=None):
        if not self.motion:
            return 0.0
        finish, _, _, start = self.motion
        now = time.monotonic() if now is None else now
        return min(1.0, max(0.0, (now - start) / max(1e-6, finish - start)))

    def pose(self):
        """Interpolated (x cm, y cm, yaw deg, height cm) - also valid in the middle of a motion."""
        motion = self.motion
        if motion and motion[2].split()[0] not in {"takeoff", "land"}:
            dx, dy, dh, dyaw = self._delta(motion[2], self._progress())
            return self.x + dx, self.y + dy, (self.yaw + dyaw + 180) % 360 - 180, self.h + dh
        return self.x, self.y, self.yaw, self.h

    def _finish(self, command):
        name, _, arg = command.partition(" ")
        if name == "takeoff":
            self.flying, self.motors_on, self.h = True, False, 80
        elif name == "land":
            self.flying, self.h = False, 0
        else:
            self._apply(command, 1.0)

    def _handle(self, command, peer):
        self.history.append(command)
        self.last_command = time.monotonic()
        if command in self.overrides:
            self._reply(peer, self.overrides[command])
            return
        if command == "command":
            self.enabled = True
            return self._reply(peer, "ok")
        if not self.enabled:
            return self._reply(peer, "error")
        if command == "emergency":
            self.flying = self.motors_on = False
            self.h, self.motion = 0, None
            self.events.append("EMERGENCY")
            return                                   # SDK lists no reply
        if command == "stop":
            if self.motion:
                self.events.append(f"interrupted {self.motion[2]}")
                if self.motion[2].split()[0] not in {"takeoff", "land"}:
                    self._apply(self.motion[2], self._progress())   # hover where it is now
                self.motion = None
            return self._reply(peer, "ok")
        reads = {"battery?": str(self.battery), "speed?": f"{self.speed}.0", "time?": "0s",
                 "wifi?": "90", "sdk?": "30", "sn?": "0TQZSIMULATOR", "hardware?": "RMTT"}
        if command in reads:
            return self._reply(peer, reads[command])
        if self.motion:
            return self._reply(peer, "error Not joystick")
        name, _, arg = command.partition(" ")
        if name == "speed":
            self.speed = int(arg)
            return self._reply(peer, "ok")
        if command == "motoron":
            if self.flying:
                return self._reply(peer, "error")
            self.motors_on = True
            return self._reply(peer, "ok")
        if command == "motoroff":
            self.motors_on = False
            return self._reply(peer, "ok")
        if command == "takeoff":
            if self.flying or self.battery < 10:
                return self._reply(peer, "error")
        elif not self.flying:
            return self._reply(peer, "error Motor stop" if name != "land" else "error")
        now = time.monotonic()
        self.motion = (now + self._duration(command), peer, command, now)

    def _run(self):
        next_state = 0.0
        drain = time.monotonic()
        while not self.stop_event.is_set():
            try:
                packet, peer = self.socket.recvfrom(2048)
                self._handle(packet.decode("ascii", "replace"), peer)
            except (socket.timeout, ConnectionResetError):   # Windows reports ICMP "port closed" here
                pass
            except OSError:
                return
            now = time.monotonic()
            if self.motion and now >= self.motion[0]:
                _, peer, command, _ = self.motion
                self.motion = None
                self._finish(command)
                self._reply(peer, "ok")
            if self.flying and not self.motion and now - self.last_command > self.autoland_sec:
                self.flying, self.h = False, 0
                self.events.append("AUTO-LAND 15s")
            if self.flying and now - drain > 20 * self.time_scale:
                self.battery = max(0, self.battery - 1)
                drain = now
            if self.enabled and now >= next_state:
                state = (f"mid:-1;x:0;y:0;z:0;mpry:0,0,0;pitch:0;roll:0;yaw:{self.yaw};"
                         f"vgx:0;vgy:0;vgz:0;templ:55;temph:58;tof:{self.h + 10 if self.flying else 10};"
                         f"h:{self.h};bat:{self.battery};baro:0.0;time:0;agx:0;agy:0;agz:-1000;")
                try:
                    self.socket.sendto(state.encode(), ("127.0.0.1", self.config.state_port))
                except OSError:
                    pass
                next_state = now + 0.1

    def close(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=1)
        self.socket.close()
