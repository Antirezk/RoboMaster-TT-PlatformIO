"""Single owner of the drone link. GUI, voice and keyboard all go through submit().

Threads: the caller (GUI / ASR) only validates and hands over an Intent; one
worker thread talks to the drone. Only stop / land / emergency / motoroff may
pre-empt a running command - everything else is refused while busy (no queue,
so a stale "forward 100" never fires seconds later).
"""
import queue
import threading
import time

from . import sdk
from .guard import GuardLink
from .guard_sim import GuardSimulator
from .link import SDKInterrupted, StateListener, TelloLink
from .sdk import FLIGHT, TEST, SDKError, SDKTimeout, Intent, timeout_for
from .simulator import Simulator


class Controller:
    def __init__(self, config, log=None, link_factory=TelloLink, sim_scale=0.5):
        self.config = config
        self._log = log or (lambda kind, text: None)
        self.link_factory = link_factory
        self.sim_scale = sim_scale
        self.mode = TEST
        self.route = config.route
        self.connected = False
        self.connecting = False
        self.simulated = False
        self.airborne = False
        self.motors_on = False
        self.battery = None
        self.speed_set = None
        self.busy = None
        self.info = {}
        self.link = self.state = self.sim = None
        self.pending = None
        self.cond = threading.Condition()
        self.jobs = queue.SimpleQueue()      # connect / disconnect / close
        self.closed = threading.Event()
        self.worker = threading.Thread(target=self._run, name="tt-control", daemon=True)
        self.worker.start()

    # ---------------------------------------------------------------- utils
    def log(self, kind, text):
        try:
            self._log(kind, text)
        except Exception:
            pass

    def snapshot(self):
        values, age = self.state.snapshot() if self.state else ({}, float("inf"))
        link = self.link
        tof, tof_age = link.tof() if isinstance(link, GuardLink) else ({}, float("inf"))
        battery = values.get("bat") if age <= self.config.state_max_age_sec else None
        return {"connected": self.connected, "connecting": self.connecting, "simulated": self.simulated,
                "mode": self.mode, "airborne": self.airborne, "motors_on": self.motors_on,
                "battery": battery if battery is not None else self.battery,
                "busy": self.busy, "uncertain": bool(self.link and self.link.uncertain),
                "height": values.get("h") if age <= self.config.state_max_age_sec else None,
                "yaw": values.get("yaw") if age <= self.config.state_max_age_sec else None,
                "temp": values.get("temph") if age <= self.config.state_max_age_sec else None,
                "telemetry_age": age, "speed": self.speed_set, "info": dict(self.info),
                "route": self.route, "tof": tof, "tof_age": tof_age,
                "avoid": bool(tof.get("avoid")) if tof and tof_age < 1.0 else None}

    # ------------------------------------------------------------- commands
    def set_route(self, route):
        if route not in {"direct", "guard"}:
            raise SDKError("Unknown route")
        if self.connected or self.connecting:
            raise SDKError("Disconnect before changing the connection route")
        self.route = route

    def set_avoidance(self, on):
        if not self.connected or not isinstance(self.link, GuardLink):
            raise SDKError("Obstacle avoidance needs the ESP32 guard route")
        self.link.set_avoidance(on)
        self.log("mode", f"Requesting obstacle avoidance {'ON' if on else 'OFF'} …")

    def connect(self, simulate=False):
        if not self.connected and not self.connecting:
            self.connecting = True
            self.jobs.put(("connect", simulate))
            with self.cond:
                self.cond.notify_all()

    def disconnect(self):
        self.jobs.put(("disconnect", None))
        with self.cond:
            self.cond.notify_all()

    def close(self):
        self.jobs.put(("close", None))
        with self.cond:
            self.pending = None
            self.cond.notify_all()

    def set_mode(self, mode):
        if mode not in sdk.MODES:
            raise SDKError("Unknown mode")
        if mode == self.mode:
            return f"Already in {sdk.MODE_NAMES[mode]} mode"
        if self.airborne:
            raise SDKError("Cannot switch mode while flying - land first")
        if self.motors_on:
            raise SDKError("Motors are still spinning - say power off first")
        if self.busy or self.pending:
            raise SDKError(f"Busy with {self.busy or self.pending.sdk} - try again in a moment")
        self.mode = mode
        self.log("mode", f"Switched to {sdk.MODE_NAMES[mode]} mode")
        return f"Switched to {sdk.MODE_NAMES[mode]} mode"

    def submit(self, intent: Intent):
        """Accept or refuse an intent. Returns a short Chinese status line; raises SDKError on refusal."""
        if intent.action not in sdk.MODE_ACTIONS[self.mode]:
            raise SDKError(f"\"{intent.label}\" is not available in {sdk.MODE_NAMES[self.mode]} mode")
        if intent.action == "status":
            s = self.snapshot()
            where = "Flying" if s["airborne"] else ("Motors spinning" if s["motors_on"] else "On ground")
            return f"{where} · battery {s['battery'] if s['battery'] is not None else '—'}% · " \
                   f"{'running ' + s['busy'] if s['busy'] else 'idle'}"
        if not self.connected:
            raise SDKError("Drone not connected: recognised only, nothing sent")
        link = self.link
        if intent.action == "emergency":
            link.fire("emergency", FLIGHT)
            with self.cond:
                self.pending = None
            self.airborne = self.motors_on = False
            self.log("error", "emergency sent: motors stopped immediately (no SDK reply). Check the drone.")
            return "emergency sent"
        with self.cond:
            if self.busy:
                if not intent.urgent:
                    raise SDKError(f"Busy with {self.busy} - no queueing; say stop to interrupt")
                if self.mode == FLIGHT and intent.action in {"stop", "land"} and (self.airborne or self.busy == "takeoff"):
                    # Hover first; the SDK replies "error" to most commands during a motion.
                    link.fire("stop", FLIGHT)
                    self.pending = intent if intent.action == "land" else None
                    self.cond.notify_all()
                    return "stop sent immediately" + (", landing next" if intent.action == "land" else "")
            if self.pending and not intent.urgent:
                raise SDKError(f"Previous {self.pending.sdk} has not started yet - no queueing")
            if self.pending:
                self.log("warn", f"Cancelled pending {self.pending.sdk}, replaced by {intent.sdk}")
            self.pending = intent
            self.cond.notify_all()
        return f"Submitted: {intent.sdk}"

    # --------------------------------------------------------------- worker
    def _run(self):
        try:
            while True:
                with self.cond:
                    if self.pending is None and self.jobs.empty():
                        self.cond.wait(timeout=0.2)
                    intent, self.pending = self.pending, None
                try:
                    job, arg = self.jobs.get_nowait()
                except queue.Empty:
                    job = None
                if job == "close":
                    self._shutdown()
                    return
                if job == "connect":
                    self._connect(arg)
                elif job == "disconnect":
                    self._shutdown()
                if intent is not None and self.connected:
                    self._execute(intent)
                elif self.connected:
                    self._housekeeping()
        finally:
            self.closed.set()

    def _connect(self, simulate):
        try:
            config = self.config
            if self.route == "guard":
                where = "simulated ESP32 guard" if simulate else f"ESP32 guard {config.esp32_host}:{config.esp32_port}"
                self.log("info", f"Connecting to {where} …")
                if simulate:
                    self.sim = GuardSimulator(config, time_scale=self.sim_scale).start()
                    self.link = GuardLink(config, self.log, *self.sim.address)
                else:
                    self.link = GuardLink(config, self.log)
            else:
                if simulate:
                    self.sim = Simulator(config, time_scale=self.sim_scale).start()
                    config = self.sim.config
                self.link = self.link_factory(config, self.log)
                try:
                    self.state = StateListener(config, self.log)
                except OSError as exc:
                    self.state = None
                    self.log("warn", f"UDP {config.state_port} telemetry port unavailable (display only): {exc}")
                self.log("info", f"Connecting to {'local simulator' if simulate else config.drone_ip + ':' + str(config.command_port)} …")
            self.link.request("command", self.mode, timeout_for("command"))
            self.battery = int(self.link.request("battery?", self.mode, 4.0))
            for query in ("sdk?", "hardware?"):
                try:
                    self.info[query] = self.link.request(query, self.mode, 3.0)
                except SDKError as exc:
                    self.log("warn", f"{query} no reply: {exc}")
                    if self.link.uncertain:
                        self.link.resync(self.mode)
            self.simulated = simulate
            self.connected = True
            self.log("ok", f"SDK mode entered · battery {self.battery}% · {self.info.get('hardware?', 'TT')}")
        except Exception as exc:
            hint = ("this PC is on the TT-ToF-Demo Wi-Fi and the guard firmware is flashed" if self.route == "guard"
                    else "this PC is on the RMTT-/TELLO- Wi-Fi and no other program uses port 8889")
            self.log("error", f"Connection failed: {exc}. Make sure {hint}.")
            self._release()
        finally:
            self.connecting = False

    def _release(self):
        for item in (self.link, self.state, self.sim):
            if item is not None:
                try:
                    item.close()
                except Exception:
                    pass
        self.link = self.state = self.sim = None
        self.connected = self.simulated = False
        self.airborne = self.motors_on = False
        self.busy = None
        self.speed_set = None

    def _shutdown(self):
        if self.connected:
            if self.airborne:
                self.log("warn", "Landing automatically before disconnect…")
                self._execute(sdk.make("land", source="system"))
            if self.motors_on:
                self._execute(sdk.make("motoroff", source="system"))
            if self.airborne:
                self.log("error", "Landing NOT confirmed! Watch the drone now.")
            self.log("info", "Disconnected")
        self._release()

    def _housekeeping(self):
        link = self.link
        if time.monotonic() - link.last_tx < self.config.keepalive_sec:
            return
        try:
            # battery? doubles as keep-alive: the SDK auto-lands after 15 s of silence.
            value = link.resync(self.mode) if link.uncertain else int(link.request("battery?", self.mode, 3.0))
            self.battery = value
        except SDKError as exc:
            self.log("warn", f"Keep-alive / battery query failed: {exc}")
            return
        if self.airborne and self.battery <= self.config.auto_land_battery:
            self.log("error", f"Battery {self.battery}% ≤ {self.config.auto_land_battery}%: auto-landing")
            self._execute(sdk.make("land", source="low-battery guard"))
        elif self.motors_on and self.battery < self.config.motor_min_battery:
            self.log("error", f"Battery {self.battery}% too low: stopping motors")
            self._execute(sdk.make("motoroff", source="low-battery guard"))

    def _send(self, command, timeout=None):
        return self.link.request(command, self.mode, timeout or timeout_for(command, self.speed_set))

    def _execute(self, intent):
        self.busy = intent.sdk
        try:
            self._execute_inner(intent)
        finally:
            self.busy = None

    def _execute_inner(self, intent):
        link, action = self.link, intent.action
        try:
            if link.uncertain:
                self.log("warn", "Previous result unknown - re-aligning the reply stream first…")
                try:
                    self.battery = link.resync(self.mode)
                except SDKError:
                    if action not in {"land", "stop", "motoroff"}:
                        raise
                    self.log("warn", f"Re-align failed; sending safety command {intent.sdk} anyway")
            if action == "read":
                reply = self._send(intent.sdk)
                if intent.sdk == "battery?":
                    self.battery = int(reply)
                else:
                    self.info[intent.sdk] = reply
                self.log("ok", f"{intent.label}：{reply}")
                return
            if action == "takeoff":
                if self.airborne:
                    raise SDKError("Already airborne")
                self.battery = int(self._send("battery?"))
                if self.battery < self.config.takeoff_min_battery:
                    raise SDKError(f"Battery {self.battery}% is below the takeoff minimum of {self.config.takeoff_min_battery}%")
                if self.speed_set != self.config.flight_speed_cm_s:
                    self._send(f"speed {self.config.flight_speed_cm_s}")
                    self.speed_set = self.config.flight_speed_cm_s
            if action in {"move", "turn"} and not self.airborne:
                raise SDKError("Not airborne yet - say take off first")
            if action == "motoron" and self.battery is not None and self.battery < self.config.motor_min_battery:
                raise SDKError(f"Battery {self.battery}% is below the motor-test minimum of {self.config.motor_min_battery}%")

            self.log("info", f"Executing: {intent.label} ({intent.source})")
            try:
                self._send(intent.sdk)
            except SDKTimeout:
                if action == "takeoff":
                    self.airborne = True     # conservative: assume it may be flying
                    raise SDKError("Takeoff reply timed out: treating the drone as possibly airborne - say land or press Land")
                if action in {"move", "turn"}:
                    raise SDKError(f"{intent.sdk} timed out: drone is still airborne - say hover / land")
                if action == "motoroff":
                    raise SDKError("motoroff not confirmed: watch the motors and power off the drone if needed")
                raise
            except SDKInterrupted as exc:
                if action == "takeoff":
                    self.airborne = True
                reason = str(exc) if "(" in str(exc) else f"{intent.sdk} interrupted by stop"
                self.log("warn", f"{reason} - drone should be hovering")
                return
            if action == "takeoff":
                self.airborne, self.motors_on = True, False      # SDK: motoron ends on takeoff
            elif action == "land":
                self.airborne = False
            elif action == "motoron":
                self.motors_on = True
            elif action == "motoroff":
                self.motors_on = False
            elif action == "speed":
                self.speed_set = int(intent.sdk.split()[1])
            self.log("ok", f"Done: {intent.label}")
        except SDKError as exc:
            self.log("error", str(exc))
