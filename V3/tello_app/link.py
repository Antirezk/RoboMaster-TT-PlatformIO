"""UDP link to the TT: one request at a time on 8889, telemetry on 8890.

The SDK has no request IDs. Rules that follow from that:
* never retry a command that timed out;
* after a timeout or an interrupt the reply stream is "uncertain" - a late
  "ok" could belong to any earlier command. `resync()` drains the socket and
  does a `battery?` round trip (its numeric reply cannot be confused with a
  late "ok") before normal commands are allowed again;
* stop / land / emergency can be fired while another command is waiting.
"""
import math
import socket
import threading
import time

from .sdk import SDKError, SDKTimeout, check_reply, validate


class SDKInterrupted(SDKError):
    pass


class TelloLink:
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self.remote = (config.drone_ip, config.command_port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.bind((config.bind_host, config.local_command_port))
        except OSError as exc:
            self.socket.close()
            raise SDKError(f"Local UDP port {config.local_command_port} is busy (is another control program still open?): {exc}") from exc
        self.lock = threading.RLock()
        self.abort = threading.Event()
        self.in_flight = None
        self.uncertain = False
        self.last_tx = 0.0
        self.closed = False

    # -- helpers -----------------------------------------------------------
    def _drain(self, seconds=0.0):
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            self.socket.settimeout(max(0.0, remaining) if seconds else 0.0)
            try:
                packet, peer = self.socket.recvfrom(2048)
            except (BlockingIOError, socket.timeout):
                return
            if peer[0] == self.remote[0]:
                self.log("warn", f"Discarded late/extra reply: {packet.decode('ascii', 'replace').strip()!r}")
            if seconds and time.monotonic() >= deadline:
                return

    def _send_raw(self, command):
        self.socket.sendto(command.encode("ascii"), self.remote)
        self.last_tx = time.monotonic()
        self.log("tx", command)

    # -- API ---------------------------------------------------------------
    def request(self, command, mode, timeout):
        validate(command, mode)
        with self.lock:
            if self.closed:
                raise SDKError("Connection closed")
            self._drain()
            self.abort.clear()
            self.in_flight = command
            start = time.monotonic()
            try:
                self._send_raw(command)
                while True:
                    if self.abort.is_set():
                        self.uncertain = True
                        raise SDKInterrupted(f"{command} interrupted by an urgent command")
                    remaining = start + timeout - time.monotonic()
                    if remaining <= 0:
                        self.uncertain = True
                        raise SDKTimeout(f"{command}: no reply within {timeout:.0f} s (result unknown, not resent)")
                    self.socket.settimeout(min(0.1, remaining))
                    try:
                        packet, peer = self.socket.recvfrom(2048)
                    except socket.timeout:
                        continue
                    if peer[0] != self.remote[0]:
                        continue
                    reply = packet.decode("ascii", "replace").strip()
                    if self.abort.is_set():
                        self.uncertain = True
                        self.log("rx", f"{reply} (interrupted, owner unknown)")
                        raise SDKInterrupted(f"{command} interrupted by an urgent command")
                    self.log("rx", f"{command} ← {reply}  ({time.monotonic() - start:.1f}s)")
                    return check_reply(command, reply)
            except OSError as exc:
                self.uncertain = True
                raise SDKError(f"Network error: {exc}") from exc
            finally:
                self.in_flight = None

    def fire(self, command, mode):
        """Send immediately, even while another request is waiting. No reply is awaited."""
        validate(command, mode)
        if self.closed:
            raise SDKError("Connection closed")
        busy = self.in_flight
        if busy:
            # Mark the waiter as aborted *before* sending, so the urgent command's
            # own "ok" can never be read as success of the interrupted one.
            self.uncertain = True
            self.abort.set()
        self._send_raw(command)
        if busy:
            self.log("warn", f"{command} sent immediately, interrupting {busy}")
        return busy

    def resync(self, mode):
        """Restore reply alignment after a timeout/interrupt. Returns battery %."""
        with self.lock:
            self._drain(0.8)
            for _ in range(3):
                try:
                    value = int(self.request("battery?", mode, 3.0))
                except SDKTimeout:
                    raise
                except SDKError as exc:
                    # A late "ok" arrived first - drain and try again.
                    self.log("warn", f"Resync: {exc}")
                    self._drain(0.5)
                    continue
                self.uncertain = False
                self.log("info", "Reply stream re-aligned")
                return value
            raise SDKError("Could not re-align the reply stream - check the drone")

    def close(self):
        self.closed = True
        self.abort.set()
        with self.lock:
            self.socket.close()


def parse_state(packet):
    values = {}
    for field in packet.decode("ascii", errors="replace").strip().split(";"):
        key, sep, value = field.partition(":")
        if not sep or not key:
            continue
        try:
            if key == "mpry":
                continue
            number = float(value)
        except ValueError:
            continue
        if math.isfinite(number):
            values[key] = int(number) if number.is_integer() else number
    return values


class StateListener:
    """Display-only telemetry from UDP 8890. Missing telemetry never blocks commands."""
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self.values = {}
        self.stamp = 0.0
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.bind((config.bind_host, config.state_port))
        except OSError:
            self.socket.close()
            raise
        self.socket.settimeout(0.2)
        self.thread = threading.Thread(target=self._run, name="tt-state", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                packet, peer = self.socket.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if peer[0] != self.config.drone_ip:
                continue
            values = parse_state(packet)
            if values:
                with self.lock:
                    self.values, self.stamp = values, time.monotonic()

    def snapshot(self):
        with self.lock:
            age = time.monotonic() - self.stamp if self.stamp else math.inf
            return dict(self.values), age

    def close(self):
        self.stop_event.set()
        self.socket.close()
        self.thread.join(timeout=1)
