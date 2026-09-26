"""Two independent sockets. SDK has no request IDs: never retry timed-out actions."""
import logging
import math
import re
import socket
import threading
import time

LOG = logging.getLogger("pc_demo")


class SDKError(RuntimeError):
    pass


def validate_wire_command(command):
    """Last boundary before UDP: only this demo's SDK subset is permitted."""
    if not isinstance(command, str):
        raise SDKError("SDK command must be a string")
    if command in {"command", "mon", "mdirection 0", "takeoff", "land", "stop", "battery?"}:
        return
    match = re.fullmatch(r"(up|down|left|right|forward|back|cw|ccw) ([1-9][0-9]{0,2})", command)
    if match:
        low, high = (1, 180) if match[1] in {"cw", "ccw"} else (20, 100)
        if low <= int(match[2]) <= high:
            return
    match = re.fullmatch(r"go 0 0 ([1-9][0-9]{1,2}) ([1-9][0-9]) m([1-8])", command)
    if match and 30 <= int(match[1]) <= 120 and 10 <= int(match[2]) <= 30:
        return
    raise SDKError(f"SDK 指令格式或参数不在 Demo 白名单中，未发送：{command!r}")


class CommandChannel:
    def __init__(self, config):
        self.config = config
        self.remote = (config.drone_ip, config.command_port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.bind((config.bind_host, config.local_command_port))
        except BaseException:
            self.socket.close()
            raise
        self.lock = threading.RLock()
        self.ready = False
        self.poisoned = False
        self.last_send = 0.0

    def send(self, command, *, flight=False):
        validate_wire_command(command)
        with self.lock:
            if self.poisoned:
                raise SDKError("command channel uncertain after timeout/interruption; restart after landing")
            if command != "command" and not self.ready:
                raise SDKError("SDK handshake has not returned ok")
            # Drain unsolicited responses already queued; no retries or pipelining.
            self.socket.setblocking(False)
            try:
                while True:
                    packet, peer = self.socket.recvfrom(4096)
                    LOG.warning("RX discarded unsolicited response %r from %s", packet, peer)
            except BlockingIOError:
                pass
            timeout = self.config.flight_timeout_sec if flight else self.config.command_timeout_sec
            deadline = time.monotonic() + timeout
            LOG.debug("TX command %s:%s <- %s", *self.remote, command)
            try:
                self.socket.sendto(command.encode("ascii"), self.remote)
                self.last_send = time.monotonic()
                LOG.info("[已发送] %s  →  %s:%s", command, *self.remote)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("SDK response timeout")
                    self.socket.settimeout(remaining)
                    packet, peer = self.socket.recvfrom(4096)
                    if peer != self.remote:
                        LOG.warning("RX ignored foreign command peer %s", peer)
                        continue
                    response = packet.decode("ascii", errors="replace").strip()
                    LOG.info("[收到回复] %s  ←  %r（%.2f 秒）", command, response,
                             time.monotonic() - self.last_send)
                    if command == "battery?":
                        valid = response.isdigit() and 0 <= int(response) <= 100
                    else:
                        valid = response.lower() == "ok"
                    if not valid:
                        if command == "command":
                            self.ready = False
                        raise SDKError(f"{command}: {response!r}")
                    if command == "command":
                        self.ready = True
                    return response
            except SDKError:
                raise
            except BaseException:
                self.poisoned = True
                LOG.error("[结果未知] %r 未收到确认或等待被中断；不重发，后续控制已阻止", command)
                raise

    def unconfirmed_land(self):
        """Exit-only fallback. Never interpret a possibly late ACK as landing success."""
        with self.lock:
            if self.ready:
                self.socket.sendto(b"land", self.remote)
                LOG.warning("[已发送] land（退出时尝试降落；通道异常，结果未确认）")

    def close(self):
        self.socket.close()


def parse_state(packet):
    values = {}
    for field in packet.decode("ascii", errors="replace").strip().split(";"):
        key, sep, value = field.partition(":")
        if not sep:
            continue
        try:
            if key == "mpry":
                parsed = tuple(float(part) for part in value.split(","))
                if len(parsed) != 3 or not all(math.isfinite(x) for x in parsed):
                    continue
            else:
                parsed = float(value)
                if not math.isfinite(parsed):
                    continue
                if parsed.is_integer():
                    parsed = int(parsed)
            values[key] = parsed
        except ValueError:
            continue
    return values


class StateChannel:
    def __init__(self, config):
        self.config = config
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.bind((config.bind_host, config.state_port))
            self.socket.settimeout(0.2)
        except BaseException:
            self.socket.close()
            raise
        self.condition = threading.Condition()
        self.values = {}
        self.received_at = 0.0
        self.sequence = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._receive, name="tt-telemetry", daemon=True)

    def start(self):
        LOG.info("STATE listening on %s:%s (independent socket/thread); video disabled",
                 self.config.bind_host, self.config.state_port)
        self.thread.start()

    def snapshot(self):
        with self.condition:
            return dict(self.values), self.received_at, self.sequence

    def fresh(self):
        values, stamp, _ = self.snapshot()
        if not stamp or time.monotonic() - stamp > self.config.state_max_age_sec:
            raise SDKError("telemetry missing/stale; check Wi-Fi and inbound UDP state port")
        return values

    def _receive(self):
        next_report = 0.0
        previous_stale = None
        while not self.stop_event.is_set():
            try:
                packet, peer = self.socket.recvfrom(4096)
                if peer[0] != self.config.drone_ip:
                    continue
                values = parse_state(packet)
                LOG.debug("RX state %s -> %r", peer, packet)
                if not values:
                    continue
                with self.condition:
                    # Replace the entire frame: missing fields must not inherit old coordinates.
                    self.values = values
                    self.received_at = time.monotonic()
                    self.sequence += 1
                    self.condition.notify_all()
            except socket.timeout:
                pass
            except OSError:
                if not self.stop_event.is_set():
                    LOG.exception("telemetry socket failed")
                return
            now = time.monotonic()
            if now >= next_report:
                values, stamp, _ = self.snapshot()
                age = now - stamp if stamp else float("inf")
                stale = age > self.config.state_max_age_sec
                if stale != previous_stale:
                    LOG.info("[遥测连接] %s", "未收到新鲜遥测（STALE），请检查连接" if stale
                             else "已收到遥测；输入 status 查看数值，或加 --telemetry 持续显示")
                    previous_stale = stale
                LOG.info("STATE age=%.1fs%s %s", age,
                         " STALE" if age > self.config.state_max_age_sec else "",
                         " ".join(f"{k}={values.get(k, '?')}" for k in
                                  ("bat", "h", "tof", "pitch", "roll", "yaw", "vgx", "vgy", "vgz", "mid", "x", "y", "z", "mpry")),
                         extra={"telemetry_summary": True})
                next_report = now + 1.0

    def close(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.socket.close()
