from __future__ import annotations

import time
from typing import Any, Callable

from control.pid_controller import PIDController
from models.mission_pad import MissionPadObservation
from models.task import Task, TaskType
from planner.search_strategy import SearchStrategy, YawSearchStrategy
from planner.state_machine import MissionState, StateMachine


class GotoMissionPadMission(StateMachine):
    def __init__(self, task: Task, drone: Any, tracker: Any, motion: Any,
                 config: dict[str, Any], logger: Any,
                 clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep,
                 search_strategy: SearchStrategy | None = None) -> None:
        super().__init__(logger)
        if task.type not in {TaskType.GOTO_MISSION_PAD, TaskType.SEARCH_MISSION_PAD}:
            raise ValueError("GotoMissionPadMission requires a mission-pad task")
        self.task, self.drone, self.tracker, self.motion = task, drone, tracker, motion
        self.config, self.clock, self.sleeper = config, clock, sleeper
        self.search_strategy = search_strategy or YawSearchStrategy(config["search"]["yaw_speed"])
        control = config["control"]
        x_cfg, y_cfg = config["pid"]["x"], config["pid"]["y"]
        self.x_pid = PIDController(**x_cfg, output_limit=control["max_xy_speed"])
        self.y_pid = PIDController(**y_cfg, output_limit=control["max_xy_speed"])
        self.started_at: float | None = None
        self.entered_at: float | None = None
        self.search_started_at: float | None = None
        self.lost_since: float | None = None
        self.stable_count = 0
        self.recoveries = 0
        self.error: str | None = None
        self.last_observation = MissionPadObservation.missing()
        self._abort_requested = False
        self._last_reported_mid: int | None = None
        self.safety_since: float | None = None
        self._last_safety_status = "NORMAL"

    def start(self) -> None:
        if self.state != MissionState.IDLE:
            raise RuntimeError("mission already started")
        now = self.clock()
        self.started_at = self.entered_at = now
        self.transition(MissionState.PRECHECK)

    def transition(self, new_state: MissionState) -> None:
        super().transition(new_state)
        self.entered_at = self.clock()
        if new_state == MissionState.SEARCH:
            self.search_started_at = self.entered_at
            self.search_strategy.reset()
            self.x_pid.reset()
            self.y_pid.reset()
            self.stable_count = 0

    def tick(self) -> MissionState:
        if self.state == MissionState.IDLE:
            self.start()
        if self.finished:
            return self.state
        if self._abort_requested:
            self._fail("mission stopped by user")
            return self.state
        assert self.started_at is not None
        if self.clock() - self.started_at > self.config["mission"]["total_timeout_sec"]:
            self._fail("total mission timeout")
            return self.state
        try:
            if self.state in {MissionState.SEARCH, MissionState.ALIGN, MissionState.DESCEND}:
                if self._safety_interlock_active():
                    return self.state
            handler = getattr(self, f"_tick_{self.state.value.lower()}")
            handler()
        except Exception as exc:
            self.logger.exception("MISSION ERROR: %s", exc)
            self._fail(str(exc))
        return self.state

    def abort(self) -> None:
        self._abort_requested = True

    def run(self) -> MissionState:
        period = 1.0 / self.config["control"]["loop_hz"]
        self.start()
        while not self.finished:
            before = self.clock()
            self.tick()
            remaining = period - (self.clock() - before)
            if remaining > 0 and not self.finished:
                self.sleeper(remaining)
        return self.state

    def _tick_precheck(self) -> None:
        if not self.drone.state.connected:
            raise RuntimeError("PRECHECK failed: drone is not connected")
        battery = self.drone.get_battery()
        self.logger.info("Battery: %s%%", battery)
        minimum = self.config["drone"]["minimum_takeoff_battery"]
        if battery < minimum:
            raise RuntimeError(f"PRECHECK failed: battery {battery}% is below {minimum}%")
        if not self.drone.state.mission_pads_enabled:
            raise RuntimeError("PRECHECK failed: Mission Pad system is not enabled")
        safety_status = self.drone.get_safety_status()
        if safety_status != "NORMAL":
            raise RuntimeError(f"PRECHECK failed: ESP32 ToF safety is {safety_status}")
        if not self.drone.state.flying:
            self.drone.takeoff()
            self.logger.info("TAKEOFF")
            self.transition(MissionState.TAKEOFF)
        else:
            self.transition(MissionState.SEARCH)

    def _tick_takeoff(self) -> None:
        assert self.entered_at is not None
        self.motion.hover()
        if self.clock() - self.entered_at >= self.config["mission"]["takeoff_settle_sec"]:
            self.transition(MissionState.SEARCH)

    def _tick_search(self) -> None:
        assert self.search_started_at is not None
        elapsed = self.clock() - self.search_started_at
        if elapsed > self.config["mission"]["search_timeout_sec"]:
            self._fail("Mission Pad search timeout")
            return
        observation = self._observe()
        if observation.detected and observation.pad_id == self.task.pad_id:
            self.motion.hover()
            self.logger.info("TARGET PAD %s FOUND", self.task.pad_id)
            self.lost_since = None
            if self.task.type == TaskType.SEARCH_MISSION_PAD:
                self.transition(MissionState.COMPLETED)
            else:
                self.logger.info("ALIGNING...")
                self.transition(MissionState.ALIGN)
            return
        command = self.search_strategy.command(elapsed)
        self.motion.send_velocity(
            command.left_right, command.forward_backward, command.up_down, command.yaw
        )

    def _tick_align(self) -> None:
        observation = self._observe()
        if not self._target_visible(observation):
            self._handle_lost_pad()
            return
        self.lost_since = None
        self._send_horizontal(observation, up_down=0)
        tolerance = self.config["mission_pad"]["alignment_tolerance_cm"]
        if abs(observation.x or 0) <= tolerance and abs(observation.y or 0) <= tolerance:
            self.stable_count += 1
        else:
            self.stable_count = 0
        if self.stable_count >= self.config["mission_pad"]["stable_frames"]:
            if self.task.final_action == "land":
                self.logger.info("DESCENDING...")
                self.transition(MissionState.DESCEND)
            else:
                self.motion.hover()
                self.transition(MissionState.COMPLETED)

    def _tick_descend(self) -> None:
        observation = self._observe()
        if not self._target_visible(observation):
            self._handle_lost_pad()
            return
        self.lost_since = None
        assert observation.z is not None
        if observation.z <= self.config["mission_pad"]["landing_trigger_height_cm"]:
            self.motion.hover()
            self.transition(MissionState.LAND)
            return
        self._send_horizontal(observation, -self.config["control"]["max_vertical_speed"])

    def _tick_land(self) -> None:
        self.motion.hover()
        if self.drone.state.flying:
            self.drone.land()
        self.logger.info("LAND")
        self.transition(MissionState.COMPLETED)

    def _observe(self) -> MissionPadObservation:
        observation = self.tracker.observe()
        self.last_observation = observation
        if not observation.detected:
            log = self.logger.info if self._last_reported_mid != -1 else self.logger.debug
            log("PAD mid=-1")
        elif observation.pad_id != self.task.pad_id:
            log = self.logger.info if self._last_reported_mid != observation.pad_id else self.logger.debug
            log("PAD mid=%s ignored", observation.pad_id)
        else:
            log = self.logger.info if self._last_reported_mid != observation.pad_id else self.logger.debug
            log("PAD mid=%s x=%s y=%s z=%s", observation.pad_id,
                observation.x, observation.y, observation.z)
        self._last_reported_mid = observation.pad_id
        return observation

    def _target_visible(self, observation: MissionPadObservation) -> bool:
        return observation.detected and observation.pad_id == self.task.pad_id

    def _send_horizontal(self, observation: MissionPadObservation, up_down: float) -> None:
        assert observation.x is not None and observation.y is not None
        # VERIFY_ON_HARDWARE: x->left/right and y->forward/back signs are config-calibrated.
        dt = 1.0 / self.config["control"]["loop_hz"]
        lr = self.x_pid.update(-observation.x, dt=dt)
        fb = self.y_pid.update(-observation.y, dt=dt)
        self.motion.send_velocity(lr, fb, up_down, 0)

    def _handle_lost_pad(self) -> None:
        now = self.clock()
        self.motion.hover()
        if self.lost_since is None:
            self.lost_since = now
            return
        if now - self.lost_since < self.config["mission_pad"]["lost_timeout_sec"]:
            return
        self.recoveries += 1
        self.lost_since = None
        if self.recoveries > self.config["mission"]["max_recoveries"]:
            self._fail("Mission Pad repeatedly lost")
        else:
            self.logger.warning("Target pad lost; recovery %s", self.recoveries)
            self.transition(MissionState.SEARCH)

    def _safety_interlock_active(self) -> bool:
        status = self.drone.get_safety_status()
        now = self.clock()
        if status == "NORMAL":
            if self.safety_since is not None:
                self.logger.info("ESP32 SAFETY recovered; reacquiring target pad")
                self.safety_since = None
                self._last_safety_status = status
                self.x_pid.reset()
                self.y_pid.reset()
                if self.state != MissionState.SEARCH:
                    self.transition(MissionState.SEARCH)
                return True
            return False

        self.motion.hover()
        self.x_pid.reset()
        self.y_pid.reset()
        self.stable_count = 0
        if self.safety_since is None:
            self.safety_since = now
        if status != self._last_safety_status:
            self.logger.warning("ESP32 SAFETY %s: high-level PID paused", status)
        self._last_safety_status = status
        elapsed = now - self.safety_since
        safety_cfg = self.config["safety"]
        timeout = (
            safety_cfg["sensor_fault_timeout_sec"]
            if status == "FAULT" else safety_cfg["intervention_timeout_sec"]
        )
        if elapsed >= timeout:
            self._fail(f"ESP32 safety remained {status} for {elapsed:.1f}s")
        return True

    def _fail(self, reason: str) -> None:
        self.error = reason
        self.logger.error("MISSION FAILED: %s", reason)
        try:
            self.motion.hover()
            if self.drone.state.flying:
                self.drone.land()
                self.logger.warning("Safety landing requested")
        finally:
            self.transition(MissionState.FAILED)
