from planner.state_machine import MissionState, StateMachine
from telemetry.logger import NullLogger


def test_state_transitions_are_recorded():
    machine = StateMachine(NullLogger())
    machine.transition(MissionState.PRECHECK)
    machine.transition(MissionState.TAKEOFF)
    assert machine.history == [
        (MissionState.IDLE, MissionState.PRECHECK),
        (MissionState.PRECHECK, MissionState.TAKEOFF),
    ]
