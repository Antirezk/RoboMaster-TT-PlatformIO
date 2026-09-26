from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any

import yaml

from control.drone_controller import DroneController
from control.esp32_serial_controller import Esp32SerialController
from control.esp32_udp_controller import Esp32UdpController
from control.motion_controller import MotionController
from core.exceptions import SafetyError
from llm.llm_client import OllamaLLMClient, OpenAICompatibleLLMClient
from llm.task_parser import TaskParser
from models.task import Task, TaskType
from perception.mission_pad_tracker import MissionPadTracker
from planner.mission_planner import MissionPlanner
from planner.state_machine import MissionState
from simulation.mock_drone import MockDrone
from telemetry.logger import configure_logging
from telemetry.telemetry_manager import TelemetryManager


class DroneSystem:
    def __init__(self, mock: bool = False, dry_run: bool = False, direct_wifi: bool = False,
                 config_path: str | Path | None = None, verbose: bool = True) -> None:
        self.base_dir = Path(__file__).resolve().parents[1]
        path = Path(config_path) if config_path else self.base_dir / "config.yaml"
        with path.open("r", encoding="utf-8") as file:
            self.config: dict[str, Any] = yaml.safe_load(file)
        self.mock, self.dry_run, self.direct_wifi = mock, dry_run, direct_wifi
        if mock:
            self.config = copy.deepcopy(self.config)
            self.config["mission"]["takeoff_settle_sec"] = 0.2
        self.logger = configure_logging(self.base_dir / "logs", verbose)
        self.telemetry = TelemetryManager(self.logger)
        transport = self.config.get("transport", {})
        if mock:
            self.drone = MockDrone()
        elif direct_wifi or transport.get("mode") == "direct_wifi":
            self.drone = DroneController(dry_run=dry_run)
        elif transport.get("mode", "esp32_udp") == "esp32_udp":
            self.drone = Esp32UdpController(
                host=transport.get("udp_host", "192.168.4.1"),
                port=transport.get("udp_port", 8889), dry_run=dry_run,
                connect_timeout_sec=transport.get("connect_timeout_sec", 15.0),
                telemetry_timeout_sec=transport.get("telemetry_timeout_sec", 0.6),
            )
        else:
            self.drone = Esp32SerialController(
                port=transport.get("serial_port", "COM8"),
                baudrate=transport.get("baudrate", 115200), dry_run=dry_run,
                connect_timeout_sec=transport.get("connect_timeout_sec", 15.0),
                telemetry_timeout_sec=transport.get("telemetry_timeout_sec", 0.6),
            )
        pad_cfg = self.config["mission_pad"]
        self.tracker = MissionPadTracker(
            self.drone, invert_x=pad_cfg["invert_x"], invert_y=pad_cfg["invert_y"]
        )
        ctrl_cfg = self.config["control"]
        self.motion = MotionController(
            self.drone, ctrl_cfg["max_xy_speed"], ctrl_cfg["max_vertical_speed"], self.telemetry
        )
        llm_cfg = self.config.get("llm", {})
        client = None
        if llm_cfg.get("enabled", False):
            provider = llm_cfg.get("provider", "ollama")
            if provider == "ollama":
                client = OllamaLLMClient(
                    model=llm_cfg["model"],
                    base_url=llm_cfg.get("base_url", "http://127.0.0.1:11434"),
                    timeout_sec=llm_cfg.get("timeout_sec", 90.0),
                    keep_alive=llm_cfg.get("keep_alive", "30m"),
                    think=llm_cfg.get("think", False),
                )
            elif provider == "openai_compatible":
                client = OpenAICompatibleLLMClient(
                    model=llm_cfg["model"], base_url=llm_cfg["base_url"],
                    timeout_sec=llm_cfg.get("timeout_sec", 60.0),
                )
            else:
                raise ValueError(f"unsupported llm.provider: {provider}")
        self.llm_client = client
        self.parser = TaskParser(client)
        self.planner = MissionPlanner(self.drone, self.tracker, self.motion, self.config, self.logger)
        self.active_mission: Any | None = None
        self._mission_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.drone.connect()
        self.drone.enable_mission_pads()
        self.drone.set_mission_pad_direction(self.config["drone"]["mission_pad_direction"])
        if self.mock:
            mode = "MOCK"
        elif self.direct_wifi or self.config.get("transport", {}).get("mode") == "direct_wifi":
            mode = "DIRECT-WIFI-DRY-RUN" if self.dry_run else "DIRECT-WIFI"
        else:
            transport_name = self.config.get("transport", {}).get("mode", "esp32_udp").upper()
            mode = f"{transport_name}-DRY-RUN" if self.dry_run else transport_name
        self.logger.info("SYSTEM READY mode=%s battery=%s%%", mode, self.drone.get_battery())
        if self.llm_client is not None:
            healthy, detail = self.llm_client.health_check()
            log = self.logger.info if healthy else self.logger.warning
            log("LOCAL LLM: %s", detail)
        if "DIRECT-WIFI" in mode:
            self.logger.warning("DIRECT-WIFI bypasses the ESP32 ToF safety arbiter")

    def execute_command(self, command: str) -> Task:
        task = self.parse_command(command)
        self.execute_task(task)
        return task

    def parse_command(self, command: str) -> Task:
        """Turn user text into a validated task without executing it."""
        self.telemetry.command(command)
        task = self.parser.parse(command)
        self.telemetry.task(task, self.parser.last_source)
        return task

    def execute_task(self, task: Task) -> None:
        """Execute a task that has already passed parser validation."""
        if task.type in {TaskType.GOTO_MISSION_PAD, TaskType.SEARCH_MISSION_PAD}:
            self._start_mission(task)
        elif task.type == TaskType.TAKEOFF:
            self._ensure_no_active_mission()
            minimum = self.config["drone"]["minimum_takeoff_battery"]
            battery = self.drone.get_battery()
            if battery < minimum:
                raise SafetyError(f"battery {battery}% is below takeoff minimum {minimum}%")
            self.drone.takeoff()
            self.logger.info("TAKEOFF")
        elif task.type == TaskType.LAND:
            self._abort_active_mission()
            if self.drone.state.flying:
                self.drone.land()
            self.logger.info("LAND")
        elif task.type == TaskType.STOP:
            self._abort_active_mission()
            self.drone.stop()
            self.logger.warning("STOP: zero RC requested")
        elif task.type == TaskType.BATTERY:
            self.logger.info("Battery: %s%%", self.drone.get_battery())
        elif task.type == TaskType.STATUS:
            state = self.drone.state
            mission = self.active_mission.state.value if self.active_mission else "NONE"
            self.logger.info(
                "STATUS connected=%s flying=%s pads=%s battery=%s%% mission=%s safety=%s "
                "override=%s firmware=%s dry_run=%s",
                state.connected, state.flying, state.mission_pads_enabled,
                self.drone.get_battery(), mission, state.safety_status,
                state.safety_intervening, state.firmware_state, self.dry_run,
            )
            transport_telemetry = getattr(self.drone, "telemetry", None)
            if transport_telemetry is not None and transport_telemetry.directions:
                self.logger.info(
                    "TOF front=%s back=%s left=%s right=%s",
                    transport_telemetry.directions.get("f"),
                    transport_telemetry.directions.get("b"),
                    transport_telemetry.directions.get("l"),
                    transport_telemetry.directions.get("r"),
                )
        elif task.type == TaskType.PAD_TELEMETRY:
            observation = self.tracker.observe()
            self.logger.info(
                "Mission Pad: detected=%s mid=%s x=%s y=%s z=%s",
                observation.detected, observation.pad_id, observation.x, observation.y, observation.z,
            )

    def _execute_task(self, task: Task) -> None:
        """Compatibility shim for callers from earlier versions."""
        self.execute_task(task)

    def _start_mission(self, task: Task) -> None:
        self._ensure_no_active_mission()
        mission = self.planner.create_mission(task)
        with self._lock:
            self.active_mission = mission
        self._mission_thread = threading.Thread(
            target=self._run_mission, args=(mission,), name="mission-control-loop", daemon=True
        )
        self._mission_thread.start()

    def _run_mission(self, mission: Any) -> None:
        try:
            result = mission.run()
            if result == MissionState.COMPLETED:
                self.logger.info("MISSION COMPLETED")
        finally:
            with self._lock:
                if self.active_mission is mission:
                    self.active_mission = None

    def wait_for_mission(self, timeout: float | None = None) -> MissionState | None:
        thread = self._mission_thread
        mission = self.active_mission
        if thread:
            thread.join(timeout)
        return mission.state if mission is not None else None

    def _ensure_no_active_mission(self) -> None:
        if self.active_mission is not None and not self.active_mission.finished:
            raise SafetyError("another mission is active; use stop or land first")

    def _abort_active_mission(self) -> None:
        if self.active_mission is not None and not self.active_mission.finished:
            self.active_mission.abort()

    def shutdown(self) -> None:
        self._abort_active_mission()
        if self._mission_thread and self._mission_thread.is_alive():
            self._mission_thread.join(timeout=3)
        try:
            self.drone.stop()
            if self.drone.state.flying and not self.dry_run:
                self.drone.land()
        except Exception as exc:
            self.logger.error("shutdown safety action failed: %s", exc)
        try:
            self.drone.disable_mission_pads()
        finally:
            self.drone.disconnect()
            self.logger.info("SYSTEM SHUTDOWN")
