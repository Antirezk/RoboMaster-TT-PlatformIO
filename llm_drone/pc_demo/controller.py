import threading
import time

from .network import LOG, SDKError
from .console import describe_intent


class Controller:
    def __init__(self, config, commands, state, dry_run=False):
        self.config, self.commands, self.state = config, commands, state
        self.dry_run = dry_run
        self.flight_state = "grounded"
        self.stop_event = threading.Event()
        self.keepalive = threading.Thread(target=self._heartbeat, name="tt-keepalive", daemon=True)

    def start(self):
        self.commands.send("command")
        self.commands.send("mon")
        self.commands.send("mdirection 0")
        self.keepalive.start()
        LOG.info("[连接就绪 / READY] SDK 和 Pad 检测已开启；输入英文，或按 Enter 录音")

    def _heartbeat(self):
        while not self.stop_event.wait(0.2):
            if not self.commands.lock.acquire(blocking=False):
                continue
            try:
                if time.monotonic() - self.commands.last_send >= self.config.keepalive_sec:
                    # Re-entering SDK mode is a non-flight keepalive, through the same lock.
                    self.commands.send("command")
            except Exception as exc:
                LOG.error("KEEPALIVE failed: %s; exit and verify drone state", exc)
                return
            finally:
                self.commands.lock.release()

    def execute(self, intent):
        LOG.debug("PARSE action=%s sdk=%r pad=%s", intent.action, intent.sdk, intent.pad_id)
        LOG.info("%s", describe_intent(intent, self.config))
        if intent.action == "status":
            LOG.info("STATUS flight=%s telemetry=%s", self.flight_state, self.state.fresh())
            return
        if intent.action == "pad_land":
            if self.dry_run:
                LOG.info("[未发送] dry-run 模式：仅展示 Pad 指令，不执行移动或降落")
                raise SDKError("dry-run blocks Mission Pad motion/landing")
            self._require_airborne()
            self._land_on_pad(intent.pad_id)
            return
        command = intent.sdk
        if command == "battery?":
            self.commands.send(command)
            return
        if self.dry_run:
            LOG.info("[未发送] %s — dry-run 模式已拦截飞行指令", command)
            raise SDKError(f"dry-run blocks flight command: {command}")
        if command == "takeoff":
            if self.flight_state != "grounded":
                raise SDKError("takeoff requires known grounded state")
            values = self.state.fresh()
            if not self.config.minimum_battery <= values.get("bat", -1) <= 100:
                raise SDKError("takeoff refused: battery missing/low/invalid")
            # Mark before sending: an ACK timeout does not prove the drone stayed down.
            self.flight_state = "unknown"
            self.commands.send(command, flight=True)
            self.flight_state = "airborne"
        elif command == "land":
            self.flight_state = "unknown"
            self.commands.send(command, flight=True)
            self.flight_state = "grounded"
        elif command == "stop":
            self.commands.send(command)
        else:
            self._require_airborne()
            self.state.fresh()
            self.commands.send(command, flight=True)
        LOG.info("DONE command=%s flight=%s", command, self.flight_state)

    def _require_airborne(self):
        if self.flight_state != "airborne":
            raise SDKError("first take off successfully in this session")

    def _pad(self, pad_id, snapshot=None):
        values, stamp, _ = snapshot or self.state.snapshot()
        if not stamp or time.monotonic() - stamp > self.config.state_max_age_sec:
            raise SDKError("Mission Pad telemetry missing/stale")
        if values.get("mid") != pad_id:
            raise SDKError(f"target pad {pad_id} not currently visible (mid={values.get('mid')}); no blind search")
        if not all(key in values for key in ("x", "y", "z")) or not 0 < values["z"] <= 500:
            raise SDKError("invalid Mission Pad coordinates")
        if abs(values["x"]) > 500 or abs(values["y"]) > 500:
            raise SDKError("Mission Pad coordinates out of range")
        return values

    def _land_on_pad(self, pad_id):
        self._pad(pad_id)
        cfg = self.config
        command = f"go 0 0 {cfg.pad_height_cm} {cfg.pad_speed_cm_s} m{pad_id}"
        LOG.info("PAD ALIGN target=%s -> %s", pad_id, command)
        self.commands.send(command, flight=True)
        # Require frames received after the go ACK; do not reuse one frame N times.
        _, _, last_sequence = self.state.snapshot()
        stable = 0
        deadline = time.monotonic() + cfg.pad_verify_timeout_sec
        LOG.info("PAD VERIFY waiting for %s fresh aligned frames", cfg.pad_stable_frames)
        while time.monotonic() < deadline:
            with self.state.condition:
                self.state.condition.wait(timeout=0.1)
            snapshot = self.state.snapshot()
            values = self._pad(pad_id, snapshot)
            _, _, sequence = snapshot
            if sequence == last_sequence:
                continue
            last_sequence = sequence
            aligned = (abs(values["x"]) <= cfg.pad_tolerance_cm
                       and abs(values["y"]) <= cfg.pad_tolerance_cm
                       and abs(values["z"] - cfg.pad_height_cm) <= cfg.pad_tolerance_cm)
            stable = stable + 1 if aligned else 0
            if stable >= cfg.pad_stable_frames:
                LOG.info("PAD aligned mid=%s x=%s y=%s z=%s; sending land",
                         pad_id, values["x"], values["y"], values["z"])
                self.flight_state = "unknown"
                self.commands.send("land", flight=True)
                self.flight_state = "grounded"
                LOG.info("DONE pad landing acknowledged")
                return
        raise SDKError("pad alignment not confirmed; no land sent. Hovering: use land or stop")

    def close(self):
        self.stop_event.set()
        if self.keepalive.is_alive():
            self.keepalive.join(timeout=self.config.command_timeout_sec + 1)
        if not self.dry_run and self.flight_state != "grounded":
            try:
                if self.commands.poisoned:
                    self.commands.unconfirmed_land()
                else:
                    self.commands.send("land", flight=True)
                    self.flight_state = "grounded"
            except Exception as exc:
                LOG.error("EXIT landing not confirmed: %s; use manual control", exc)
        self.commands.close()
        self.state.close()
