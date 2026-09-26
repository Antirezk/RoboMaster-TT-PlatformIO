from __future__ import annotations

from collections import deque

from control.motion_controller import MotionController
from models.mission_pad import MissionPadObservation
from models.task import Task, TaskType
from perception.mission_pad_tracker import MissionPadTracker
from planner.goto_mission_pad import GotoMissionPadMission
from planner.mission_planner import MissionPlanner
from planner.state_machine import MissionState
from simulation.mock_drone import MockDrone
from telemetry.logger import NullLogger
from tests.conftest import FakeClock


class ScriptedTracker:
    def __init__(self, observations):
        self.items = deque(observations)
        self.last = observations[-1]

    def observe(self):
        if self.items:
            self.last = self.items.popleft()
        return self.last


def obs(mid, x=0, y=0, z=80, timestamp=0):
    if mid < 1:
        return MissionPadObservation.missing(timestamp)
    return MissionPadObservation(True, mid, x, y, z, timestamp)


def make_mission(config, observations, task=None):
    clock = FakeClock()
    drone = MockDrone()
    drone.connect()
    drone.enable_mission_pads()
    motion = MotionController(drone, 25, 20)
    mission = GotoMissionPadMission(
        task or Task(TaskType.GOTO_MISSION_PAD, 1, "land"), drone,
        ScriptedTracker(observations), motion, config, NullLogger(), clock, clock.sleep,
    )
    return mission, drone, clock


def advance_to_search(mission, clock):
    mission.tick()  # IDLE -> PRECHECK -> TAKEOFF
    clock.sleep(0.11)
    mission.tick()  # TAKEOFF -> SEARCH
    assert mission.state == MissionState.SEARCH


def test_planner_routes_goto_task(config):
    drone = MockDrone(); tracker = MissionPadTracker(drone); motion = MotionController(drone)
    planner = MissionPlanner(drone, tracker, motion, config, NullLogger())
    assert isinstance(planner.create_mission(Task(TaskType.GOTO_MISSION_PAD, 1)), GotoMissionPadMission)


def test_search_ignores_wrong_pad_then_aligns_on_target(config):
    mission, _, clock = make_mission(config, [obs(3), obs(1, 20, -10)])
    advance_to_search(mission, clock)
    mission.tick()
    assert mission.state == MissionState.SEARCH
    mission.tick()
    assert mission.state == MissionState.ALIGN


def test_align_requires_stable_frames(config):
    mission, _, clock = make_mission(config, [obs(1), obs(1, 2, 2), obs(1, 2, 2), obs(1, 2, 2)])
    advance_to_search(mission, clock)
    mission.tick()
    for expected in (1, 2):
        mission.tick()
        assert mission.stable_count == expected
        assert mission.state == MissionState.ALIGN
    mission.tick()
    assert mission.state == MissionState.DESCEND


def test_brief_loss_hovers_then_returns_to_search(config):
    mission, _, clock = make_mission(config, [obs(1), obs(-1), obs(-1)])
    advance_to_search(mission, clock)
    mission.tick()
    mission.tick()
    assert mission.state == MissionState.ALIGN
    assert mission.motion.last_command.is_zero
    clock.sleep(0.11)
    mission.tick()
    assert mission.state == MissionState.SEARCH


def test_search_timeout_fails_and_safely_lands(config):
    mission, drone, clock = make_mission(config, [obs(-1)])
    advance_to_search(mission, clock)
    clock.sleep(1.01)
    mission.tick()
    assert mission.state == MissionState.FAILED
    assert not drone.state.flying
    assert "land" in drone.events


def test_mock_mission_completes_end_to_end(config):
    config["mission"]["search_timeout_sec"] = 2
    config["mission"]["total_timeout_sec"] = 10
    clock = FakeClock()
    drone = MockDrone()
    drone.connect(); drone.enable_mission_pads()
    tracker = MissionPadTracker(drone, clock=clock)
    motion = MotionController(drone)
    mission = GotoMissionPadMission(
        Task(TaskType.GOTO_MISSION_PAD, 1, "land"), drone, tracker, motion,
        config, NullLogger(), clock, clock.sleep,
    )
    assert mission.run() == MissionState.COMPLETED
    assert "takeoff" in drone.events and "land" in drone.events
    states = [new for _, new in mission.history]
    assert MissionState.SEARCH in states
    assert MissionState.ALIGN in states
    assert MissionState.DESCEND in states
    assert states[-1] == MissionState.COMPLETED


def test_esp32_safety_pauses_pid_then_reacquires(config):
    mission, drone, clock = make_mission(config, [obs(1), obs(1, 20, -10)])
    advance_to_search(mission, clock)
    mission.tick()
    assert mission.state == MissionState.ALIGN
    drone.get_safety_status = lambda: "ESCAPE"
    mission.tick()
    assert mission.state == MissionState.ALIGN
    assert mission.motion.last_command.is_zero
    drone.get_safety_status = lambda: "NORMAL"
    mission.tick()
    assert mission.state == MissionState.SEARCH


def test_persistent_esp32_safety_fails_and_lands(config):
    config["safety"]["intervention_timeout_sec"] = 0.1
    mission, drone, clock = make_mission(config, [obs(1)])
    advance_to_search(mission, clock)
    drone.get_safety_status = lambda: "BLOCKED"
    mission.tick()
    clock.sleep(0.11)
    mission.tick()
    assert mission.state == MissionState.FAILED
    assert not drone.state.flying
