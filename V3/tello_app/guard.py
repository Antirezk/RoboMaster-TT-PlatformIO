"""PC side of the ESP32 ToF guard protocol (firmware/src/main.cpp documents it).

Same interface as link.TelloLink, so the Controller does not care which route is used:
    request(cmd, mode, timeout) / fire(cmd, mode) / resync(mode) / uncertain / last_tx / close()
Extra: set_avoidance(on), tof() snapshot, guard events.

Unlike the raw SDK, every guard request carries an ID, so replies can never be
mis-attributed. The guard itself re-aligns the UART reply stream after interrupts.
"""
import math
import re
import socket
import threading
import time

from .link import SDKInterrupted
from .sdk import SDKError, SDKTimeout, check_reply, validate


class AvoidRejected(SDKError):
    """The guard refused a command because of an obstacle or a sensor problem."""


def friendly_reason(detail):
    """'avoid:obstacle-front-650mm-need-900mm' -> readable English."""
    text = detail.replace("avoid:", "")
    m = re.match(r"obstacle-(\w+)-(\d+)mm-need-(\d+)mm", text)
    if m:
        return f"obstacle {m[1]} at {int(m[2]) / 1000:.2f} m (needs > {int(m[3]) / 1000:.2f} m)"
    m = re.match(r"obstacle-(\w+)-(\d+)mm-limit-(\d+)mm", text)
    if m:
        return f"obstacle {m[1]} at {int(m[2]) / 1000:.2f} m inside braking distance {int(m[3]) / 1000:.2f} m"
    m = re.match(r"too-close-(\w+)-(\d+)mm", text)
    if m:
        return f"too close on the {m[1]} side ({int(m[2])} mm) to take off / rotate"
    m = re.match(r"beyond-sensor-range-max-(\d+)cm", text)
    if m:
        return f"move longer than the sensors can verify (max {m[1]} cm with avoidance ON)"
    m = re.match(r"sensor-(\w+)-(invalid|lost)", text)
    if m:
        return f"{m[1]} sensor has no valid reading"
    m = re.match(r"critical-(\w+)-(\d+)mm(?:-retreat-(\w+))?", text)
    if m:
        return f"obstacle {m[1]} only {m[2]} mm away" + (f" - backing off to the {m[3]}" if m[3] else "")
    m = re.match(r"trapped-(\w+)-(\d+)mm-(\w+)-(\d+)mm", text)
    if m:
        return f"trapped: {m[1]} {m[2]} mm and {m[3]} {m[4]} mm - hovering"
    return text.replace("-", " ")


def parse_tof(line):
    out = {}
    for key, value in re.findall(r"(\w+)=(-?\d+)", line):
        out[key] = int(value)
    ok = re.search(r"ok=([01]{4})", line)
    out["fresh"] = [c == "1" for c in ok[1]] if ok else [False] * 4
    return out


class GuardLink:
    def __init__(self, config, log, host=None, port=None):
        self.config = config
        self.log = log
        self.remote = (host or config.esp32_host, port or config.esp32_port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("0.0.0.0" if self.remote[0] != "127.0.0.1" else "127.0.0.1", 0))
        self.socket.settimeout(0.1)
        self.lock = threading.Lock()          # one normal request at a time
        self.id_lock = threading.Lock()
        self.next_id = 0
        self.waiters = {}                     # id -> [event, status, detail]
        self.in_flight = None
        self.uncertain = False
        self.last_tx = 0.0
        self.closed = False
        self.welcome = None
        self.tof_values = {}
        self.tof_stamp = 0.0
        self.stop_event = threading.Event()
        self.receiver = threading.Thread(target=self._receive, name="guard-rx", daemon=True)
        self.receiver.start()
        self._send_line("HELLO")
        deadline = time.monotonic() + 3
        while self.welcome is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if self.welcome is None:
            self.close()
            raise SDKError(f"No answer from the ESP32 guard at {self.remote[0]}:{self.remote[1]} - "
                           "is this PC on the TT-ToF-Demo Wi-Fi and the guard firmware flashed?")
        if self.welcome.startswith("BUSY"):
            self.close()
            raise SDKError("The ESP32 guard is owned by another PC (wait 3 s after it disconnects)")
        self.heartbeat = threading.Thread(target=self._ping, name="guard-ping", daemon=True)
        self.heartbeat.start()
        self.log("ok", f"ESP32 guard: {self.welcome}")

    # ---------------------------------------------------------------- io
    def _send_line(self, line):
        self.socket.sendto(line.encode("ascii"), self.remote)

    def _ping(self):
        while not self.stop_event.wait(0.2):
            try:
                self._send_line("PING")
            except OSError:
                return

    def _receive(self):
        while not self.stop_event.is_set():
            try:
                packet, peer = self.socket.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if peer[0] != self.remote[0]:
                continue
            for line in packet.decode("ascii", "replace").splitlines():
                self._dispatch(line.strip())

    def _dispatch(self, line):
        if not line:
            return
        if line.startswith("TOF "):
            self.tof_values, self.tof_stamp = parse_tof(line), time.monotonic()
        elif line.startswith("ACK "):
            parts = line.split(" ", 3)
            if len(parts) >= 3 and parts[1].isdigit():
                waiter = self.waiters.get(int(parts[1]))
                if waiter:
                    waiter[1], waiter[2] = parts[2], parts[3] if len(parts) > 3 else ""
                    waiter[0].set()
        elif line.startswith("EVENT "):
            name, _, detail = line[6:].partition(" ")
            if name.startswith("AVOID_") or name == "HEARTBEAT_LOST":
                self.log("warn", f"Guard {name.replace('_', ' ').lower()}: {friendly_reason(detail)}")
            elif name == "AVOID":
                self.log("mode", f"Obstacle avoidance {detail}")
            elif name == "INTERNAL":
                self.log("info", f"Guard: {detail}")
        elif line.startswith(("WELCOME", "BUSY")):
            self.welcome = line
        elif line.startswith(("NOTOWNER", "PROTOCOL_ERROR")):
            self.log("warn", f"Guard: {line}")

    def _new_id(self):
        with self.id_lock:
            self.next_id += 1
            waiter = [threading.Event(), None, None]
            self.waiters[self.next_id] = waiter
            return self.next_id, waiter

    def _call(self, body, timeout):
        ident, waiter = self._new_id()
        try:
            self._send_line(f"{ident} {body}")
            if not waiter[0].wait(timeout):
                self.uncertain = True
                raise SDKTimeout(f"no answer from the ESP32 guard for '{body}' (link lost?)")
            return waiter[1], waiter[2]
        finally:
            self.waiters.pop(ident, None)

    # --------------------------------------------------------------- API
    def request(self, command, mode, timeout):
        validate(command, mode)
        with self.lock:
            if self.closed:
                raise SDKError("Connection closed")
            self.in_flight = command
            self.log("tx", f"{command}  (via guard)")
            start = time.monotonic()
            try:
                self.last_tx = start
                status, detail = self._call(f"SDK {command}", timeout + 3.0)
            finally:
                self.in_flight = None
            self.uncertain = False
            self.log("rx", f"{command} ← {status} {detail}  ({time.monotonic() - start:.1f}s)")
            if status == "OK":
                return check_reply(command, "ok" if detail == "ok-after-interrupt" else detail)
            if status == "REJECT":
                if detail.startswith("avoid:"):
                    raise AvoidRejected(f"Obstacle avoidance refused {command}: {friendly_reason(detail)}")
                raise SDKError(f"Guard refused {command}: {detail.replace('-', ' ')}")
            if status == "INTERRUPTED":
                reason = friendly_reason(detail) if detail.startswith("avoid:") else detail
                raise SDKInterrupted(f"{command} interrupted ({reason})")
            if status == "TIMEOUT":
                raise SDKTimeout(f"{command}: no reply from the drone (result unknown, not resent)")
            raise SDKError(f"{command} rejected by the drone: {detail!r}")

    def fire(self, command, mode):
        validate(command, mode)
        busy = self.in_flight
        ident, waiter = self._new_id()
        self._send_line(f"{ident} SDK {command}")
        self.last_tx = time.monotonic()
        self.log("tx", f"{command}  (via guard, immediate)")
        threading.Thread(target=lambda: (waiter[0].wait(5), self.waiters.pop(ident, None)), daemon=True).start()
        if busy:
            self.log("warn", f"{command} sent immediately, interrupting {busy}")
        return busy

    def resync(self, mode):
        value = int(self.request("battery?", mode, 4.0))
        self.uncertain = False
        return value

    def set_avoidance(self, on):
        """Non-blocking: the guard confirms through the next ACK / TOF line."""
        ident, waiter = self._new_id()
        self._send_line(f"{ident} AVOID {'ON' if on else 'OFF'}")
        threading.Thread(target=lambda: (waiter[0].wait(3), self.waiters.pop(ident, None)), daemon=True).start()

    def tof(self):
        age = time.monotonic() - self.tof_stamp if self.tof_stamp else math.inf
        return dict(self.tof_values), age

    def close(self):
        self.closed = True
        self.stop_event.set()
        try:
            self.socket.close()
        except OSError:
            pass
