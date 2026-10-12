"""Loopback stand-in for the ESP32 ToF guard (firmware/src), for practising without hardware.

It speaks the same PC protocol as the firmware, relays SDK commands to the TT Simulator,
and produces ToF readings from the simulated drone's position inside a rectangular room,
so obstacle avoidance can be seen working. The policy below mirrors firmware/src/Avoidance.h
(same thresholds); the firmware is the reference implementation.
"""
from dataclasses import dataclass
import math
import socket
import threading
import time

from .simulator import Simulator

# --- mirror of firmware/src/GuardConfig.h
SENSOR_MAX_MM = 2000
SENSOR_STALE_S = 0.3
MOVE_MARGIN_MM = 400
PERIMETER_MM = 300
STOP_BASE_MM = 400
STOP_REACTION_S = 0.6
STOP_MAX_MM = 1200
CRITICAL_MM = 250
RETREAT_CM = 20
RETREAT_COOLDOWN_S = 2.5
HEARTBEAT_HOVER_S = 1.5
HEARTBEAT_LAND_S = 6.0
OWNER_TAKEOVER_S = 3.0
KEEPALIVE_S = 5.0
SIDES = ("front", "back", "left", "right")
MOVES = ("forward", "back", "left", "right", "up", "down")
URGENT = {"stop", "land", "emergency", "motoroff"}

# Room for the simulated drone, in cm. The drone starts at (0, 0) facing +x.
ROOM = (-150.0, 300.0, -150.0, 150.0)   # x_min, x_max, y_min, y_max
BODY_CM = 10.0                           # sensor sits this far from the centre


@dataclass
class Sample:
    ok: bool = False
    mm: int = 0
    at: float = 0.0
    closing: float = 0.0


def end_clearance(speed):
    """Mirror of Avoidance::endClearance(): braking distance at the commanded speed."""
    return int(min(STOP_MAX_MM, MOVE_MARGIN_MM + speed * 10 * STOP_REACTION_S))


class Policy:
    def __init__(self, time_scale=1.0):
        self.enabled = True
        self.r = [Sample() for _ in range(4)]
        # The simulator runs faster than real time; measured rates are scaled back.
        self.time_scale = time_scale

    def update(self, i, mm, now):
        """mm=None -> sensor fault; mm >= SENSOR_MAX_MM -> out of range."""
        s = self.r[i]
        if mm is None:
            s.ok, s.closing = False, 0.0
            return
        value = min(int(mm), SENSOR_MAX_MM)
        if s.ok and now > s.at and value < SENSOR_MAX_MM:
            v = max(-3000.0, min(3000.0, (s.mm - value) / (now - s.at) * self.time_scale))
            s.closing = 0.6 * s.closing + 0.4 * v
        elif value >= SENSOR_MAX_MM:
            s.closing = 0.0
        s.ok, s.mm, s.at = True, value, now

    def fresh(self, i, now):
        return self.r[i].ok and now - self.r[i].at <= SENSOR_STALE_S

    def stop_distance(self, i):
        return int(min(STOP_MAX_MM, STOP_BASE_MM + max(0.0, self.r[i].closing) * STOP_REACTION_S))

    def admit(self, cmd, now, speed=10):
        if not self.enabled:
            return None
        name, _, arg = cmd.partition(" ")
        if name in MOVES[:4]:
            for i in range(4):
                if not self.fresh(i, now):
                    return f"sensor-{SIDES[i]}-invalid"
            clearance = end_clearance(speed)
            axis, need = MOVES.index(name), int(arg) * 10 + clearance
            s = self.r[axis]
            if need >= SENSOR_MAX_MM:
                if s.mm < SENSOR_MAX_MM:
                    return f"obstacle-{SIDES[axis]}-{s.mm}mm-need-{need}mm"
                return f"beyond-sensor-range-max-{(SENSOR_MAX_MM - clearance) // 10 - 1}cm"
            if s.mm <= need:
                return f"obstacle-{SIDES[axis]}-{s.mm}mm-need-{need}mm"
        elif name in {"cw", "ccw", "takeoff"}:
            for i in range(4):
                if not self.fresh(i, now):
                    return f"sensor-{SIDES[i]}-invalid"
                if self.r[i].mm <= PERIMETER_MM:
                    return f"too-close-{SIDES[i]}-{self.r[i].mm}mm-need-{PERIMETER_MM}mm"
        return None

    def monitor(self, active, now, last_retreat):
        """-> (action, axis, why) with action in {None, 'stop', 'retreat'}."""
        if not self.enabled:
            return None, -1, ""
        name = active.split()[0] if active else ""
        moving = name in MOVES or name in {"cw", "ccw", "takeoff"}
        for i in range(4):
            if not self.fresh(i, now):
                return ("stop", -1, f"sensor-{SIDES[i]}-lost") if moving else (None, -1, "")
        axis = MOVES.index(name) if name in MOVES[:4] else -1
        if moving and axis >= 0:
            limit = self.stop_distance(axis)
            if self.r[axis].mm <= limit:
                return "stop", -1, f"obstacle-{SIDES[axis]}-{self.r[axis].mm}mm-limit-{limit}mm"
        for i in range(4):
            if self.r[i].mm >= CRITICAL_MM:
                continue
            if moving and axis >= 0 and i == axis ^ 1:
                continue
            if moving:
                return "stop", -1, f"critical-{SIDES[i]}-{self.r[i].mm}mm"
            if active:
                return None, -1, ""
            if last_retreat and now - last_retreat < RETREAT_COOLDOWN_S:
                return None, -1, ""
            opposite = i ^ 1
            if self.r[opposite].mm > RETREAT_CM * 10 + MOVE_MARGIN_MM:
                return "retreat", opposite, f"critical-{SIDES[i]}-{self.r[i].mm}mm-retreat-{SIDES[opposite]}"
            return None, -1, f"trapped-{SIDES[i]}-{self.r[i].mm}mm-{SIDES[opposite]}-{self.r[opposite].mm}mm"
        return None, -1, ""


def timeout_for(cmd, speed):
    name, _, arg = cmd.partition(" ")
    if name in {"takeoff", "land"}:
        return 20.0
    if name in MOVES:
        return 8.0 + 1.5 * int(arg) / (speed or 10)
    if name in {"cw", "ccw"}:
        return 8.0 + int(arg) / 30
    return 5.0 if name in {"command", "motoron", "motoroff"} else 4.0


class GuardSimulator:
    def __init__(self, config, time_scale=0.5):
        self.tt = Simulator(config, time_scale=time_scale)
        self.pc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.pc.bind(("127.0.0.1", 0))
        self.pc.setblocking(False)
        self.uart = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # stands in for the TT UART
        self.uart.bind(("127.0.0.1", 0))
        self.uart.setblocking(False)
        self.absorb = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # swallow TT state packets
        try:
            self.absorb.bind(("127.0.0.1", self.tt.config.state_port))
        except OSError:
            pass
        self.address = self.pc.getsockname()
        self.policy = Policy(time_scale)
        self.owner = None
        self.last_seen = 0.0
        self.last_id = 0
        self.pending = None           # dict(id, cmd, deadline)
        self.ambiguous = False
        self.resync_needed = False
        self.resyncing = False
        self.deferred = None
        self.queued = None            # PC command waiting behind our own keep-alive
        self.airborne = self.sdk_ready = self.motors_on = False
        self.speed = 10
        self.battery = -1
        self.last_control_tx = 0.0
        self.last_retreat = 0.0
        self.last_trapped = 0.0
        self.hover_sent = self.land_sent = False
        self.events = []
        self.wire = []
        self.blocked_sensor = None    # tests: index of a sensor to fail
        self.inject = {}              # tests / demo: sensor index -> fixed distance in mm
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="guard-sim", daemon=True)

    def start(self):
        self.tt.start()
        self.thread.start()
        return self

    # ------------------------------------------------------------ helpers
    def send(self, line):
        if self.owner:
            try:
                self.pc.sendto((line + "\n").encode(), self.owner)
            except OSError:
                pass

    def done(self, ident, cmd, status, detail):
        if ident:
            self.send(f"ACK {ident} {status} {detail}")
        else:
            self.send(f"EVENT INTERNAL {cmd} {status} {detail}")

    def _tt_send(self, cmd):
        self.wire.append(cmd)
        self.uart.sendto(cmd.encode(), ("127.0.0.1", self.tt.config.command_port))

    def _start(self, ident, cmd, now):
        self.pending = {"id": ident, "cmd": cmd, "deadline": now + timeout_for(cmd, self.speed)}
        self.ambiguous = False
        self._tt_send(cmd)
        if not cmd.endswith("?"):
            self.last_control_tx = now
        if cmd == "emergency":
            self.airborne = self.motors_on = False
            self.resync_needed = True
            self._finish("OK", "sent-no-reply")

    def _finish(self, status, detail):
        p, amb = self.pending, self.ambiguous
        self.pending, self.ambiguous = None, False
        self.done(p["id"], p["cmd"], status, "ok-after-interrupt" if amb and status == "OK" else detail)
        if self.queued and not self.pending:
            ident, cmd = self.queued
            self.queued = None
            self.submit(ident, cmd, time.monotonic())

    def submit(self, ident, cmd, now, reason=None):
        if self.pending:
            if cmd not in URGENT:
                keep_alive = self.pending["id"] == 0 and self.pending["cmd"] == "command" and not self.resyncing
                if keep_alive and self.queued is None:
                    self.queued = (ident, cmd)
                    return True
                return False
            detail = reason or cmd
            if self.queued:
                self.done(self.queued[0], self.queued[1], "INTERRUPTED", detail)
                self.queued = None
            if self.resyncing:
                self.resyncing, self.pending = False, None
                if self.deferred:
                    self.done(self.deferred[0], self.deferred[1], "INTERRUPTED", detail)
                self.deferred = None
            else:
                old, self.pending = self.pending, None
                if old["cmd"] == "takeoff":
                    self.airborne = True
                self.done(old["id"], old["cmd"], "INTERRUPTED", detail)
            self.resync_needed = True
            self._start(ident, cmd, now)
            if self.pending:
                self.ambiguous = True
            return True
        if self.resync_needed and cmd not in URGENT:
            self.deferred = (ident, cmd)
            self.resyncing = True
            self._start(0, "battery?", now)
            return True
        self._start(ident, cmd, now)
        return True

    def _on_reply(self, reply, now):
        if not self.pending:
            return
        cmd = self.pending["cmd"]
        if self.resyncing:
            if not reply.isdigit():
                return
            self.battery, self.resyncing, self.resync_needed, self.pending = int(reply), False, False, None
            if self.deferred:
                ident, nxt = self.deferred
                self.deferred = None
                self._start(ident, nxt, now)
            return
        ok = reply.isdigit() if cmd == "battery?" else (
            reply not in {"ok"} and not reply.startswith("error") if cmd.endswith("?") else reply == "ok")
        if ok:
            if cmd == "command":
                self.sdk_ready = True
            elif cmd == "takeoff":
                self.airborne, self.motors_on = True, False
            elif cmd == "land":
                self.airborne = False
            elif cmd == "motoron":
                self.motors_on = True
            elif cmd == "motoroff":
                self.motors_on = False
            elif cmd.startswith("speed "):
                self.speed = int(cmd.split()[1])
            elif cmd == "battery?":
                self.battery = int(reply)
        self._finish("OK" if ok else "ERROR", reply)

    def sensor_mm(self):
        """Distances from the drone's current position to the room walls along the 4 body axes."""
        x, y, yaw, _ = self.tt.pose()
        x0, x1, y0, y1 = ROOM
        out = []
        for offset in (0, 180, -90, 90):          # front, back, left, right
            a = math.radians(yaw + offset)
            dx, dy = math.cos(a), math.sin(a)
            hits = []
            if dx > 1e-9: hits.append((x1 - x) / dx)
            if dx < -1e-9: hits.append((x0 - x) / dx)
            if dy > 1e-9: hits.append((y1 - y) / dy)
            if dy < -1e-9: hits.append((y0 - y) / dy)
            out.append(max(0, int((min(hits) - BODY_CM) * 10)))
        return out

    # --------------------------------------------------------------- loop
    def _frame(self, line, now):
        if line == "HELLO":
            self.last_id = 0
            self.hover_sent = self.land_sent = False
            self.send(f"WELCOME TOFGUARD 3.0.0 sim avoid={int(self.policy.enabled)}")
            return
        ident_text, _, rest = line.partition(" ")
        if not ident_text.isdigit() or int(ident_text) == 0:
            self.send("PROTOCOL_ERROR expected-<id>-<verb>")
            return
        ident = int(ident_text)
        if ident <= self.last_id:
            self.send(f"ACK {ident} REJECT duplicate-or-old-id")
            return
        self.last_id = ident
        if rest.startswith("SDK "):
            cmd = rest[4:]
            if not self.sdk_ready and cmd != "command" and cmd not in URGENT:
                return self.send(f"ACK {ident} REJECT sdk-not-ready-send-command-first")
            why = None if cmd in URGENT else self.policy.admit(cmd, now, self.speed)
            if why:
                return self.send(f"ACK {ident} REJECT avoid:{why}")
            if not self.submit(ident, cmd, now):
                self.send(f"ACK {ident} REJECT busy:{self.pending['cmd']}")
        elif rest in {"AVOID ON", "AVOID OFF"}:
            self.policy.enabled = rest.endswith("ON")
            self.send(f"ACK {ident} OK avoid={int(self.policy.enabled)}")
            self.send(f"EVENT AVOID {'ON' if self.policy.enabled else 'OFF'}")
        elif rest == "STATUS":
            self.send(f"ACK {ident} OK fw=3.0.0 mode=sim air={int(self.airborne)} avoid={int(self.policy.enabled)}")
        else:
            self.send(f"ACK {ident} REJECT unknown-verb")

    def _run(self):
        next_sample = next_status = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            for sock, kind in ((self.pc, "pc"), (self.uart, "tt")):
                while True:
                    try:
                        packet, peer = sock.recvfrom(4096)
                    except (BlockingIOError, ConnectionResetError):
                        break
                    except OSError:
                        return
                    text = packet.decode("ascii", "replace").strip()
                    if kind == "tt":
                        self._on_reply(text, now)
                        continue
                    if text == "HELLO":
                        if self.owner in (None, peer) or now - self.last_seen > OWNER_TAKEOVER_S:
                            self.owner, self.last_seen = peer, now
                            self._frame(text, now)
                        else:
                            self.pc.sendto(b"BUSY another-pc-owns-this-guard\n", peer)
                        continue
                    if peer != self.owner:
                        self.pc.sendto(b"NOTOWNER send-HELLO-first\n", peer)
                        continue
                    self.last_seen = now
                    if text != "PING":
                        self._frame(text, now)
            if self.pending and self.pending["cmd"] != "emergency" and now >= self.pending["deadline"]:
                if self.resyncing:
                    self.resyncing, self.pending = False, None
                    if self.deferred:
                        self.done(self.deferred[0], self.deferred[1], "ERROR", "resync-timeout")
                    self.deferred = None
                else:
                    if self.pending["cmd"] == "takeoff":
                        self.airborne = True
                    self.resync_needed = True
                    self._finish("TIMEOUT", "no-reply-not-resent")
            if now >= next_sample:
                next_sample = now + 0.05
                for i, mm in enumerate(self.sensor_mm()):
                    mm = self.inject.get(i, mm)
                    self.policy.update(i, None if i == self.blocked_sensor else mm, now)
                if self.airborne and min(self.sensor_mm()) <= 0 and "COLLISION" not in self.events:
                    self.events.append("COLLISION")
                    self.send("EVENT SIM_COLLISION the simulated drone touched a wall")
            self._supervise(now)
            if now >= next_status:
                next_status = now + 0.1
                r = self.policy.r
                fresh = "".join("1" if self.policy.fresh(i, now) else "0" for i in range(4))
                self.send(f"TOF f={r[0].mm} b={r[1].mm} l={r[2].mm} r={r[3].mm} ok={fresh} "
                          f"cf={int(r[0].closing)} cb={int(r[1].closing)} cl={int(r[2].closing)} "
                          f"cr={int(r[3].closing)} avoid={int(self.policy.enabled)} air={int(self.airborne)} "
                          f"busy={int(bool(self.pending))} rs={int(self.resync_needed)} bat={self.battery}")
            time.sleep(0.005)

    def _supervise(self, now):
        if self.airborne and self.owner:
            silent = now - self.last_seen
            if silent > HEARTBEAT_HOVER_S and not self.hover_sent:
                self.hover_sent = True
                self.submit(0, "stop", now, "heartbeat-lost")
                self.send("EVENT HEARTBEAT_LOST hover")
            if silent > HEARTBEAT_LAND_S and not self.land_sent:
                self.land_sent = True
                self.submit(0, "land", now, "heartbeat-lost")
                self.send("EVENT HEARTBEAT_LOST land")
            if silent < HEARTBEAT_HOVER_S:
                self.hover_sent = self.land_sent = False
        if self.airborne:
            active = self.pending["cmd"] if self.pending else ""
            action, axis, why = self.policy.monitor(active, now, self.last_retreat)
            if action == "stop" and active not in {"stop", "land", "emergency"}:
                self.events.append(f"AVOID_STOP {why}")
                self.submit(0, "stop", now, f"avoid:{why}")
                self.send(f"EVENT AVOID_STOP {why}")
            elif action == "retreat" and not self.pending:
                self.last_retreat = now
                self.events.append(f"AVOID_RETREAT {why}")
                self.submit(0, f"{MOVES[axis]} {RETREAT_CM}", now)
                self.send(f"EVENT AVOID_RETREAT {why}")
            elif why.startswith("trapped") and now - self.last_trapped > 2:
                self.last_trapped = now
                self.send(f"EVENT AVOID_TRAPPED {why}")
        if (self.sdk_ready and (self.airborne or self.motors_on) and not self.pending
                and now - self.last_control_tx > KEEPALIVE_S):
            self.submit(0, "command", now)

    def close(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=1)
        for sock in (self.pc, self.uart, self.absorb):
            sock.close()
        self.tt.close()
